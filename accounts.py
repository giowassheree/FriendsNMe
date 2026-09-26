import hashlib
import hmac
import json
import os
import random
import re
import secrets
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, session


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "Frontend"
DEFAULT_DATA_DIR = Path("/tmp/friendsnme-data") if os.environ.get("VERCEL") else BASE_DIR / "server" / "data"
DATA_DIR = Path(os.environ.get("AUTH_DATA_DIR", DEFAULT_DATA_DIR))
DB_PATH = DATA_DIR / "auth-db.json"

CODE_TTL_MINUTES = 10
MAX_CODES_PER_HOUR = 5
MIN_RESEND_SECONDS = 60
MAX_CODE_ATTEMPTS = 5
TEMPLE_EMAIL_RE = re.compile(r"^[^\s@]+@temple\.edu$", re.IGNORECASE)

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
app.secret_key = os.environ.get("SESSION_SECRET", "development-only-secret-change-me")
app.permanent_session_lifetime = timedelta(days=30)

if os.environ.get("VERCEL"):
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )


def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


def parse_iso(value):
    return datetime.fromisoformat(value)


def load_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        data = {"users": [], "verificationCodes": []}
        save_db(data)
        return data

    with DB_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)

    data.setdefault("users", [])
    data.setdefault("verificationCodes", [])
    return data


def save_db(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = DB_PATH.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
    temp_path.replace(DB_PATH)


def normalize_email(email):
    return str(email or "").strip().lower()


def is_temple_email(email):
    return bool(TEMPLE_EMAIL_RE.match(normalize_email(email)))


def normalize_username(username):
    return " ".join(str(username or "").strip().split())


def hmac_value(value):
    return hmac.new(app.secret_key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def public_user(user):
    return {
        "id": user["id"],
        "email": user["email"],
        "username": user["username"],
        "createdAt": user["createdAt"],
    }


def find_user_by_email(data, email):
    return next((user for user in data["users"] if user["email"] == email), None)


def find_user_by_id(data, user_id):
    return next((user for user in data["users"] if user["id"] == user_id), None)


def find_user_by_username(data, username):
    username_lower = username.lower()
    return next((user for user in data["users"] if user.get("usernameLower") == username_lower), None)


def require_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    data = load_db()
    return find_user_by_id(data, user_id)


def json_error(message, status):
    return jsonify({"error": message}), status


def send_verification_email(email, code):
    should_log = os.environ.get("AUTH_LOG_VERIFICATION_CODES", "").lower() == "true"
    is_hosted = bool(os.environ.get("VERCEL"))
    is_production = os.environ.get("FLASK_ENV") == "production" or is_hosted

    if should_log or not is_production and not os.environ.get("SMTP_HOST"):
        print(f"[auth] Verification code for {email}: {code}", flush=True)
        return

    smtp_host = os.environ.get("SMTP_HOST")
    smtp_from = os.environ.get("SMTP_FROM")
    if not smtp_host or not smtp_from:
        raise RuntimeError("Email delivery is not configured. Set SMTP_HOST and SMTP_FROM.")

    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    smtp_secure = os.environ.get("SMTP_SECURE", "").lower() == "true"

    message = EmailMessage()
    message["From"] = smtp_from
    message["To"] = email
    message["Subject"] = "Your FriendsNMe verification code"
    message.set_content(f"Your FriendsNMe verification code is {code}. It expires in 10 minutes.")

    if smtp_secure:
        server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20)
    else:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=20)

    with server:
        if not smtp_secure:
            server.starttls()
        if smtp_user and smtp_pass:
            server.login(smtp_user, smtp_pass)
        server.send_message(message)


def latest_usable_code(data, email):
    current = now_utc()
    usable = [
        code
        for code in data["verificationCodes"]
        if code["email"] == email
        and not code.get("usedAt")
        and parse_iso(code["expiresAt"]) > current
    ]
    usable.sort(key=lambda code: code["createdAt"], reverse=True)
    return usable[0] if usable else None


@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/<path:path>")
def static_files(path):
    return send_from_directory(FRONTEND_DIR, path)


@app.get("/api/auth/session")
def auth_session():
    user = require_user()
    if not user:
        return jsonify({"authenticated": False}), 401
    return jsonify({"authenticated": True, "user": public_user(user)})


@app.post("/api/auth/request-code")
def request_code():
    body = request.get_json(silent=True) or {}
    email = normalize_email(body.get("email"))
    if not is_temple_email(email):
        return json_error("Only Temple University email addresses can access this website.", 400)

    data = load_db()
    current = now_utc()
    recent_codes = [
        code
        for code in data["verificationCodes"]
        if code["email"] == email and parse_iso(code["createdAt"]) >= current - timedelta(hours=1)
    ]

    if len(recent_codes) >= MAX_CODES_PER_HOUR:
        return json_error("Too many verification codes requested. Please try again later.", 429)

    recent_codes.sort(key=lambda code: code["createdAt"], reverse=True)
    if recent_codes:
        latest_created = parse_iso(recent_codes[0]["createdAt"])
        if (current - latest_created).total_seconds() < MIN_RESEND_SECONDS:
            return json_error("Please wait before requesting another verification code.", 429)

    code = str(random.SystemRandom().randint(100000, 999999))
    data["verificationCodes"].append(
        {
            "id": str(uuid.uuid4()),
            "email": email,
            "codeHash": hmac_value(f"{email}:{code}"),
            "expiresAt": (current + timedelta(minutes=CODE_TTL_MINUTES)).isoformat(),
            "createdAt": current.isoformat(),
            "attempts": 0,
            "usedAt": None,
        }
    )
    save_db(data)

    try:
        send_verification_email(email, code)
    except RuntimeError as error:
        return json_error(str(error), 503)

    return jsonify({"ok": True, "email": email, "expiresInSeconds": CODE_TTL_MINUTES * 60})


@app.post("/api/auth/verify-code")
def verify_code():
    body = request.get_json(silent=True) or {}
    email = normalize_email(body.get("email"))
    code = str(body.get("code") or "").strip()

    if not is_temple_email(email):
        return json_error("Only Temple University email addresses can access this website.", 400)
    if not re.match(r"^\d{6}$", code):
        return json_error("Enter the 6-digit verification code.", 400)

    data = load_db()
    code_entry = latest_usable_code(data, email)
    if not code_entry or code_entry.get("attempts", 0) >= MAX_CODE_ATTEMPTS:
        return json_error("That verification code is invalid or expired.", 400)

    submitted_hash = hmac_value(f"{email}:{code}")
    if not secrets.compare_digest(submitted_hash, code_entry["codeHash"]):
        code_entry["attempts"] = code_entry.get("attempts", 0) + 1
        save_db(data)
        return json_error("That verification code is invalid or expired.", 400)

    code_entry["usedAt"] = iso_now()
    existing_user = find_user_by_email(data, email)
    save_db(data)

    session.permanent = True
    if existing_user:
        session["user_id"] = existing_user["id"]
        session.pop("verified_email", None)
        return jsonify({"ok": True, "needsUsername": False, "user": public_user(existing_user)})

    session["verified_email"] = email
    return jsonify({"ok": True, "needsUsername": True})


@app.post("/api/auth/complete-signup")
def complete_signup():
    email = session.get("verified_email")
    if not email:
        return json_error("Your signup session expired. Please verify your email again.", 401)

    body = request.get_json(silent=True) or {}
    username = normalize_username(body.get("username"))
    if not username:
        return json_error("Username cannot be empty.", 400)
    if len(username) > 32:
        return json_error("Username must be 32 characters or fewer.", 400)

    data = load_db()
    if find_user_by_email(data, email):
        return json_error("An account already exists for this Temple email.", 409)
    if find_user_by_username(data, username):
        return json_error("That username is already taken.", 409)

    user = {
        "id": str(uuid.uuid4()),
        "email": email,
        "username": username,
        "usernameLower": username.lower(),
        "lastLocation": None,
        "createdAt": iso_now(),
    }
    data["users"].append(user)
    save_db(data)

    session.permanent = True
    session["user_id"] = user["id"]
    session.pop("verified_email", None)
    return jsonify({"ok": True, "user": public_user(user)}), 201


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


def parse_coordinate(value, minimum, maximum, name):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number between {minimum} and {maximum}.")

    if number < minimum or number > maximum:
        raise ValueError(f"{name} must be a number between {minimum} and {maximum}.")
    return number


@app.post("/api/location")
def save_location():
    user = require_user()
    if not user:
        return json_error("You must be signed in to use this feature.", 401)

    body = request.get_json(silent=True) or {}
    try:
        latitude = parse_coordinate(body.get("latitude"), -90, 90, "Latitude")
        longitude = parse_coordinate(body.get("longitude"), -180, 180, "Longitude")
        accuracy = body.get("accuracy")
        accuracy = None if accuracy is None else max(float(accuracy), 0)
        captured_at = body.get("capturedAt") or iso_now()
        parse_iso(captured_at)
    except (TypeError, ValueError):
        return json_error("Invalid location payload.", 400)

    data = load_db()
    stored_user = find_user_by_id(data, user["id"])
    stored_user["lastLocation"] = {
        "latitude": latitude,
        "longitude": longitude,
        "accuracy": accuracy,
        "capturedAt": captured_at,
        "updatedAt": iso_now(),
    }
    save_db(data)
    return jsonify({"ok": True, "location": stored_user["lastLocation"]})


@app.get("/api/location/me")
def get_location():
    user = require_user()
    if not user:
        return json_error("You must be signed in to use this feature.", 401)
    return jsonify({"ok": True, "location": user.get("lastLocation")})


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    print(f"FriendsNMe Flask running locally at http://localhost:{port}", flush=True)
    print(f"FriendsNMe Flask running on the network at http://10.109.29.222:{port}", flush=True)
    app.run(host=host, port=port, debug=True)
