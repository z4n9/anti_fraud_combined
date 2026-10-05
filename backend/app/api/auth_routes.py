"""Password-based accounts and revocable server-side sessions for local test users."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.domain.models import LoginSession, User
from app.domain.schemas import LoginIn

from app.core.security import COOKIE, DUMMY_HASH, password_matches, token_hash

router = APIRouter(prefix="/api/auth", tags=["Аккаунты"])
DB = Annotated[Session, Depends(get_db)]

@router.post("/login")
def login(data: LoginIn, request: Request, response: Response, db: DB):
    user = db.scalar(select(User).where(User.test_iin == data.test_iin))
    valid = password_matches(data.password, user.password_hash if user and user.password_hash else DUMMY_HASH)
    if not user or not valid:
        raise HTTPException(401, "Неверный TEST-ИИН или пароль")
    old = request.cookies.get(COOKIE)
    if old:
        db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(old)))
    db.execute(delete(LoginSession).where(LoginSession.expires_at < datetime.now(timezone.utc)))
    token = secrets.token_urlsafe(32)
    db.add(LoginSession(user_id=user.id, token_hash=token_hash(token), expires_at=datetime.now(timezone.utc)+timedelta(hours=12)))
    db.commit()
    response.set_cookie(COOKIE, token, max_age=43200, httponly=True, samesite="strict", secure=request.url.scheme == "https")
    return {"id": user.id, "name": user.name, "test_iin": user.test_iin}


@router.post("/logout")
def logout(request: Request, response: Response, db: DB):
    token = request.cookies.get(COOKIE)
    if token:
        db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(token)))
        db.commit()
    response.delete_cookie(COOKIE, httponly=True, samesite="strict")
    return {"success": True}


