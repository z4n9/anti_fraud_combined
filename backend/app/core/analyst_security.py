"""Analyst permissions are stored server-side; client headers cannot grant them."""
from typing import Annotated
from fastapi import Depends, HTTPException
from app.core.security import CurrentAccount
from app.domain.models import User


def require_analyst(account: CurrentAccount):
    if account.role != "analyst":
        raise HTTPException(403, "Кабинет доступен только риск-аналитику")
    return account


CurrentAnalyst = Annotated[User, Depends(require_analyst)]
