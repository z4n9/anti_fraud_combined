"""Account-authenticated staff review of frozen bank events, never arbitrary debits."""
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.core.analyst_security import CurrentAnalyst
from app.core.database import get_db
from app.domain.approval_schemas import BankDecisionIn, TransferRequestOut
from app.services.transfer_approval_service import bank_decide, bank_events

router = APIRouter(prefix="/api/analyst/bank-events", tags=["Банковская проверка"])
DB = Annotated[Session, Depends(get_db)]


@router.get("")
def events(db: DB, current: CurrentAnalyst,
           status: Literal["pending_approval", "bank_review", "completed", "blocked_no_trusted", "rejected", "cancelled", "expired"] | None = None,
           page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
           sender_name: str | None = Query(default=None, max_length=100)):
    return bank_events(db, current, status=status, page=page, page_size=page_size, sender_name=sender_name)


@router.get("/{request_id}", response_model=TransferRequestOut)
def event(request_id: int, db: DB, current: CurrentAnalyst):
    return bank_events(db, current, request_id=request_id)


@router.post("/{request_id}/approve", response_model=TransferRequestOut)
def approve(request_id: int, data: BankDecisionIn, db: DB, current: CurrentAnalyst):
    return bank_decide(db, current, request_id, "approve", data.note)


@router.post("/{request_id}/reject", response_model=TransferRequestOut)
def reject(request_id: int, data: BankDecisionIn, db: DB, current: CurrentAnalyst):
    return bank_decide(db, current, request_id, "reject", data.note)
