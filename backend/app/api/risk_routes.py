"""Authenticated read-only risk preview; execution policy is introduced separately."""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import CurrentUser
from app.domain.risk_schemas import RiskCheckIn, RiskCheckOut
from app.services.fraud_detector import FraudDetector

router = APIRouter(tags=["Проверка риска"])
DB = Annotated[Session, Depends(get_db)]


@router.post("/api/transfers/risk-check", response_model=RiskCheckOut)
def risk_check(data: RiskCheckIn, db: DB, current: CurrentUser):
    return FraudDetector().evaluate_transfer(db, current, data.recipient, data.amount)
