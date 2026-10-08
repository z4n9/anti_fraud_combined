"""24-hour cooling period. No balance or transaction history is modified here."""
from datetime import timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select, text

from app.domain.models import ProtectionChangeRequest, TrustedPerson, utcnow


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def change_out(row):
    return {"id": row.id, "action": row.action, "status": row.status,
            "created_at": _utc(row.created_at).isoformat(), "effective_at": _utc(row.effective_at).isoformat(),
            "decided_at": _utc(row.decided_at).isoformat() if row.decided_at else None,
            "can_cancel": row.status == "pending"}


def apply_due(db, user_id=None, *, now=None):
    """Caller owns the write lock and commit. Match the frozen invitation identity."""
    now = _utc(now or utcnow())
    query = select(ProtectionChangeRequest).where(ProtectionChangeRequest.status == "pending",
        ProtectionChangeRequest.effective_at <= now.replace(tzinfo=None)).order_by(ProtectionChangeRequest.id)
    if user_id is not None:
        query = query.where(ProtectionChangeRequest.user_id == user_id)
    rows = list(db.scalars(query))
    for row in rows:
        person = db.scalar(select(TrustedPerson).where(TrustedPerson.user_id == row.user_id))
        # A pending, unaccepted invitation may have been replaced during the wait.
        # Never apply an old removal to a newly selected relationship.
        if person is None or (row.action == "remove" and person.current_invitation_id != row.invitation_id):
            row.status = "cancelled"
        else:
            person.protection_active = False
            if row.action == "remove":
                person.name = person.phone = person.relationship = ""
                person.verified = person.relationship_verified = False
                person.invitation_status = None
                person.trusted_person_test_iin = None
                person.current_invitation_id = None
            row.status = "executed"
        row.decided_at = now
        row.decided_by_user_id = None  # Scheduled server action.
    if rows:
        db.flush()
    return bool(rows)


def settle_due(db, user_id=None):
    """Commit only the scheduled protection lifecycle before a financial command.

    A later denied approval must not roll back an already effective removal.
    The financial command rechecks due changes under its own lock afterwards.
    """
    db.execute(text("BEGIN IMMEDIATE"))
    db.expire_all()
    try:
        apply_due(db, user_id)
        db.commit()
    except Exception:
        db.rollback()
        raise


def lock_after_due(db, user_id=None):
    """Start a decision lock after durably applying every already due change."""
    settle_due(db, user_id)
    while True:
        db.execute(text("BEGIN IMMEDIATE"))
        db.expire_all()
        if not apply_due(db, user_id):
            return
        # No decision or monetary writes exist yet. Persist the lifecycle even
        # when an upcoming permission/validation check will reject the command.
        db.commit()


def schedule_change(db, user_id, action):
    """Caller owns lock/commit; repeats retain the original effective deadline."""
    existing = db.scalar(select(ProtectionChangeRequest).where(ProtectionChangeRequest.user_id == user_id,
        ProtectionChangeRequest.action == action, ProtectionChangeRequest.status == "pending"))
    if existing:
        return existing
    person = db.scalar(select(TrustedPerson).where(TrustedPerson.user_id == user_id))
    if person is None:
        raise HTTPException(404, "Доверенное лицо не найдено")
    if action == "disable" and not person.protection_active:
        raise HTTPException(409, "Семейная защита уже отключена")
    if action == "remove" and person.current_invitation_id is None:
        raise HTTPException(409, "Доверенное лицо не назначено")
    now = utcnow()
    row = ProtectionChangeRequest(user_id=user_id, invitation_id=person.current_invitation_id,
        action=action, status="pending", created_at=now, effective_at=now + timedelta(hours=24))
    db.add(row)
    db.flush()
    return row


def request_change(db, current, action):
    user_id = current.id
    lock_after_due(db, user_id)
    try:
        apply_due(db, user_id)
        row = schedule_change(db, user_id, action)
        result = change_out(row)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def list_changes(db, current):
    user_id = current.id
    settle_due(db, user_id)
    rows = db.scalars(select(ProtectionChangeRequest).where(ProtectionChangeRequest.user_id == user_id)
                      .order_by(ProtectionChangeRequest.id.desc()))
    return [change_out(row) for row in rows]


def cancel_change(db, current, request_id):
    user_id = current.id
    lock_after_due(db, user_id)
    try:
        apply_due(db, user_id)
        row = db.get(ProtectionChangeRequest, request_id)
        if row is None or row.user_id != user_id:
            raise HTTPException(404, "Запрос изменения защиты не найден")
        if row.status == "executed":
            raise HTTPException(409, "Изменение защиты уже вступило в силу")
        if row.status == "pending":
            row.status = "cancelled"
            row.decided_at = utcnow()
            row.decided_by_user_id = user_id
        result = change_out(row)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
