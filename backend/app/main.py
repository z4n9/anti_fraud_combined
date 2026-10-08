"""Local teaching app. Account-scoped endpoints; no real banking."""
from contextlib import AsyncExitStack, asynccontextmanager
import logging
import sqlite3
from pathlib import Path
from typing import Annotated
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.deployment import deployment_settings
from app.seed import seed_data
from app.core.analyst_assets import AnalystAssets
from app.risk_ledger.runtime import analyst_lifespan
from app.risk_ledger.api.routes import ApiError, router as analyst_router
from app.risk_ledger.core.config import (
    DEFAULT_DICTIONARY_PATH, DEFAULT_MODEL_REGISTRY_DIR, DEFAULT_SESSION_DIR,
)

from app.api import analyst_auth_routes, auth_routes, bank_event_routes, bank_routes, family_routes, risk_routes, transfer_routes

@asynccontextmanager
async def lifespan(app):
    deployment_settings()  # Fail before seeding if deployment configuration is invalid.
    seed_data()
    test_root = getattr(app.state, "analyst_runtime_root", None)
    async with AsyncExitStack() as stack:
        try:
            await stack.enter_async_context(analyst_lifespan(
                app,
                session_dir=Path(test_root) / "sessions" if test_root else DEFAULT_SESSION_DIR,
                model_registry_dir=Path(test_root) / "model-registry" if test_root else DEFAULT_MODEL_REGISTRY_DIR,
                dictionary_path=Path(test_root) / "semantic_dictionary.json" if test_root else DEFAULT_DICTIONARY_PATH,
            ))
        except (OSError, sqlite3.DatabaseError, ValueError, RuntimeError):
            logging.getLogger(__name__).exception("Analyst storage could not initialize")
            app.state.analysis_manager = None
            app.state.model_registry = None
            app.state.model_error = "Аналитическое хранилище временно недоступно."
        yield


app = FastAPI(title="AMAN Bank — объединённый учебный API", lifespan=lifespan)
ANALYST_INDEX_PATH = Path(__file__).resolve().parents[2] / "frontend" / "analyst" / "dist" / "index.html"

@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/analyst/"):
        return JSONResponse(status_code=422, content={"error": {
            "code": "request_validation_error", "message": "Проверьте параметры запроса.",
            "details": [".".join(str(part) for part in error["loc"]) for error in exc.errors()],
        }})
    fields = [str(error["loc"][-1]) for error in exc.errors()]
    message = "Введите корректную сумму: больше 0, не более 2 знаков после запятой" if "amount" in fields else "Проверьте заполненные поля: " + ", ".join(fields)
    return JSONResponse(status_code=422, content={"detail": message})


@app.exception_handler(ApiError)
async def analyst_error(request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status_code, content={"error": {
        "code": exc.code, "message": exc.message, "details": exc.details,
    }})


@app.exception_handler(OperationalError)
async def database_busy(request: Request, exc: OperationalError):
    return JSONResponse(status_code=503, content={"detail": "База временно недоступна. Повторите попытку."})


@app.middleware("http")
async def same_origin_mutations(request: Request, call_next):
    settings = deployment_settings()
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        expected_origin = settings.public_origin or str(request.base_url).rstrip("/")
        if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin != expected_origin):
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
               transfer_routes.router, risk_routes.router, bank_event_routes.router, analyst_auth_routes.router,
               analyst_router):
    app.include_router(router)


@app.get("/api/readiness", include_in_schema=False)
def readiness(db: Annotated[Session, Depends(get_db)]):
    """The contest deployment requires both storage and the built analyst UI."""
    db.connection().exec_driver_sql("SELECT 1")
    ready = (getattr(app.state, "analysis_manager", None) is not None
             and getattr(app.state, "model_registry", None) is not None
             and ANALYST_INDEX_PATH.is_file())
    return JSONResponse(status_code=200 if ready else 503,
                        content={"status": "ok" if ready else "unavailable", "bank": "ready",
                                 "analyst": "ready" if ready else "unavailable"})

# Serve only the public AMAN assets. Database and application sources stay private.
app.mount("/analyst", AnalystAssets(
    directory=Path(__file__).resolve().parents[2] / "frontend" / "analyst" / "dist",
    html=True, check_dir=False,
), name="analyst")
app.mount("/", StaticFiles(directory=Path(__file__).resolve().parents[2] / "frontend" / "aman", html=True), name="frontend")
