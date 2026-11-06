"""Public advisory risk contract; internal graph evidence never crosses this boundary."""
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PrivateAttr

from app.domain.schemas import AntiScamIn, InputModel

RiskLevel = Literal["low", "medium", "high", "critical"]


class RiskCheckIn(InputModel):
    recipient: Annotated[str, Field(min_length=1, max_length=100)]
    amount: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]
    anti_scam: AntiScamIn | None = None


class RiskFactor(BaseModel):
    code: str
    label: str
    description: str
    strength: Annotated[float, Field(ge=0, le=1)]
    source: Literal["rule", "graph", "ml"]


class RiskComponent(BaseModel):
    score: Annotated[float, Field(ge=0, le=1)]
    level: RiskLevel
    factors: list[RiskFactor]


class DataUncertainty(BaseModel):
    level: Literal["low", "medium", "high"]
    reasons: list[str]
    sender_history_count: Annotated[int, Field(ge=0)]


class PublicMLAssessment(BaseModel):
    status: Literal["available", "unavailable", "disabled"] = "disabled"
    model_id: str | None = None
    model_version: str | None = None
    decision: Literal["ALLOW", "REVIEW", "HIGH_RISK"] | None = None
    out_of_distribution: bool = False
    currency: Literal["KZT"] | None = None
    model_currency: Literal["KZT"] | None = None
    unit_version: str | None = None


class RiskCheckOut(BaseModel):
    _ml_audit: dict = PrivateAttr(default_factory=dict)
    family_protection_active: bool = False
    requires_risk_acknowledgement: bool = False
    engine_mode: Literal["rule_based_fallback", "hybrid_ml_rules"] = "rule_based_fallback"
    ml: PublicMLAssessment = Field(default_factory=PublicMLAssessment)
    evaluated_at: str
    recipient_risk: RiskComponent
    transaction_risk: RiskComponent
    data_uncertainty: DataUncertainty
    overall_level: RiskLevel
    transfer_enforcement: bool = False
    advisory: bool = True
    anti_scam: dict = Field(default_factory=lambda: {"pressure": None, "secrecy": None, "stranger": None})
