"""Family consent lifecycle. Only the authenticated invitee can give consent."""
from datetime import timezone
from fastapi import HTTPException
from sqlalchemy import select, text
from app.services.mock_egov import verify_relationship
from app.domain.models import TrustedInvitation, TrustedPerson, User, utcnow
from app.services.protection_changes import lock_after_due

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
            "active": invitation.active, "relationship_verified": invitation.relationship_verified,
            "created_at": invitation.created_at.replace(tzinfo=timezone.utc).isoformat(),
            "updated_at": invitation.updated_at.replace(tzinfo=timezone.utc).isoformat()}


def active_invitations(db, user_id):
    return list(db.scalars(select(TrustedInvitation).where(TrustedInvitation.owner_user_id == user_id,
        TrustedInvitation.active.is_(True)).order_by(TrustedInvitation.id)))


def sync_summary(db, user_id):
    person = get_trusted(db, user_id)
    invites = active_invitations(db, user_id)
    accepted = [item for item in invites if item.status == "accepted"]
    chosen = accepted[0] if accepted else invites[-1] if invites else None
    if chosen is None:
        person.name = person.phone = person.relationship = ""
        person.verified = person.relationship_verified = False
        person.invitation_status = person.trusted_person_test_iin = person.current_invitation_id = None
    else:
        account = db.get(User, chosen.recipient_user_id)
        person.name = chosen.trusted_person_name
        person.phone = account.phone if account else ""
        person.relationship = chosen.relationship
        person.verified = person.relationship_verified = chosen.relationship_verified
        person.invitation_status = chosen.status
        person.trusted_person_test_iin = chosen.trusted_person_test_iin
        person.current_invitation_id = chosen.id
    return person


def respond(db, invitation_id, status, current):
    lock_after_due(db)
    invitation = db.get(TrustedInvitation, invitation_id)
    if invitation is None:
        raise HTTPException(404, "Приглашение не найдено")
    if invitation.recipient_user_id != current.id or invitation.trusted_person_test_iin != current.test_iin:
        raise HTTPException(403, "Ответить может только приглашённый родственник")
    person = get_trusted(db, invitation.owner_user_id)
    if not invitation.active:
        raise HTTPException(409, "Это доверенное лицо удалено")
    if invitation.status != "pending" and invitation.status != status:
        raise HTTPException(409, "Ответ уже сохранён. Для изменения создайте новое приглашение.")
    if not invitation.relationship_verified:
        raise HTTPException(409, "Сначала подтвердите родство и создайте приглашение")
    if invitation.status == "pending":
        invitation.status = status
        invitation.accepted_by_user_id = current.id if status == "accepted" else None
        invitation.updated_at = utcnow()
        sync_summary(db, invitation.owner_user_id)
    db.commit()
    return invitation_out(db, invitation)


