from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

import uuid

db = SQLAlchemy()

# We want to be able to generate a unique id for each account record

def generate_uuid():
    return str(uuid.uuid4())

def utc_now():
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)

    email = db.Column(db.String(120), unique=True, nullable=False)

    username = db.Column(db.String(32), nullable=False)

    username_lower = db.Column(db.String(32), unique=True, nullable=False)

    # We can do last location in the database
    last_location = db.Column(db.JSON, nullable=True)
    # We can use a JSON blob to store Users most recent location
    # (Lat, Long, Accur, Timestamps bundled)

    created_at = db.Column(db.DateTime(timezone=True), default=utc_now)

    def to_public_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "username": self.username,
            # SQLite returns naive datetimes; they are stored as UTC
            "createdAt": self.created_at.replace(tzinfo=timezone.utc).isoformat(),
        }


class LocationShare(db.Model):
    # owner shares their location with viewer. One direction only:
    # each person decides who can see them.
    __tablename__ = "location_shares"
    __table_args__ = (db.UniqueConstraint("owner_id", "viewer_id"),)

    id = db.Column(db.String, primary_key=True, default=generate_uuid)

    owner_id = db.Column(db.String, db.ForeignKey("users.id"), nullable=False, index=True)

    viewer_id = db.Column(db.String, db.ForeignKey("users.id"), nullable=False, index=True)

    created_at = db.Column(db.DateTime(timezone=True), default=utc_now)


class HelpRequest(db.Model):
    # Created when someone doesn't answer their "Are you OK?" check.
    # Friends they share their location with are alerted until it's
    # resolved (they tap "I'm OK") or it expires.
    __tablename__ = "help_requests"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)

    user_id = db.Column(db.String, db.ForeignKey("users.id"), nullable=False, index=True)

    created_at = db.Column(db.DateTime(timezone=True), default=utc_now)

    resolved_at = db.Column(db.DateTime(timezone=True), nullable=True)


class VerificationCode(db.Model):
    __tablename__ = "verification_codes"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)

    email = db.Column(db.String(120), nullable=False, index=True)

    code_hash = db.Column(db.String(64), nullable=False)

    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)

    created_at = db.Column(db.DateTime(timezone=True), default=utc_now)

    attempts = db.Column(db.Integer, default=0)

    used_at = db.Column(db.DateTime(timezone=True), nullable=True)