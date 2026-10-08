"""Family consent lifecycle. Only the authenticated invitee can give consent."""
from typing import Annotated
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import CurrentUser
from app.services.mock_egov import verify_relationship
from app.domain.models import TrustedInvitation, TrustedPerson, User
from app.domain.schemas import (InvitationIn, InvitationOut, RelationshipOut, VerifyRelationshipIn, ProtectionSettingsIn, TrustedPersonIn, TrustedPersonOut)

from app.services.family_service import active_invitations, get_trusted, invitation_out, respond, sync_summary, trusted_out
from app.domain.protection_schemas import ProtectionCancelIn, ProtectionChangeIn, ProtectionChangeOut
from app.services.protection_changes import cancel_change, list_changes, lock_after_due, request_change, schedule_change, settle_due

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
    owner_id = current.id
    lock_after_due(db, owner_id)
    owner = current
    if owner is None or not owner.test_iin:
        raise HTTPException(409, "Пользователь не связан с Mock eGov")
    result = verify_relationship(db, owner.test_iin, data.trusted_iin)
    if not result["verified"]:
        reason = "Тестовый гражданин не найден" if result["reason"] == "citizen_not_found" else "Родство не подтверждено"
        raise HTTPException(400, reason)
    person = get_trusted(db, current.id)
    invites = active_invitations(db, owner_id)
    previous = next((item for item in invites if item.trusted_person_test_iin == data.trusted_iin), None)
    if previous:
        if previous.status == "pending":
            db.commit()
            return invitation_out(db, previous)
        if previous.status == "accepted":
            raise HTTPException(409, "Этот родственник уже принял приглашение")
        previous.active = False
    if len([item for item in invites if item.status in {"pending", "accepted"}]) >= 3:
        raise HTTPException(409, "Можно назначить не более трёх доверенных родственников")
    recipient = db.scalar(select(User).where(User.test_iin == data.trusted_iin))
    if recipient is None:
        raise HTTPException(409, "У родственника ещё нет аккаунта AMAN Bank")
    trusted = result["trusted_person"]
    invitation = TrustedInvitation(owner_user_id=owner.id, recipient_user_id=recipient.id, trusted_person_name=trusted["full_name"],
                                   trusted_person_test_iin=data.trusted_iin, relationship=result["relationship"],
                                   status="pending", active=True, relationship_verified=True)
    db.add(invitation)
    db.flush()
    sync_summary(db, owner_id)
    # Preserve the user's enabled/disabled preference; pending never means ready.
    db.commit()
    return invitation_out(db, invitation)


@router.get("/trusted-invitations", response_model=list[InvitationOut])
def own_invitations(db: DB, current: CurrentUser):
    settle_due(db, current.id)
    rows = db.scalars(select(TrustedInvitation).where(TrustedInvitation.owner_user_id == current.id)
                      .order_by(TrustedInvitation.id.desc()))
    return [invitation_out(db, invitation) for invitation in rows]


@router.get("/trusted-invitations/current", response_model=InvitationOut | None)
def current_invitation(db: DB, current: CurrentUser):
    settle_due(db, current.id)
    person = get_trusted(db, current.id)
    if not person.current_invitation_id:
        return None
    invitation = db.get(TrustedInvitation, person.current_invitation_id)
    if invitation is None or invitation.owner_user_id != current.id:
        return None
    return invitation_out(db, invitation)


@router.get("/trusted-invitations/incoming", response_model=list[InvitationOut])
def incoming_invitations(db: DB, current: CurrentUser):
    settle_due(db)
    rows = db.scalars(select(TrustedInvitation)
                      .where(TrustedInvitation.recipient_user_id == current.id, TrustedInvitation.active.is_(True))
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
    settle_due(db, current.id)
    return trusted_out(get_trusted(db, current.id))


@router.put("/trusted-person", response_model=TrustedPersonOut)
def update_trusted_person(data: TrustedPersonIn, db: DB, current: CurrentUser):
    raise HTTPException(409, "Выберите родственника через Mock eGov и отправьте приглашение")


@router.put("/protection-settings", response_model=TrustedPersonOut)
def protection_settings(data: ProtectionSettingsIn, db: DB, current: CurrentUser):
    user_id = current.id
    lock_after_due(db, user_id)
    person = get_trusted(db, current.id)
    if not data.confirmation_enabled and (person.protection_active or data.protection_active):
        raise HTTPException(409, "При активной семейной защите подтверждение отключить нельзя")
    person.notifications_enabled = data.notifications_enabled
    if person.protection_active and not data.protection_active:
        schedule_change(db, user_id, "disable")
        person.confirmation_enabled = True
    else:
        person.protection_active = data.protection_active
        person.confirmation_enabled = data.confirmation_enabled
    db.commit()
    return trusted_out(person)


@router.get("/protection-change-requests", response_model=list[ProtectionChangeOut])
def protection_changes(db: DB, current: CurrentUser):
    return list_changes(db, current)


@router.post("/protection-change-requests", response_model=ProtectionChangeOut)
def create_protection_change(data: ProtectionChangeIn, db: DB, current: CurrentUser):
    return request_change(db, current, data.action, data.invitation_id)


@router.post("/protection-change-requests/{request_id}/cancel", response_model=ProtectionChangeOut)
def cancel_protection_change(request_id: int, db: DB, current: CurrentUser,
                             data: ProtectionCancelIn | None = Body(default=None)):
    return cancel_change(db, current, request_id)


