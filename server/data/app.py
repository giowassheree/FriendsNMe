import hashlib
import hmac
import mimetypes
import os
import random
import re
import secrets
import smtplib

from datetime import timedelta, timezone, datetime
from email.message import EmailMessage

from flask import Flask, request, jsonify, send_from_directory, session

from models import db, User, VerificationCode, LocationShare, HelpRequest

from party import PartyTracker, FRESH_SECONDS

from calculations import CLUSTER_DISTANCE


# ==================================================
# FOLDERS
# ==================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Windows can map .js to text/plain in the registry, and browsers
# refuse to run a service worker (sw.js) served that way.
mimetypes.add_type("application/javascript", ".js")

FRONTEND_DIR = os.path.abspath(
    os.path.join(BASE_DIR, "..", "..", "Frontend")
)


# ==================================================
# FLASK APP
# ==================================================

app = Flask(__name__)

# Debug mode turns on the Werkzeug debugger and /api/debug/users.
# Only enable it on your own machine: FRIENDSNME_DEBUG=true
DEBUG_MODE = (
    os.environ.get("FRIENDSNME_DEBUG", "").lower()
    == "true"
)

# The secret signs login cookies. A hard-coded fallback would let
# anyone who has read this file forge a session, so when
# SESSION_SECRET is unset we use a random one instead. Sessions
# then reset whenever the server restarts.
app.secret_key = os.environ.get("SESSION_SECRET")

if not app.secret_key:

    app.secret_key = secrets.token_hex(32)

    print(
        "WARNING: SESSION_SECRET is not set. Using a random secret; "
        "everyone will be signed out when the server restarts.",
        flush=True
    )

app.permanent_session_lifetime = timedelta(days=30)


# ==================================================
# PARTY STATE
# ==================================================

# Lives in server memory (see party.py), so restarting
# app.py clears all parties.
party_tracker = PartyTracker()


# ==================================================
# DATABASE
# ==================================================

DATABASE_PATH = os.path.join(BASE_DIR, "friendsnme.db")

app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DATABASE_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

with app.app_context():

    db.create_all()

    print("\n================================")
    print("DATABASE STARTED")
    print("================================")

    print("Database:", DATABASE_PATH)

    users = User.query.all()

    print("Users currently stored:", len(users))

    # Emails and locations only go to the log in debug mode.
    for user in (users if DEBUG_MODE else []):

        print(
            "USER:",
            user.id,
            user.username,
            user.email,
            user.last_location
        )


# ==================================================
# AUTH SETTINGS
# ==================================================

CODE_TTL_MINUTES = 10
MAX_CODES_PER_HOUR = 5
MIN_RESEND_SECONDS = 60
MAX_CODE_ATTEMPTS = 5

TEMPLE_EMAIL_RE = re.compile(
    r"^[^\s@]+@temple\.edu$",
    re.IGNORECASE
)


# ==================================================
# HELPERS
# ==================================================

def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


def as_utc(value):

    # SQLite drops timezone information when reading
    # DateTime values. They were stored as UTC.
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value


def normalize_email(email):

    return str(email or "").strip().lower()


def normalize_username(username):

    return " ".join(
        str(username or "").strip().split()
    )


def is_temple_email(email):

    return bool(
        TEMPLE_EMAIL_RE.match(
            normalize_email(email)
        )
    )


def hmac_value(value):

    return hmac.new(
        app.secret_key.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def find_user_by_email(email):

    return User.query.filter_by(
        email=email
    ).first()


def find_user_by_id(user_id):

    return db.session.get(
        User,
        user_id
    )


def find_user_by_username(username):

    return User.query.filter_by(
        username_lower=username.lower()
    ).first()


def require_user():

    user_id = session.get("user_id")

    if not user_id:
        return None

    return find_user_by_id(user_id)


def json_error(message, status):

    return jsonify({
        "error": message
    }), status


# ==================================================
# EMAIL VERIFICATION
# ==================================================

def send_verification_email(email, code):

    should_log = (
        os.environ.get(
            "AUTH_LOG_VERIFICATION_CODES",
            ""
        ).lower() == "true"
    )

    is_production = (
        os.environ.get("FLASK_ENV")
        == "production"
    )

    # During development, print verification code
    # directly into terminal if SMTP is not configured.
    if (
        should_log
        or (
            not is_production
            and not os.environ.get("SMTP_HOST")
        )
    ):

        print(
            f"[AUTH] Verification code for {email}: {code}",
            flush=True
        )

        return

    smtp_host = os.environ.get("SMTP_HOST")
    smtp_from = os.environ.get("SMTP_FROM")

    if not smtp_host or not smtp_from:

        raise RuntimeError(
            "Email delivery is not configured."
        )

    smtp_port = int(
        os.environ.get(
            "SMTP_PORT",
            "587"
        )
    )

    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")

    smtp_secure = (
        os.environ.get(
            "SMTP_SECURE",
            ""
        ).lower() == "true"
    )

    message = EmailMessage()

    message["From"] = smtp_from
    message["To"] = email
    message["Subject"] = "Your FriendsNMe verification code"

    message.set_content(
        f"Your FriendsNMe verification code is {code}. "
        "It expires in 10 minutes."
    )

    if smtp_secure:

        server = smtplib.SMTP_SSL(
            smtp_host,
            smtp_port,
            timeout=20
        )

    else:

        server = smtplib.SMTP(
            smtp_host,
            smtp_port,
            timeout=20
        )

    with server:

        if not smtp_secure:
            server.starttls()

        if smtp_user and smtp_pass:

            server.login(
                smtp_user,
                smtp_pass
            )

        server.send_message(message)


def latest_usable_code(email):

    current = now_utc()

    return (
        VerificationCode.query
        .filter(
            VerificationCode.email == email,
            VerificationCode.used_at.is_(None),
            VerificationCode.expires_at > current,
        )
        .order_by(
            VerificationCode.created_at.desc()
        )
        .first()
    )


# ==================================================
# WEBSITE
# ==================================================

@app.route("/")
def home():

    return send_from_directory(
        FRONTEND_DIR,
        "index.html"
    )


# ==================================================
# AUTH SESSION
# ==================================================

@app.get("/api/auth/session")
def auth_session():

    user = require_user()

    if not user:

        return jsonify({
            "authenticated": False
        }), 401

    return jsonify({
        "authenticated": True,
        "user": user.to_public_dict()
    })


# ==================================================
# REQUEST VERIFICATION CODE
# ==================================================

@app.post("/api/auth/request-code")
def request_code():

    body = request.get_json(
        silent=True
    ) or {}

    email = normalize_email(
        body.get("email")
    )

    if not is_temple_email(email):

        return json_error(
            "Only Temple University email addresses can access this website.",
            400
        )

    current = now_utc()

    recent_codes = (
        VerificationCode.query
        .filter(
            VerificationCode.email == email,
            VerificationCode.created_at
            >= current - timedelta(hours=1),
        )
        .order_by(
            VerificationCode.created_at.desc()
        )
        .all()
    )

    if len(recent_codes) >= MAX_CODES_PER_HOUR:

        return json_error(
            "Too many verification codes requested. Try again later.",
            429
        )

    if recent_codes:

        latest_created = as_utc(
            recent_codes[0].created_at
        )

        if (
            current - latest_created
        ).total_seconds() < MIN_RESEND_SECONDS:

            return json_error(
                "Please wait before requesting another verification code.",
                429
            )

    code = str(
        random.SystemRandom().randint(
            100000,
            999999
        )
    )

    new_code = VerificationCode(
        email=email,

        code_hash=hmac_value(
            f"{email}:{code}"
        ),

        expires_at=(
            current
            + timedelta(
                minutes=CODE_TTL_MINUTES
            )
        ),
    )

    db.session.add(new_code)
    db.session.commit()

    try:

        send_verification_email(
            email,
            code
        )

    except RuntimeError as error:

        return json_error(
            str(error),
            503
        )

    return jsonify({
        "ok": True,
        "email": email,
        "expiresInSeconds":
            CODE_TTL_MINUTES * 60
    })


# ==================================================
# VERIFY CODE
# ==================================================

@app.post("/api/auth/verify-code")
def verify_code():

    body = request.get_json(
        silent=True
    ) or {}

    email = normalize_email(
        body.get("email")
    )

    code = str(
        body.get("code") or ""
    ).strip()

    if not is_temple_email(email):

        return json_error(
            "Only Temple University email addresses can access this website.",
            400
        )

    if not re.match(
        r"^\d{6}$",
        code
    ):

        return json_error(
            "Enter the 6-digit verification code.",
            400
        )

    code_entry = latest_usable_code(
        email
    )

    if (
        not code_entry
        or code_entry.attempts
        >= MAX_CODE_ATTEMPTS
    ):

        return json_error(
            "That verification code is invalid or expired.",
            400
        )

    submitted_hash = hmac_value(
        f"{email}:{code}"
    )

    if not secrets.compare_digest(
        submitted_hash,
        code_entry.code_hash
    ):

        code_entry.attempts += 1

        db.session.commit()

        return json_error(
            "That verification code is invalid or expired.",
            400
        )

    code_entry.used_at = now_utc()

    existing_user = find_user_by_email(
        email
    )

    db.session.commit()

    session.permanent = True

    # Existing account
    if existing_user:

        session["user_id"] = existing_user.id

        session.pop(
            "verified_email",
            None
        )

        print(
            "USER LOGGED IN:",
            existing_user.username
        )

        return jsonify({
            "ok": True,
            "needsUsername": False,
            "user":
                existing_user.to_public_dict()
        })

    # New account
    session["verified_email"] = email

    return jsonify({
        "ok": True,
        "needsUsername": True
    })


# ==================================================
# COMPLETE SIGNUP
# ==================================================

@app.post("/api/auth/complete-signup")
def complete_signup():

    email = session.get(
        "verified_email"
    )

    if not email:

        return json_error(
            "Your signup session expired. Verify your email again.",
            401
        )

    body = request.get_json(
        silent=True
    ) or {}

    username = normalize_username(
        body.get("username")
    )

    if not username:

        return json_error(
            "Username cannot be empty.",
            400
        )

    if len(username) > 32:

        return json_error(
            "Username must be 32 characters or fewer.",
            400
        )

    if find_user_by_email(email):

        return json_error(
            "An account already exists for this Temple email.",
            409
        )

    if find_user_by_username(username):

        return json_error(
            "That username is already taken.",
            409
        )

    # CREATE DATABASE USER
    user = User(
        email=email,
        username=username,
        username_lower=username.lower()
    )

    db.session.add(user)
    db.session.commit()

    print("\n================================")
    print("NEW USER SAVED TO DATABASE")
    print("================================")

    print("ID:", user.id)
    print("USERNAME:", user.username)
    print("EMAIL:", user.email)

    session.permanent = True
    session["user_id"] = user.id

    session.pop(
        "verified_email",
        None
    )

    return jsonify({
        "ok": True,
        "user": user.to_public_dict()
    }), 201


# ==================================================
# LOGOUT
# ==================================================

@app.post("/api/auth/logout")
def logout():

    session.clear()

    return jsonify({
        "ok": True
    })


# ==================================================
# LOCATION HELPERS
# ==================================================

def location_age_seconds(location, current):

    try:
        updated = datetime.fromisoformat(
            location["updatedAt"]
        )
    except (KeyError, TypeError, ValueError):
        return None

    return (current - as_utc(updated)).total_seconds()


def has_location(user):

    loc = user.last_location

    return (
        isinstance(loc, dict)
        and "latitude" in loc
        and "longitude" in loc
    )


def party_locations(current):

    # Everyone's latest location in the shape party.py wants.
    locations = []

    for database_user in User.query.all():

        if not has_location(database_user):
            continue

        age = location_age_seconds(
            database_user.last_location,
            current
        )

        if age is None:
            continue

        loc = database_user.last_location

        locations.append({
            "id": database_user.id,
            "latitude": float(loc["latitude"]),
            "longitude": float(loc["longitude"]),
            "accuracy": loc.get("accuracy"),
            "age_seconds": age,
        })

    return locations


# A help request stops alerting friends after this long, even if
# nobody taps "I'm OK".
HELP_REQUEST_HOURS = 2


def active_help_request(user_id, current):

    return (
        HelpRequest.query
        .filter(
            HelpRequest.user_id == user_id,
            HelpRequest.resolved_at.is_(None),
            HelpRequest.created_at
            >= current - timedelta(hours=HELP_REQUEST_HOURS),
        )
        .order_by(HelpRequest.created_at.desc())
        .first()
    )


def public_location(loc):

    return {
        "latitude": loc["latitude"],
        "longitude": loc["longitude"],
        "accuracy": loc.get("accuracy"),
    }


def map_payload(user):

    # Everything the map needs: my party and status, plus the
    # friends who chose to share their location with me.
    current = now_utc()

    party_tracker.expire(current)

    my_status = party_tracker.status_for(
        user.id,
        user.last_location if has_location(user) else None
    )

    my_party_id = (
        my_status["party"]["id"]
        if my_status["party"]
        else None
    )

    friends = []

    shared_with_me = (
        User.query
        .join(
            LocationShare,
            LocationShare.owner_id == User.id
        )
        .filter(LocationShare.viewer_id == user.id)
        .order_by(User.username_lower)
        .all()
    )

    for friend in shared_with_me:

        entry = {
            "id": friend.id,
            "username": friend.username,
            "location": None,
            "ageSeconds": None,
            "status": "NOT_IN_PARTY",
            "distanceFromParty": None,
            "sameParty": False,
            # They missed their "Are you OK?" check.
            "needsHelp": bool(
                active_help_request(friend.id, current)
            ),
        }

        if has_location(friend):

            loc = friend.last_location

            friend_status = party_tracker.status_for(
                friend.id,
                loc
            )

            entry["location"] = public_location(loc)
            entry["ageSeconds"] = location_age_seconds(
                loc,
                current
            )
            entry["status"] = friend_status["status"]
            entry["distanceFromParty"] = (
                friend_status["distanceFromParty"]
            )
            entry["sameParty"] = bool(
                my_party_id
                and friend_status["party"]
                and friend_status["party"]["id"] == my_party_id
            )

        friends.append(entry)

    return {
        "me": {
            "party": my_status["party"],
            "status": my_status["status"],
            "distanceFromParty":
                my_status["distanceFromParty"],
            # The party I left, if I can still rejoin it.
            "leftParty": my_status["leftParty"],
            # Used to draw routes when this phone isn't tracking.
            "location": (
                public_location(user.last_location)
                if has_location(user)
                else None
            ),
            "needsHelp": bool(
                active_help_request(user.id, current)
            ),
        },
        "friends": friends,
        "freshSeconds": FRESH_SECONDS,
        "clusterDistance": CLUSTER_DISTANCE,
    }


# ==================================================
# LOCATION
# ==================================================

@app.post("/location")
def location():

    data = request.get_json(
        silent=True
    ) or {}

    latitude = data.get("latitude")
    longitude = data.get("longitude")
    accuracy = data.get("accuracy")

    if latitude is None or longitude is None:

        return jsonify({
            "error": "Latitude and longitude are required"
        }), 400

    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return json_error(
            "Latitude and longitude must be numbers.",
            400
        )

    if not (
        -90 <= latitude <= 90
        and -180 <= longitude <= 180
    ):
        return json_error(
            "Latitude or longitude is out of range.",
            400
        )

    # Accuracy is optional; ignore anything that isn't a
    # sensible number of meters.
    try:
        accuracy = float(accuracy)
        if not 0 <= accuracy < 100000:
            accuracy = None
    except (TypeError, ValueError):
        accuracy = None

    user = require_user()

    if not user:

        return jsonify({
            "error":
                "You must be signed in before sending location."
        }), 401

    # --------------------------------------------------
    # SAVE THIS PHONE'S LATEST LOCATION
    # --------------------------------------------------

    user.last_location = {
        "latitude": latitude,
        "longitude": longitude,
        "accuracy": accuracy,
        "updatedAt": iso_now()
    }

    db.session.commit()

    print(
        "LOCATION:",
        user.username,
        round(latitude, 6),
        round(longitude, 6),
        "| accuracy:",
        accuracy,
        flush=True
    )

    # --------------------------------------------------
    # RECALCULATE PARTIES WITH EVERYONE'S LATEST LOCATION
    # --------------------------------------------------

    current = now_utc()

    party_tracker.update(
        party_locations(current),
        current
    )

    return jsonify(map_payload(user))


@app.get("/api/map")
def map_data():

    user = require_user()

    if not user:
        return json_error("You must be signed in.", 401)

    return jsonify(map_payload(user))


@app.post("/api/party/leave")
def leave_party():

    user = require_user()

    if not user:
        return json_error("You must be signed in.", 401)

    party_tracker.leave(user.id)

    return jsonify(map_payload(user))


@app.post("/api/party/rejoin")
def rejoin_party():

    user = require_user()

    if not user:
        return json_error("You must be signed in.", 401)

    if not party_tracker.rejoin(user.id):
        return json_error(
            "That party has ended, so there is nothing to rejoin.",
            409
        )

    return jsonify(map_payload(user))


# ==================================================
# HELP REQUESTS ("ARE YOU OK?" NOT ANSWERED)
# ==================================================

@app.post("/api/help")
def request_help():

    user = require_user()

    if not user:
        return json_error("You must be signed in.", 401)

    current = now_utc()

    if not active_help_request(user.id, current):
        db.session.add(HelpRequest(user_id=user.id))
        db.session.commit()

    # Everyone this user shares their location with is alerted;
    # they already see where the user is on their map.
    viewers = (
        User.query
        .join(
            LocationShare,
            LocationShare.viewer_id == User.id
        )
        .filter(LocationShare.owner_id == user.id)
        .order_by(User.username_lower)
        .all()
    )

    print(
        "HELP REQUESTED:",
        user.username,
        "| alerting:",
        [viewer.username for viewer in viewers],
        flush=True
    )

    return jsonify({
        "ok": True,
        "notified": [viewer.username for viewer in viewers],
    })


@app.post("/api/help/resolve")
def resolve_help():

    user = require_user()

    if not user:
        return json_error("You must be signed in.", 401)

    HelpRequest.query.filter(
        HelpRequest.user_id == user.id,
        HelpRequest.resolved_at.is_(None),
    ).update({"resolved_at": now_utc()})

    db.session.commit()

    return jsonify(map_payload(user))


# ==================================================
# LOCATION SHARING
# ==================================================

def share_entry(user, sharing_ids):

    return {
        "id": user.id,
        "username": user.username,
        "sharing": user.id in sharing_ids,
    }


def my_sharing_ids(user):

    return {
        share.viewer_id
        for share in LocationShare.query.filter_by(
            owner_id=user.id
        )
    }


@app.get("/api/users/search")
def search_users():

    user = require_user()

    if not user:
        return json_error("You must be signed in.", 401)

    query = str(request.args.get("q") or "").strip().lower()

    if len(query) < 2:
        return json_error(
            "Type at least 2 characters to search.",
            400
        )

    # Usernames match partially; emails only match exactly so
    # the search can't be used to list everyone's email.
    if "@" in query:
        condition = User.email == query
    else:
        escaped = (
            query
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        condition = User.username_lower.like(
            f"%{escaped}%",
            escape="\\"
        )

    matches = (
        User.query
        .filter(condition, User.id != user.id)
        .order_by(User.username_lower)
        .limit(10)
        .all()
    )

    sharing_ids = my_sharing_ids(user)

    return jsonify({
        "users": [
            share_entry(match, sharing_ids)
            for match in matches
        ]
    })


@app.get("/api/shares")
def list_shares():

    user = require_user()

    if not user:
        return json_error("You must be signed in.", 401)

    sharing_with = (
        User.query
        .join(
            LocationShare,
            LocationShare.viewer_id == User.id
        )
        .filter(LocationShare.owner_id == user.id)
        .order_by(User.username_lower)
        .all()
    )

    return jsonify({
        "sharingWith": [
            {"id": viewer.id, "username": viewer.username}
            for viewer in sharing_with
        ]
    })


@app.post("/api/shares")
def add_share():

    user = require_user()

    if not user:
        return json_error("You must be signed in.", 401)

    body = request.get_json(silent=True) or {}

    viewer = find_user_by_id(str(body.get("userId") or ""))

    if not viewer or viewer.id == user.id:
        return json_error("That account doesn't exist.", 404)

    if not LocationShare.query.filter_by(
        owner_id=user.id,
        viewer_id=viewer.id
    ).first():

        db.session.add(
            LocationShare(
                owner_id=user.id,
                viewer_id=viewer.id
            )
        )
        db.session.commit()

    return jsonify({
        "ok": True,
        "user": {"id": viewer.id, "username": viewer.username}
    }), 201


@app.delete("/api/shares/<viewer_id>")
def remove_share(viewer_id):

    user = require_user()

    if not user:
        return json_error("You must be signed in.", 401)

    LocationShare.query.filter_by(
        owner_id=user.id,
        viewer_id=viewer_id
    ).delete()

    db.session.commit()

    return jsonify({"ok": True})


# ==================================================
# GET MY LOCATION
# ==================================================

@app.get("/api/location/me")
def get_my_location():

    user = require_user()

    if not user:

        return json_error(
            "You must be signed in.",
            401
        )

    return jsonify({
        "ok": True,
        "user": user.to_public_dict(),
        "location": user.last_location
    })


# ==================================================
# DEBUG DATABASE
# ==================================================

@app.get("/api/debug/users")
def debug_users():

    # Lists every user's email and location, so it only
    # exists in debug mode and still requires a login.
    if not DEBUG_MODE:
        return json_error("Not found.", 404)

    if not require_user():
        return json_error("You must be signed in.", 401)

    users = User.query.all()

    stored_users = []

    for user in users:

        stored_users.append({
            "id":
                user.id,

            "email":
                user.email,

            "username":
                user.username,

            "last_location":
                user.last_location
        })

    print("\n================================")
    print("DATABASE USERS")
    print("================================")

    print(
        "Number of users:",
        len(stored_users)
    )

    for stored_user in stored_users:

        print(
            stored_user["username"],
            "|",
            stored_user["email"],
            "|",
            stored_user["last_location"]
        )

    return jsonify({
        "count": len(stored_users),
        "users": stored_users
    })


# ==================================================
# FRONTEND FILES
# Keep this AFTER the API routes
# ==================================================

@app.route("/<path:filename>")
def frontend_files(filename):

    return send_from_directory(
        FRONTEND_DIR,
        filename
    )


# ==================================================
# START SERVER
# ==================================================

if __name__ == "__main__":

    print(
        "Frontend folder:",
        FRONTEND_DIR
    )

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=DEBUG_MODE
    )