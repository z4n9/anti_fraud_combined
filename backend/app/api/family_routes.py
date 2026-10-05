"""Family consent lifecycle. Only the authenticated invitee can give consent."""
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import CurrentUser
from app.services.mock_egov import verify_relationship
from app.domain.models import TrustedInvitation, TrustedPerson, User
from app.domain.schemas import (InvitationIn, InvitationOut, RelationshipOut, VerifyRelationshipIn, ProtectionSettingsIn, TrustedPersonIn, TrustedPersonOut)

from app.services.family_service import get_trusted, invitation_out, respond, trusted_out

router = APIRouter(prefix="/api", tags=["Mock eGov и семейные приглашения"])
DB = Annotated[Session, Depends(get_db)]

@router.post("/mock-egov/verify-relationship", response_model=RelationshipOut, response_model_exclude_none=True)
def check_relationship(data: VerifyRelationshipIn, db: DB, current: CurrentUser):
    # No directory/list endpoint. No birth dates or other relatives are returned.
    if data.client_iin is not None and data.client_iin != current.test_iin:
        raise HTTPException(403, "Можно проверять родство только для своего аккаунта")
    return verify_relationship(db, current.test_iin, data.trusted_iin)


@router.post("/trusted-invitations", response_model=InvitationOut)
def create_invitation(data: InvitationIn, db: DB, current: CurrentUser):
    db.execute(text("BEGIN IMMEDIATE"))  # Serialize concurrent invite/reply changes.
    owner = current
    if owner is None or not owner.test_iin:
        raise HTTPException(409, "Пользователь не связан с Mock eGov")
    result = verify_relationship(db, owner.test_iin, data.trusted_iin)
    if not result["verified"]:
        reason = "Тестовый гражданин не найден" if result["reason"] == "citizen_not_found" else "Родство не подтверждено"
        raise HTTPException(400, reason)
    person = get_trusted(db, current.id)
    current = db.get(TrustedInvitation, person.current_invitation_id) if person.current_invitation_id else None
    if current and current.trusted_person_test_iin == data.trusted_iin:
        if current.status == "pending":
            db.commit()
            return invitation_out(db, current)  # Double click / network retry.
        if current.status == "accepted":
            raise HTTPException(409, "Этот родственник уже принял приглашение")
    recipient = db.scalar(select(User).where(User.test_iin == data.trusted_iin))
    if recipient is None:
        raise HTTPException(409, "У родственника ещё нет аккаунта AMAN Bank")
    trusted = result["trusted_person"]
    invitation = TrustedInvitation(owner_user_id=owner.id, recipient_user_id=recipient.id, trusted_person_name=trusted["full_name"],
                                   trusted_person_test_iin=data.trusted_iin, relationship=result["relationship"],
                                   status="pending")
    db.add(invitation)
    db.flush()
    person.name = trusted["full_name"]
    person.phone = trusted["phone"]
    person.relationship = result["relationship"]
    person.verified = True
    person.relationship_verified = True
    person.trusted_person_test_iin = data.trusted_iin
    person.invitation_status = "pending"
    person.current_invitation_id = invitation.id
    # Preserve the user's enabled/disabled preference; pending never means ready.
    db.commit()
    return invitation_out(db, invitation)


@router.get("/trusted-invitations/current", response_model=InvitationOut | None)
def current_invitation(db: DB, current: CurrentUser):
    person = get_trusted(db, current.id)
    if not person.current_invitation_id:
        return None
    invitation = db.get(TrustedInvitation, person.current_invitation_id)
    if invitation is None or invitation.owner_user_id != current.id:
        return None
    return invitation_out(db, invitation)


@router.get("/trusted-invitations/incoming", response_model=list[InvitationOut])
def incoming_invitations(db: DB, current: CurrentUser):
    rows = db.scalars(select(TrustedInvitation).join(TrustedPerson, TrustedPerson.current_invitation_id == TrustedInvitation.id)
                      .where(TrustedInvitation.recipient_user_id == current.id)
                      .order_by(TrustedInvitation.created_at.desc(), TrustedInvitation.id.desc()))
    return [invitation_out(db, invitation) for invitation in rows]


@router.post("/trusted-invitations/{invitation_id}/accept", response_model=InvitationOut)
def accept(invitation_id: int, db: DB, current: CurrentUser):
    return respond(db, invitation_id, "accepted", current)


@router.post("/trusted-invitations/{invitation_id}/reject", response_model=InvitationOut)
def reject(invitation_id: int, db: DB, current: CurrentUser):
    return respond(db, invitation_id, "rejected", current)


@router.get("/trusted-person", response_model=TrustedPersonOut)
def trusted_person(db: DB, current: CurrentUser):
    return trusted_out(get_trusted(db, current.id))


@router.put("/trusted-person", response_model=TrustedPersonOut)
def update_trusted_person(data: TrustedPersonIn, db: DB, current: CurrentUser):
    raise HTTPException(409, "Выберите родственника через Mock eGov и отправьте приглашение")


@router.put("/protection-settings", response_model=TrustedPersonOut)
def protection_settings(data: ProtectionSettingsIn, db: DB, current: CurrentUser):
    person = get_trusted(db, current.id)
    for key, value in data.model_dump().items():
        setattr(person, key, value)
    db.commit()
    return trusted_out(person)


