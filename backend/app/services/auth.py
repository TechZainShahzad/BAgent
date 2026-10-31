import base64
import hashlib
import hmac
import os
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orchestration.audit import append_audit
from app.persistence.models import User

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, dk_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


def create_user(session: Session, email: str, password: str) -> User:
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    user = User(email=email.lower().strip(), password_hash=hash_password(password))
    session.add(user)
    session.flush()
    append_audit(session, "user.created", {"user_id": str(user.id), "email": user.email})
    return user


def authenticate(session: Session, email: str, password: str) -> User | None:
    user = session.scalars(select(User).where(User.email == email.lower().strip())).first()
    ok = user is not None and user.is_active and verify_password(password, user.password_hash)
    if user is None or not ok:
        # run a dummy hash to keep timing similar for unknown users
        if user is None:
            verify_password(password, hash_password("dummy-password"))
        append_audit(session, "auth.login_failed", {"email": email.lower().strip()[:200]})
        return None
    append_audit(session, "auth.login_succeeded", {"user_id": str(user.id)})
    return user


def get_user(session: Session, user_id: uuid.UUID) -> User | None:
    return session.get(User, user_id)


def set_password(session: Session, email: str, password: str) -> User:
    """Reset a user's password (CLI admin action). Audited; the password is never logged."""
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    user = session.scalars(select(User).where(User.email == email.lower().strip())).first()
    if user is None:
        raise LookupError(f"no user with email {email}")
    user.password_hash = hash_password(password)
    append_audit(session, "user.password_reset", {"user_id": str(user.id), "email": user.email})
    return user


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.email)))
