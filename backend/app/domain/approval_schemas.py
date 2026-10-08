from typing import Literal
from pydantic import BaseModel, Field
from app.domain.schemas import InputModel
from app.domain.risk_schemas import RiskCheckOut


class DecisionIn(InputModel):
    """No mutable transfer parameters are accepted by a decision endpoint."""
    pass


class TransferRequestOut(BaseModel):
    id: int
    status: Literal["completed", "pending_approval", "bank_review", "blocked_no_trusted", "rejected", "cancelled", "expired"]
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
    participants: list[dict] = []
    my_vote: str | None = None
    anti_scam: dict = {}
    bank_review_reason: str | None = None
    bank_decisions: list[dict] = []


class BankDecisionIn(InputModel):
    note: str = Field(min_length=5, max_length=500)
