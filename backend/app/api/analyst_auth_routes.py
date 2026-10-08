"""The shared login establishes a session; this route verifies analyst access."""
from fastapi import APIRouter
from app.core.analyst_security import CurrentAnalyst

router = APIRouter(prefix="/api/analyst", tags=["Доступ аналитика"])


@router.get("/me")
def analyst_me(current: CurrentAnalyst):
    return {"id": current.id, "name": current.name, "role": "analyst"}
