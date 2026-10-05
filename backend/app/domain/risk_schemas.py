"""Public advisory risk contract; internal graph evidence never crosses this boundary."""
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.domain.schemas import InputModel

RiskLevel = Literal["low", "medium", "high", "critical"]


class RiskCheckIn(InputModel):
    recipient: Annotated[str, Field(min_length=1, max_length=100)]
    amount: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]


class RiskFactor(BaseModel):
    code: str
    label: str
    description: str
    strength: Annotated[float, Field(ge=0, le=1)]
    source: Literal["rule", "graph"]


class RiskComponent(BaseModel):
    score: Annotated[float, Field(ge=0, le=1)]
    level: RiskLevel
    factors: list[RiskFactor]


class DataUncertainty(BaseModel):
    level: Literal["low", "medium", "high"]
    reasons: list[str]
    sender_history_count: Annotated[int, Field(ge=0)]


class RiskCheckOut(BaseModel):
    engine_mode: Literal["rule_based_fallback"] = "rule_based_fallback"
    evaluated_at: str
    recipient_risk: RiskComponent
    transaction_risk: RiskComponent
    data_uncertainty: DataUncertainty
    overall_level: RiskLevel
    transfer_enforcement: bool = False
    advisory: bool = True
