"""One frozen relative's first response and staff decisions share atomic posting."""
from datetime import timedelta, timezone
import json
from fastapi import HTTPException
from sqlalchemy import select, func
from app.domain.models import (BankReviewDecision, Card, Transaction, TransferParticipant, TransferRequest,
                               TrustedInvitation, TrustedPerson, User, utcnow)
from app.services.bank_service import find_recipient, normalize_recipient, post_transfer, replay, transfer_out
from app.services.fraud_detector import FraudDetector
from app.services.transaction_rules import SCENARIO_ENGINE_VERSION
from app.services.protection_changes import lock_after_due

UNKNOWN_ANSWERS = {"pressure": None, "secrecy": None, "stranger": None}
FAMILY_POLICY_VERSION = "family-v4-first-response"


def risk_warning_policy(db, sender_id, risk):
    """Server-owned voluntary protection policy, shared by preview and command."""
    person = db.scalar(select(TrustedPerson).where(TrustedPerson.user_id == sender_id))
    active = bool(person and person.protection_active)
    return risk.model_copy(update={"family_protection_active": active,
        "requires_risk_acknowledgement": risk.overall_level in {"high", "critical"} and not active})


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _lock(db):
    lock_after_due(db)


def _valid_invite(db, sender_id, invite, actor_id=None):
    person = db.scalar(select(TrustedPerson).where(TrustedPerson.user_id == sender_id))
    if not person or not person.protection_active or not person.relationship_verified:
        return False
    if not invite or not (invite.owner_user_id == sender_id and invite.active and invite.relationship_verified
        and invite.status == "accepted" and invite.accepted_by_user_id == invite.recipient_user_id
        and invite.recipient_user_id is not None and invite.recipient_user_id != sender_id):
        return False
    relative = db.get(User, invite.recipient_user_id)
    return bool(relative and relative.role == "client" and relative.test_iin == invite.trusted_person_test_iin
                and (actor_id is None or relative.id == actor_id))


def _trusted_all(db, sender_id):
    invites = db.scalars(select(TrustedInvitation).where(TrustedInvitation.owner_user_id == sender_id,
        TrustedInvitation.active.is_(True), TrustedInvitation.status == "accepted").order_by(TrustedInvitation.id))
    return [invite for invite in invites if _valid_invite(db, sender_id, invite)]


def _trusted(db, sender_id):
    invites = _trusted_all(db, sender_id)
    return invites[0] if invites else None


def _participants(db, row):
    return list(db.scalars(select(TransferParticipant).where(TransferParticipant.request_id == row.id)
                          .order_by(TransferParticipant.id)))


def _valid_quorum(db, row, participants):
    return bool(participants) and all(_valid_invite(db, row.sender_user_id,
        db.get(TrustedInvitation, p.invitation_id), p.user_id) for p in participants)


def _valid_participant(db, row, participant):
    return participant is not None and _valid_invite(db, row.sender_user_id,
        db.get(TrustedInvitation, participant.invitation_id), participant.user_id)


def _first_response_policy(row):
    return json.loads(row.policy_json).get("policy_version") == FAMILY_POLICY_VERSION


def _expire(row, now):
    if row.status in {"pending_approval", "bank_review"} and _utc(row.expires_at) <= now:
        row.status, row.decided_at = "expired", now


def _refresh_request(db, row, now):
    """Transition waiting legacy requests without ever posting money on a read."""
    _expire(row, now)
    if row.status != "pending_approval" or _first_response_policy(row):
        return
    policy = json.loads(row.policy_json)
    policy["previous_policy_version"] = policy.get("policy_version")
    policy["policy_version"] = FAMILY_POLICY_VERSION
    policy["required_family_approvals"] = 1
    if any(p.response != "pending" for p in _participants(db, row)):
        # A vote cast under unanimous consent must not become a new permission.
        row.status, row.decided_at, row.decided_by_user_id = "cancelled", now, None
        policy["resolution_note"] = "Порядок согласования изменён. Создайте новый перевод."
    row.policy_json = json.dumps(policy)


def _family_approval_valid(db, row, participants):
    if not _first_response_policy(row):
        # Historical bank reviews keep their original consent contract.
        return (_valid_quorum(db, row, participants)
                and not any(p.response == "pending" for p in participants)
                and not all(p.response == "reject" for p in participants))
    decision = json.loads(row.policy_json).get("family_decision", {})
    approver = next((p for p in participants if p.user_id == decision.get("user_id")
                    and p.invitation_id == decision.get("invitation_id")), None)
    return (decision.get("response") == "approve" and approver is not None
            and approver.response == "approve" and _valid_participant(db, row, approver))


def _command_out(db, row):
    if row.transaction_id is not None:
        result = transfer_out(db.get(Transaction, row.transaction_id))
    else:
        result = {"success": False, "transaction_id": None, "recipient_name": row.recipient_name,
            "amount": row.amount_cents / 100, "new_balance": db.get(Card, row.sender_card_id).balance / 100,
            "status": row.status}
    result.update(request_id=row.id, risk=json.loads(row.risk_json), needs_approval=row.status == "pending_approval")
    return result


def request_out(db, row, current_id):
    participants = _participants(db, row)
    mine = next((p for p in participants if p.user_id == current_id), None)
    valid = _valid_participant(db, row, mine)
    decisions = list(db.scalars(select(BankReviewDecision).where(BankReviewDecision.request_id == row.id)))
    viewer = db.get(User, current_id)
    audit = json.loads(row.policy_json).get("ml_evaluation", {}) if viewer and viewer.role == "analyst" else {}
    staff_ml = {key: audit[key] for key in ("status", "model_id", "model_version", "score", "ml_decision",
        "rule_decision", "decision", "out_of_distribution", "thresholds", "currency", "model_currency",
        "unit_version", "amount_kzt", "monetary_thresholds_kzt") if key in audit}
    if staff_ml:
        if audit.get("unit_version") != "kzt-native-v1" and "demo_kzt_per_eur" in audit:
            staff_ml["demo_kzt_per_eur"] = audit["demo_kzt_per_eur"]  # Historical audit, never recomputed.
        staff_ml["ood_reasons"] = ["Контекст операции выходит за диапазон обучающей выборки."] if audit.get("out_of_distribution") else []
    return {"id": row.id, "status": row.status, "sender_name": db.get(User, row.sender_user_id).name,
        "recipient_name": row.recipient_name, "recipient": row.recipient, "amount": row.amount_cents / 100,
        "message": row.message, "created_at": _utc(row.created_at).isoformat(),
        "expires_at": _utc(row.expires_at).isoformat(),
        "decided_at": _utc(row.decided_at).isoformat() if row.decided_at else None,
        "risk": json.loads(row.risk_json), "needs_approval": row.status == "pending_approval",
        "can_approve": row.status == "pending_approval" and valid and mine is not None and mine.response == "pending",
        "can_cancel": row.status in {"pending_approval", "bank_review"} and row.sender_user_id == current_id,
        "participants": [{"invitation_id": p.invitation_id, "user_id": p.user_id, "name": db.get(User, p.user_id).name,
            "response": p.response, "responded_at": _utc(p.responded_at).isoformat() if p.responded_at else None,
            "can_vote": valid and row.status == "pending_approval" and p.user_id == current_id and p.response == "pending"}
            for p in participants], "my_vote": mine.response if mine and mine.response != "pending" else None,
        "anti_scam": json.loads(row.anti_scam_json), "bank_review_reason": row.bank_review_reason,
        "resolution_note": json.loads(row.policy_json).get("resolution_note"),
        "ml_assessment": staff_ml or None,
        "bank_decisions": [{"action": d.action, "note": d.note, "actor_name": db.get(User, d.actor_user_id).name,
                            "created_at": _utc(d.created_at).isoformat()} for d in decisions]}


def _post(db, row):
    posted = post_transfer(db, sender=db.get(User, row.sender_user_id), recipient=db.get(User, row.recipient_user_id),
        sender_card_id=row.sender_card_id, recipient_card_id=row.recipient_card_id, cents=row.amount_cents,
        phone=row.recipient, message=row.message, request_key=row.request_key)
    row.transaction_id = posted.id


def create_transfer(data, db, current, idempotency_key):
    if not idempotency_key.strip():
        raise HTTPException(422, "Idempotency-Key не должен состоять только из пробелов")
    phone, cents, current_id = normalize_recipient(data.recipient), int(data.amount * 100), current.id
    answers = data.anti_scam.model_dump() if data.anti_scam is not None else dict(UNKNOWN_ANSWERS)
    key = f"{current_id}:{idempotency_key}"
    _lock(db)
    try:
        existing = db.scalar(select(TransferRequest).where(TransferRequest.request_key == key))
        if existing:
            policy = json.loads(existing.policy_json)
            if (existing.recipient != phone or existing.amount_cents != cents or existing.message != data.message
                    or json.loads(existing.anti_scam_json) != answers
                    or policy.get("risk_warning_acknowledged", False) != data.risk_warning_acknowledged):
                raise HTTPException(409, "Этот ключ уже использован для другого перевода")
            _refresh_request(db, existing, utcnow())
            result = _command_out(db, existing)
            db.commit()
            return result
        legacy = db.scalar(select(Transaction).where(Transaction.request_key == key))
        if legacy:
            if answers != UNKNOWN_ANSWERS or data.risk_warning_acknowledged:
                raise HTTPException(409, "Сохранённый перевод не содержит этих ответов")
            data.recipient = phone
            result = replay(legacy, data, cents)
            db.commit()
            return result
        sender = db.get(User, current_id)
        recipient = find_recipient(db, phone)
        if recipient.id == current_id:
            raise HTTPException(400, "Для перевода выберите другого получателя")
        sender_card = db.scalar(select(Card).where(Card.user_id == current_id, Card.status == "active", Card.currency == "KZT").order_by(Card.id))
        recipient_card = db.scalar(select(Card).where(Card.user_id == recipient.id, Card.status == "active", Card.currency == "KZT").order_by(Card.id))
        if not sender_card:
            raise HTTPException(404, "Активная тестовая карта не найдена")
        if not recipient_card:
            raise HTTPException(400, "У получателя нет активной карты в тенге")
        now = utcnow()
        risk = FraudDetector().evaluate_transfer(db, sender, phone, data.amount, as_of=now)
        risk = risk_warning_policy(db, current_id, risk)
        risk = risk.model_copy(update={"transfer_enforcement": True, "advisory": False, "anti_scam": answers})
        high, scam = risk.overall_level in {"high", "critical"}, any(value is True for value in answers.values())
        requires_family = high and risk.family_protection_active
        if risk.requires_risk_acknowledgement and not data.risk_warning_acknowledged:
            raise HTTPException(428, "Перевод имеет высокий риск. Ознакомьтесь с предупреждением и явно подтвердите перевод.")
        invites = _trusted_all(db, current_id) if requires_family else []
        status = ("pending_approval" if invites else "blocked_no_trusted") if requires_family else "bank_review" if scam else "completed"
        row = TransferRequest(request_key=key, sender_user_id=current_id, recipient_user_id=recipient.id,
            sender_card_id=sender_card.id, recipient_card_id=recipient_card.id,
            trusted_user_id=invites[0].accepted_by_user_id if invites else None, invitation_id=invites[0].id if invites else None,
            recipient=phone, recipient_name=recipient.name, message=data.message, amount_cents=cents,
            risk_json=risk.model_dump_json(), anti_scam_json=json.dumps(answers),
            bank_review_reason="anti_scam" if status == "bank_review" else None,
            policy_json=json.dumps({"policy_version": FAMILY_POLICY_VERSION, "required_family_approvals": 1,
                "scenario_engine_version": SCENARIO_ENGINE_VERSION,
                "high_threshold": 0.72, "critical_threshold": 0.95, "requires_family": requires_family,
                "risk_warning_acknowledged": data.risk_warning_acknowledged,
                "engine_mode": risk.engine_mode, "ml_evaluation": risk._ml_audit}), created_at=now, expires_at=now + timedelta(hours=24),
            status=status, decided_at=now if status in {"completed", "blocked_no_trusted"} else None,
            decided_by_user_id=current_id if status in {"completed", "blocked_no_trusted"} else None)
        db.add(row)
        db.flush()
        for invite in invites:
            db.add(TransferParticipant(request_id=row.id, user_id=invite.accepted_by_user_id, invitation_id=invite.id, response="pending"))
        if status == "completed":
            _post(db, row)
        db.flush()
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
        query = select(TransferRequest)
        if relatives:
            query = query.join(TransferParticipant).where(TransferParticipant.user_id == current_id)
        else:
            query = query.where(TransferRequest.sender_user_id == current_id)
        rows = list(db.scalars(query.order_by(TransferRequest.id.desc())))
        for row in rows:
            _refresh_request(db, row, utcnow())
        result = [request_out(db, row, current_id) for row in rows if not relatives or
                  row.status in {"pending_approval", "bank_review"} and any(
                      p.user_id == current_id and _valid_participant(db, row, p) for p in _participants(db, row))]
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
        participants = _participants(db, row)
        mine = next((p for p in participants if p.user_id == current_id), None)
        if action == "cancel":
            if row.sender_user_id != current_id:
                raise HTTPException(403, "Отменить запрос может только отправитель")
        elif current_id == row.sender_user_id or not _valid_participant(db, row, mine):
            raise HTTPException(403, "Вы не являетесь действующим родственником этого запроса")
        _refresh_request(db, row, utcnow())
        if row.status == "expired" or (row.status == "cancelled" and json.loads(row.policy_json).get("resolution_note")):
            result = request_out(db, row, current_id)
            db.commit()
            return result
        if action == "cancel":
            if row.status == "cancelled":
                pass
            elif row.status in {"pending_approval", "bank_review"}:
                row.status, row.decided_at, row.decided_by_user_id = "cancelled", utcnow(), current_id
            else:
                raise HTTPException(409, "Решение по запросу уже принято")
        elif mine.response != "pending":
            if mine.response != action:
                raise HTTPException(409, "Сохранённый голос нельзя изменить")
        else:
            if row.status != "pending_approval":
                raise HTTPException(409, "Семейное голосование завершено")
            mine.response, mine.responded_at = action, utcnow()
            policy = json.loads(row.policy_json)
            policy["family_decision"] = {"user_id": mine.user_id, "invitation_id": mine.invitation_id,
                "response": action, "responded_at": _utc(mine.responded_at).isoformat()}
            row.policy_json = json.dumps(policy)
            if action == "reject":
                row.status = "rejected"
            elif any(v is True for v in json.loads(row.anti_scam_json).values()):
                row.status, row.bank_review_reason = "bank_review", "anti_scam"
            else:
                _post(db, row)
                row.status = "completed"
            if row.status != "bank_review":
                row.decided_at, row.decided_by_user_id = utcnow(), current_id
        db.flush()
        result = request_out(db, row, current_id)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def bank_events(db, current, *, request_id=None, status=None, page=1, page_size=20, sender_name=None):
    if current.role != 'analyst':
        raise HTTPException(403, 'Analyst access required')
    current_id = current.id
    _lock(db)
    try:
        # Apply expirations before filters so the staff queue never presents an
        # already expired operation as an actionable review.
        for row in db.scalars(select(TransferRequest).where(TransferRequest.status.in_(["pending_approval", "bank_review"]))):
            _refresh_request(db, row, utcnow())
        query = select(TransferRequest)
        if request_id is not None:
            row = db.get(TransferRequest, request_id)
            if row is None:
                raise HTTPException(404, "Банковское событие не найдено")
            result = request_out(db, row, current_id)
        else:
            if status:
                query = query.where(TransferRequest.status == status)
            if sender_name:
                query = query.join(User, User.id == TransferRequest.sender_user_id).where(User.name.contains(sender_name, autoescape=True))
            total = db.scalar(select(func.count()).select_from(query.subquery()))
            rows = list(db.scalars(query.order_by(TransferRequest.id.desc()).offset((page-1)*page_size).limit(page_size)))
            result = {"items": [request_out(db, row, current_id) for row in rows], "total": total, "page": page, "page_size": page_size}
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def bank_decide(db, current, request_id, action, note):
    if current.role != 'analyst':
        raise HTTPException(403, 'Analyst access required')
    actor_id = current.id
    _lock(db)
    try:
        row = db.get(TransferRequest, request_id)
        if row is None:
            raise HTTPException(404, "Банковское событие не найдено")
        prior = db.scalar(select(BankReviewDecision).where(BankReviewDecision.request_id == row.id))
        if prior:
            if prior.action != action or prior.note != note:
                raise HTTPException(409, "Решение сотрудника уже сохранено")
            result = request_out(db, row, actor_id)
            db.commit()
            return result
        _refresh_request(db, row, utcnow())
        if row.status == "expired" or (row.status == "cancelled" and json.loads(row.policy_json).get("resolution_note")):
            result = request_out(db, row, actor_id)
            db.commit()
            return result
        if row.status != "bank_review":
            raise HTTPException(409, "Сотрудник решает только переданные на банковскую проверку запросы")
        participants = _participants(db, row)
        requires_family = json.loads(row.policy_json).get("requires_family", json.loads(row.risk_json)["overall_level"] in {"high", "critical"})
        if action == "approve" and requires_family and not _family_approval_valid(db, row, participants):
            raise HTTPException(409, "Действующего одобрения родственника нет")
        if action == "approve":
            _post(db, row)
        row.status = "completed" if action == "approve" else "rejected"
        row.decided_at, row.decided_by_user_id = utcnow(), actor_id
        db.add(BankReviewDecision(request_id=row.id, actor_user_id=actor_id, action=action, note=note, created_at=row.decided_at))
        db.flush()
        result = request_out(db, row, actor_id)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
