"""Password-based accounts and revocable server-side sessions for local test users."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.domain.models import LoginSession, User

DB = Annotated[Session, Depends(get_db)]
COOKIE = "aman_session"

def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=1, maxmem=67108864)
    return salt.hex() + ":" + digest.hex()


def password_matches(password, encoded):
    try:
        salt, digest = encoded.split(":")
        value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=67108864)
        return hmac.compare_digest(value.hex(), digest)
    except (ValueError, AttributeError):
        return False


DUMMY_HASH = hash_password(secrets.token_urlsafe(24))

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def current_account(request: Request, db: DB):
    token = request.cookies.get(COOKIE)
    session = db.scalar(select(LoginSession).where(LoginSession.token_hash == token_hash(token))) if token else None
    if session is None or session.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        raise HTTPException(401, "Войдите в свой аккаунт")
    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(401, "Аккаунт не найден")
    expected = request.headers.get("x-account-id")
    if expected and expected != str(user.id):
        raise HTTPException(409, "В другой вкладке сменился аккаунт. Обновите страницу и войдите заново.")
    return user


CurrentAccount = Annotated[User, Depends(current_account)]


def current_user(account: CurrentAccount):
    if account.role != "client":
        raise HTTPException(403, "Этот раздел доступен клиентам банка")
    return account


CurrentUser = Annotated[User, Depends(current_user)]
