"""Family consent lifecycle. Only the authenticated invitee can give consent."""
from datetime import timezone
from fastapi import HTTPException
from sqlalchemy import select, text
from app.services.mock_egov import verify_relationship
from app.domain.models import TrustedInvitation, TrustedPerson, User, utcnow

def mask_phone(phone):
    digits = "".join(c for c in phone if c.isdigit())
    return f"+7 {digits[1:4]} *** ** {digits[-2:]}" if len(digits) == 11 else "Номер скрыт"


def trusted_out(person):
    ready = bool(person.protection_active and person.relationship_verified and person.invitation_status == "accepted")
    return {"id": person.id, "name": person.name, "phone": mask_phone(person.phone),
            "relationship": person.relationship, "verified": person.relationship_verified,
            "relationship_verified": person.relationship_verified,
            "invitation_status": person.invitation_status,
            "trusted_person_test_iin": person.trusted_person_test_iin,
            "current_invitation_id": person.current_invitation_id,
            "family_protection_ready": ready,
            "protection_active": person.protection_active,
            "notifications_enabled": person.notifications_enabled,
            "confirmation_enabled": person.confirmation_enabled}


def get_trusted(db, user_id):
    person = db.scalar(select(TrustedPerson).where(TrustedPerson.user_id == user_id))
    if person is None:
        raise HTTPException(404, "Доверенное лицо не найдено")
    return person


def invitation_out(db, invitation):
    owner = db.get(User, invitation.owner_user_id)
    reverse = verify_relationship(db, invitation.trusted_person_test_iin, owner.test_iin)
    return {"id": invitation.id, "owner_user_id": owner.id, "owner_name": owner.name,
            "trusted_person_name": invitation.trusted_person_name,
            "trusted_person_test_iin": invitation.trusted_person_test_iin,
            "relationship": invitation.relationship,
            "reverse_relationship": reverse.get("relationship", "Родство не найдено"),
            "status": invitation.status,
            "recipient_user_id": invitation.recipient_user_id,
            "accepted_by_user_id": invitation.accepted_by_user_id,
            "created_at": invitation.created_at.replace(tzinfo=timezone.utc).isoformat(),
            "updated_at": invitation.updated_at.replace(tzinfo=timezone.utc).isoformat()}


def respond(db, invitation_id, status, current):
    db.execute(text("BEGIN IMMEDIATE"))
    invitation = db.get(TrustedInvitation, invitation_id)
    if invitation is None:
        raise HTTPException(404, "Приглашение не найдено")
    if invitation.recipient_user_id != current.id or invitation.trusted_person_test_iin != current.test_iin:
        raise HTTPException(403, "Ответить может только приглашённый родственник")
    person = get_trusted(db, invitation.owner_user_id)
    if person.current_invitation_id != invitation.id:
        raise HTTPException(409, "Это приглашение заменено новым. Обновите данные.")
    if invitation.status != "pending" and invitation.status != status:
        raise HTTPException(409, "Ответ уже сохранён. Для изменения создайте новое приглашение.")
    if not person.relationship_verified or person.trusted_person_test_iin != invitation.trusted_person_test_iin:
        raise HTTPException(409, "Сначала подтвердите родство и создайте приглашение")
    if invitation.status == "pending":
        invitation.status = status
        invitation.accepted_by_user_id = current.id if status == "accepted" else None
        invitation.updated_at = utcnow()
        person.invitation_status = status
    db.commit()
    return invitation_out(db, invitation)


