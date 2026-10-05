"""Account-scoped bank posting primitives; commands own the transaction."""
import re

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.models import Card, Transaction, User
from app.domain.schemas import TransferIn


def normalize_recipient(phone):
    phone = re.sub(r"[\s()\-]", "", phone)
    if not re.fullmatch(r"\+7[0-9]{10}", phone):
        raise HTTPException(422, "Введите номер: +7 и 10 цифр")
    return phone


def find_recipient(db, phone):
    person = db.scalar(select(User).where(User.phone == phone))
    if person is None:
        raise HTTPException(404, "Получатель с таким номером не найден")
    return person


def transfer_out(transaction):
    return {"success": True, "transaction_id": transaction.id, "status": transaction.status,
            "recipient_name": transaction.title, "amount": -transaction.amount / 100,
            "new_balance": transaction.balance_after / 100}


def replay(existing, data, cents):
    if existing.recipient != data.recipient or existing.amount != -cents or existing.message != data.message:
        raise HTTPException(409, "Этот ключ уже использован для другого перевода")
    return transfer_out(existing)


def execute_transfer(data: TransferIn, db: Session, current: User, idempotency_key: str):
    from app.services.transfer_approval_service import create_transfer
    return create_transfer(data, db, current, idempotency_key)


def post_transfer(db, *, sender, recipient, sender_card_id, recipient_card_id,
                  cents, phone, message, request_key):
    """Post both ledger sides without committing; caller owns the transaction."""
    balance = db.scalar(update(Card).where(Card.id == sender_card_id, Card.user_id == sender.id,
                        Card.currency == "KZT", Card.status == "active", Card.balance >= cents)
                        .values(balance=Card.balance - cents).returning(Card.balance))
    if balance is None:
        raise HTTPException(400, "Недостаточно средств или карта отправителя недоступна")
    row = Transaction(user_id=sender.id, card_id=sender_card_id, type="transfer", title=recipient.name,
                      amount=-cents, recipient=phone, message=message, status="completed",
                      request_key=request_key, balance_after=balance)
    db.add(row)
    db.flush()
    recipient_balance = db.scalar(update(Card).where(Card.id == recipient_card_id,
        Card.user_id == recipient.id, Card.currency == "KZT", Card.status == "active")
        .values(balance=Card.balance + cents).returning(Card.balance))
    if recipient_balance is None:
        raise HTTPException(400, "Карта получателя недоступна")
    db.add(Transaction(user_id=recipient.id, card_id=recipient_card_id, type="income", title=sender.name,
                       amount=cents, recipient=sender.phone, message=message, status="completed",
                       request_key=f"credit:{row.id}", balance_after=recipient_balance))
    db.flush()
    return row
