"""Local teaching app. Account-scoped endpoints; no real banking."""
from datetime import timezone
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.domain.models import Card, Transaction
from app.domain.schemas import CardOut, TransactionOut, UserOut
from app.core.security import CurrentUser
from app.services.family_service import mask_phone

from app.services.bank_service import find_recipient, normalize_recipient

router = APIRouter(tags=["Банк"])
DB = Annotated[Session, Depends(get_db)]

@router.get("/api/user", response_model=UserOut)
def user(db: DB, current: CurrentUser):
    value = current
    if value is None:
        raise HTTPException(404, "Тестовый пользователь не найден")
    return {"id": value.id, "name": value.name, "phone": mask_phone(value.phone), "test_iin": value.test_iin}


@router.get("/api/cards", response_model=list[CardOut])
def cards(db: DB, current: CurrentUser):
    return [{"id": c.id, "card_name": c.card_name, "last_four": c.last_four,
             "balance": c.balance / 100, "currency": c.currency, "expiry": c.expiry,
             "status": c.status} for c in db.scalars(select(Card).where(Card.user_id == current.id).order_by(Card.id))]


@router.get("/api/transactions", response_model=list[TransactionOut])
def transactions(db: DB, current: CurrentUser):
    rows = db.scalars(select(Transaction).where(Transaction.user_id == current.id)
                      .order_by(Transaction.created_at.desc(), Transaction.id.desc()))
    return [{"id": t.id, "type": t.type, "title": t.title, "amount": t.amount / 100,
             "recipient": t.recipient, "message": t.message, "status": t.status,
             "created_at": t.created_at.replace(tzinfo=timezone.utc).isoformat()} for t in rows]


@router.get("/api/recipients")
def recipient_lookup(phone: str, db: DB, current: CurrentUser):
    person = find_recipient(db, normalize_recipient(phone))
    return {"name": person.name, "phone": person.phone}


