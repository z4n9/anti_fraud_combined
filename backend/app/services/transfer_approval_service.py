"""Serialized transfer decisions. Financial writes and terminal status commit together."""
from datetime import timedelta, timezone
import json

from fastapi import HTTPException
from sqlalchemy import select, text

from app.domain.models import Card, Transaction, TransferRequest, TrustedInvitation, TrustedPerson, User, utcnow
from app.services.bank_service import find_recipient, normalize_recipient, post_transfer, replay, transfer_out
from app.services.fraud_detector import FraudDetector
from app.services.transaction_rules import SCENARIO_ENGINE_VERSION


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _lock(db):
    # Auth SELECTs do not start a SQLite write transaction. Acquire before any
    # command reads, then invalidate objects potentially loaded by authentication.
    db.execute(text("BEGIN IMMEDIATE"))
    db.expire_all()


def _trusted(db, sender_id):
    person = db.scalar(select(TrustedPerson).where(TrustedPerson.user_id == sender_id))
    if not person or not (person.protection_active and person.relationship_verified
                          and person.invitation_status == "accepted" and person.current_invitation_id):
        return None
    invite = db.get(TrustedInvitation, person.current_invitation_id)
    if not invite or not (invite.owner_user_id == sender_id and invite.status == "accepted"
        and invite.recipient_user_id is not None and invite.accepted_by_user_id == invite.recipient_user_id
        and invite.recipient_user_id != sender_id
        and person.trusted_person_test_iin == invite.trusted_person_test_iin):
        return None
    relative = db.get(User, invite.recipient_user_id)
    if relative is None or relative.test_iin != invite.trusted_person_test_iin:
        return None
    return invite


def _expire(row, now):
    if row.status == "pending_approval" and _utc(row.expires_at) <= now:
        row.status = "expired"
        row.decided_at = now


def _command_out(db, row):
    if row.transaction_id is not None:
        result = transfer_out(db.get(Transaction, row.transaction_id))
    else:
        card = db.get(Card, row.sender_card_id)
        result = {"success": False, "transaction_id": None, "recipient_name": row.recipient_name,
                  "amount": row.amount_cents / 100, "new_balance": card.balance / 100,
                  "status": row.status}
    result.update(request_id=row.id, risk=json.loads(row.risk_json), needs_approval=row.status == "pending_approval")
    return result


def request_out(db, row, current_id):
    invite = _trusted(db, row.sender_user_id)
    valid_relative = bool(invite and invite.id == row.invitation_id
                          and invite.accepted_by_user_id == current_id == row.trusted_user_id)
    return {"id": row.id, "status": row.status, "sender_name": db.get(User, row.sender_user_id).name,
            "recipient_name": row.recipient_name, "recipient": row.recipient, "amount": row.amount_cents / 100,
            "message": row.message, "created_at": _utc(row.created_at).isoformat(),
            "expires_at": _utc(row.expires_at).isoformat(),
            "decided_at": _utc(row.decided_at).isoformat() if row.decided_at else None,
            "risk": json.loads(row.risk_json), "needs_approval": row.status == "pending_approval",
            "can_approve": row.status == "pending_approval" and valid_relative,
            "can_cancel": row.status == "pending_approval" and row.sender_user_id == current_id}


def create_transfer(data, db, current, idempotency_key):
    if not idempotency_key.strip():
        raise HTTPException(422, "Idempotency-Key не должен состоять только из пробелов")
    phone = normalize_recipient(data.recipient)
    cents = int(data.amount * 100)
    current_id = current.id
    key = f"{current_id}:{idempotency_key}"
    _lock(db)
    try:
        existing = db.scalar(select(TransferRequest).where(TransferRequest.request_key == key))
        if existing:
            if (existing.recipient != phone or existing.amount_cents != cents or existing.message != data.message):
                raise HTTPException(409, "Этот ключ уже использован для другого перевода")
            _expire(existing, utcnow())
            result = _command_out(db, existing)
            db.commit()
            return result
        legacy = db.scalar(select(Transaction).where(Transaction.request_key == key))
        if legacy:
            data.recipient = phone
            result = replay(legacy, data, cents)
            db.commit()
            return result
        sender = db.get(User, current_id)
        recipient = find_recipient(db, phone)
        if recipient.id == current_id:
            raise HTTPException(400, "Для перевода выберите другого получателя")
        sender_card = db.scalar(select(Card).where(Card.user_id == current_id, Card.status == "active",
                                                   Card.currency == "KZT").order_by(Card.id))
        recipient_card = db.scalar(select(Card).where(Card.user_id == recipient.id, Card.status == "active",
                                                      Card.currency == "KZT").order_by(Card.id))
        if not sender_card:
            raise HTTPException(404, "Активная тестовая карта не найдена")
        if not recipient_card:
            raise HTTPException(400, "У получателя нет активной карты в тенге")
        now = utcnow()
        risk = FraudDetector().evaluate_transfer(db, sender, phone, data.amount, as_of=now)
        risk = risk.model_copy(update={"transfer_enforcement": True, "advisory": False})
        high = risk.overall_level in {"high", "critical"}
        invite = _trusted(db, current_id) if high else None
        row = TransferRequest(request_key=key, sender_user_id=current_id, recipient_user_id=recipient.id,
            sender_card_id=sender_card.id, recipient_card_id=recipient_card.id,
            trusted_user_id=invite.accepted_by_user_id if invite else None,
            invitation_id=invite.id if invite else None, recipient=phone, recipient_name=recipient.name,
            message=data.message, amount_cents=cents, risk_json=risk.model_dump_json(), created_at=now,
            policy_json=json.dumps({"policy_version": "family-v1", "scenario_engine_version": SCENARIO_ENGINE_VERSION,
                                   "high_threshold": 0.72, "critical_threshold": 0.95,
                                   "engine_mode": "rule_based_fallback"}),
            decided_by_user_id=None if invite else current_id,
            expires_at=now + timedelta(hours=24), decided_at=None if invite else now,
            status="pending_approval" if invite else "blocked_no_trusted" if high else "completed")
        db.add(row)
        db.flush()
        if not high:
            posted = post_transfer(db, sender=sender, recipient=recipient, sender_card_id=sender_card.id,
                recipient_card_id=recipient_card.id, cents=cents, phone=phone, message=data.message, request_key=key)
            row.transaction_id = posted.id
        result = _command_out(db, row)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def list_requests(db, current, *, relatives=False):
    current_id = current.id
    _lock(db)
    try:
        condition = TransferRequest.trusted_user_id == current_id if relatives else TransferRequest.sender_user_id == current_id
        rows = list(db.scalars(select(TransferRequest).where(condition).order_by(TransferRequest.id.desc())))
        now = utcnow()
        for row in rows:
            _expire(row, now)
        result = [request_out(db, row, current_id) for row in rows
                  if not relatives or row.status == "pending_approval"
                  and (invite := _trusted(db, row.sender_user_id)) is not None
                  and invite.id == row.invitation_id and invite.accepted_by_user_id == current_id]
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def decide_request(db, current, request_id, action):
    current_id = current.id
    _lock(db)
    try:
        row = db.get(TransferRequest, request_id)
        if row is None:
            raise HTTPException(404, "Запрос перевода не найден")
        if action == "cancel":
            if row.sender_user_id != current_id:
                raise HTTPException(403, "Отменить запрос может только отправитель")
        else:
            if current_id == row.sender_user_id or current_id != row.trusted_user_id:
                raise HTTPException(403, "Решение принимает назначенный доверенный родственник")
            invite = _trusted(db, row.sender_user_id)
            if not invite or invite.id != row.invitation_id or invite.accepted_by_user_id != current_id:
                raise HTTPException(403, "Полномочия доверенного родственника больше не действуют")
        now = utcnow()
        _expire(row, now)
        desired = {"approve": "completed", "reject": "rejected", "cancel": "cancelled"}[action]
        if row.status == "expired":
            result = request_out(db, row, current_id)
            db.commit()
            return result
        if row.status == desired:
            result = request_out(db, row, current_id)
            db.commit()
            return result
        if row.status != "pending_approval":
            raise HTTPException(409, "Решение по этому запросу уже принято")
        if action == "approve":
            sender = db.get(User, row.sender_user_id)
            recipient = db.get(User, row.recipient_user_id)
            posted = post_transfer(db, sender=sender, recipient=recipient,
                sender_card_id=row.sender_card_id, recipient_card_id=row.recipient_card_id,
                cents=row.amount_cents, phone=row.recipient, message=row.message, request_key=row.request_key)
            row.transaction_id = posted.id
        row.status = desired
        row.decided_at = now
        row.decided_by_user_id = current_id
        db.flush()
        result = request_out(db, row, current_id)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
