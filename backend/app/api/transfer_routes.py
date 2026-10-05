"""Bank commands with risk enforcement and account-scoped family decisions."""
from typing import Annotated
from fastapi import APIRouter, Body, Depends, Header
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import CurrentUser
from app.domain.schemas import TransferIn, TransferOut
from app.services.bank_service import execute_transfer
from app.domain.approval_schemas import DecisionIn, TransferRequestOut
from app.services.transfer_approval_service import decide_request, list_requests

router = APIRouter(tags=["Переводы"])
DB = Annotated[Session, Depends(get_db)]

@router.post("/api/transfers", response_model=TransferOut)
def transfer(data: TransferIn, db: DB, current: CurrentUser,
             idempotency_key: Annotated[str, Header(min_length=1, max_length=100)]):
    return execute_transfer(data, db, current, idempotency_key)


@router.get("/api/transfers/requests", response_model=list[TransferRequestOut])
def own_requests(db: DB, current: CurrentUser):
    return list_requests(db, current)


@router.get("/api/transfers/pending-requests", response_model=list[TransferRequestOut])
def pending_requests(db: DB, current: CurrentUser):
    return list_requests(db, current, relatives=True)


@router.post("/api/transfers/requests/{request_id}/approve", response_model=TransferRequestOut)
def approve_request(request_id: int, db: DB, current: CurrentUser, data: DecisionIn | None = Body(default=None)):
    return decide_request(db, current, request_id, "approve")


@router.post("/api/transfers/requests/{request_id}/reject", response_model=TransferRequestOut)
def reject_request(request_id: int, db: DB, current: CurrentUser, data: DecisionIn | None = Body(default=None)):
    return decide_request(db, current, request_id, "reject")


@router.post("/api/transfers/requests/{request_id}/cancel", response_model=TransferRequestOut)
def cancel_request(request_id: int, db: DB, current: CurrentUser, data: DecisionIn | None = Body(default=None)):
    return decide_request(db, current, request_id, "cancel")
