"""Local teaching app. Account-scoped endpoints; no real banking."""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.seed import seed_data

from app.api import auth_routes, bank_routes, family_routes, risk_routes, transfer_routes

@asynccontextmanager
async def lifespan(app):
    seed_data()
    yield


app = FastAPI(title="AMAN Bank — объединённый учебный API", lifespan=lifespan)

@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    fields = [str(error["loc"][-1]) for error in exc.errors()]
    message = "Введите корректную сумму: больше 0, не более 2 знаков после запятой" if "amount" in fields else "Проверьте заполненные поля: " + ", ".join(fields)
    return JSONResponse(status_code=422, content={"detail": message})


@app.exception_handler(OperationalError)
async def database_busy(request: Request, exc: OperationalError):
    return JSONResponse(status_code=503, content={"detail": "База временно недоступна. Повторите попытку."})


@app.middleware("http")
async def same_origin_mutations(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin != str(request.base_url).rstrip("/")):
            return JSONResponse(status_code=403, content={"detail": "Запрос с другого сайта запрещён"})
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
def health(db: Annotated[Session, Depends(get_db)]):
    db.connection().exec_driver_sql("SELECT 1")
    return {"status": "ok", "bank": "ready",
            "analysis": {"status": "ready", "engine_mode": "rule_based_fallback",
                         "model_available": False,
                         "profiles": ["transaction_rules", "transaction_graph"],
                         "advisory_only": False,
                         "transfer_enforcement": True}}

for router in (auth_routes.router, bank_routes.router, family_routes.router,
               transfer_routes.router, risk_routes.router):
    app.include_router(router)

# Serve only the public AMAN assets. Database and application sources stay private.
app.mount("/", StaticFiles(directory=Path(__file__).resolve().parents[2] / "frontend" / "aman", html=True), name="frontend")
