from typing import Literal
from pydantic import BaseModel
from app.domain.schemas import InputModel


class ProtectionChangeIn(InputModel):
    action: Literal["disable", "remove"]
    invitation_id: int | None = None


class ProtectionCancelIn(InputModel):
    pass


class ProtectionChangeOut(BaseModel):
    id: int
    action: Literal["disable", "remove"]
    status: Literal["pending", "cancelled", "executed"]
    created_at: str
    effective_at: str
    decided_at: str | None
    can_cancel: bool
    invitation_id: int | None = None
