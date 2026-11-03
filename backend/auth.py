import hashlib
import hmac
import secrets
from datetime import timedelta
from fastapi import HTTPException, Request
from sqlalchemy import select
from backend.db import SessionLocal
from backend.models import User, Session, utcnow

COOKIE = 'serviceops_session'
IT_COOKIE = 'serviceops_it'
# Precomputed once, not an extra PBKDF2 computation on each unknown-username request.
DUMMY_PASSWORD_HASH = 'pbkdf2_sha256$600000$00000000000000000000000000000000$10e225d31f733befe8034aac3704f36cddc8b9a281a2f531e81e6edf2162098b'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000)
    return f'pbkdf2_sha256$600000${salt}${key.hex()}'


def verify_password(password, stored):
    return hmac.compare_digest(hash_password(password, stored.split('$')[2]), stored)


def login(username, password, required_role):
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        # Equal work for unknown usernames; no username enumeration in errors.
        valid = verify_password(password, user.password_hash if user else DUMMY_PASSWORD_HASH)
        if not user or not valid or user.role != required_role:
            raise HTTPException(401, 'invalid_credentials')
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db.add(Session(token_hash=digest(token), user_id=user.id, csrf_hash=digest(csrf),
                       expires_at=utcnow() + timedelta(hours=8)))
        db.commit()
        return user, token, csrf


def authenticate(request: Request, cookie=COOKIE, required_role='employee', write=False):
    with SessionLocal() as db:
        session = db.get(Session, digest(request.cookies.get(cookie, '')))
        if not session or session.expires_at <= utcnow():
            raise HTTPException(401, 'unauthenticated')
        user = db.get(User, session.user_id)
        if not user or user.role != required_role:
            raise HTTPException(403, 'forbidden_role')
        if write and not hmac.compare_digest(digest(request.headers.get('X-CSRF-Token', '')), session.csrf_hash):
            raise HTTPException(403, 'csrf_denied')
        request.state.user_id = user.id
        return user
