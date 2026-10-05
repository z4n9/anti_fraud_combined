from typing import Literal
from pydantic import BaseModel
from app.domain.schemas import InputModel
from app.domain.risk_schemas import RiskCheckOut


class DecisionIn(InputModel):
    """No mutable transfer parameters are accepted by a decision endpoint."""
    pass


class TransferRequestOut(BaseModel):
    id: int
    status: Literal["completed", "pending_approval", "blocked_no_trusted", "rejected", "cancelled", "expired"]
    sender_name: str
    recipient_name: str
    recipient: str
    amount: float
    message: str
    created_at: str
    expires_at: str
    decided_at: str | None
    risk: RiskCheckOut
    needs_approval: bool
    can_approve: bool
    can_cancel: bool
