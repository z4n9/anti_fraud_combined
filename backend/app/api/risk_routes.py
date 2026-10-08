"""Authenticated read-only risk preview; execution policy is introduced separately."""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import CurrentUser
from app.domain.risk_schemas import RiskCheckIn, RiskCheckOut
from app.services.fraud_detector import FraudDetector
from app.services.protection_changes import settle_due

router = APIRouter(tags=["Проверка риска"])
DB = Annotated[Session, Depends(get_db)]


@router.post("/api/transfers/risk-check", response_model=RiskCheckOut)
def risk_check(data: RiskCheckIn, db: DB, current: CurrentUser):
    settle_due(db, current.id)
    result = FraudDetector().evaluate_transfer(db, current, data.recipient, data.amount)
    if data.anti_scam is not None:
        result = result.model_copy(update={"anti_scam": data.anti_scam.model_dump()})
    return result
