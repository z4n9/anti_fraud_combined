from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.risk_ledger.core.config import MAX_INPUT_BYTES, MAX_INPUT_RECORDS
from app.risk_ledger.core.model_loader import load_artifacts
from app.risk_ledger.domain.contracts import (
    AnalysisPlanContract,
    AnalysisResultContract,
    AnalysisState,
    SemanticMappingContract,
    SourceFormat,
    SourceInventoryContract,
    TimeRange,
    PlanProfileState,
)
from app.risk_ledger.domain.canonical_schema import AnalysisProfile
from app.core.analyst_security import CurrentAnalyst
from app.risk_ledger.domain.models import ModelManifest
from app.risk_ledger.services.csv_reader import CsvFormatError, read_csv
from app.risk_ledger.services.adapter_registry import DEFAULT_SOURCE_ADAPTERS
from app.risk_ledger.services.analysis_planner import AnalysisPlanner
from app.risk_ledger.services.analysis_orchestrator import (
    CancellationToken,
    ClientRiskProfileRunner,
    TransactionAnomalyProfileRunner,
    UniversalAnalysisOrchestrator,
)
from app.risk_ledger.services.canonical_materializer import materialize_profiles
from app.risk_ledger.services.export_service import iter_csv_records
from app.risk_ledger.services.metrics import calculate_metrics
from app.risk_ledger.services.predictor import predict_frame
from app.risk_ledger.services.schema_validator import validate_schema
from app.risk_ledger.services.session_store import SessionNotFoundError, SessionStore
from app.risk_ledger.services.semantic_dictionary import SemanticDictionary
from app.risk_ledger.services.semantic_mapper import SemanticSchemaMapper
from app.risk_ledger.services.source_adapters import SourceAdapterError, SourceRecordLimitError
from app.risk_ledger.services.source_upload import (
    UploadValidationError,
    cleanup_staging_root,
    detect_source_format,
    prepare_staged_upload,
    remove_staged_upload,
    safe_filename,
    validate_staged_signature,
)
from app.risk_ledger.services.model_registry import ModelProfile, ModelRegistry, ModelRegistryError
from app.risk_ledger.services.transaction_model import load_transaction_artifacts


AnalysisStatus = Literal[
    "queued",
    "validating",
    "predicting",
    "completed",
    "failed",
    "cancel_requested",
    "cancelled",
    "planned",
]
logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    model_ready: bool
    rules_ready: bool = False


class ModelStatusResponse(BaseModel):
    ready: bool
    version: str | None = None
    features: int | None = None
    review_threshold: float | None = None
    error: str | None = None
    engine_mode: str = "rule_based_fallback"
    client_model_available: bool = False
    transaction_model_available: bool = False


class CreateAnalysisResponse(BaseModel):
    analysis_id: str
    status: Literal["queued"]
    source_format: SourceFormat
    status_url: str


class AnalysisStatusResponse(BaseModel):
    analysis_id: str
    filename: str
    status: AnalysisStatus
    progress: int
    stage: str
    source_format: SourceFormat
    received_bytes: int
    can_cancel: bool
    warnings: list[str]
    errors: list[str]


class CancelAnalysisResponse(BaseModel):
    analysis_id: str
    status: Literal["cancel_requested", "cancelled"]


class AnalysisSummaryResponse(BaseModel):
    analysis_id: str
    model_version: str
    threshold: float
    summary: dict[str, Any]
    metrics: dict[str, Any]


class AnalysisResultsResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
    threshold: float


class ProbabilityBinResponse(BaseModel):
    from_: float = Field(alias="from")
    to: float
    count: int


class RiskDistributionResponse(BaseModel):
    analysis_id: str
    threshold: float
    risk_counts: dict[str, int]
    probability_histogram: list[ProbabilityBinResponse]


class PlanPeriodPatch(BaseModel):
    start: str | None = None
    end: str | None = None


class FeedbackPatch(BaseModel):
    status: Literal["new", "in_review", "confirmed", "dismissed"]
    comment: str = Field(default="", max_length=1000)


class CandidateRegistration(BaseModel):
    profile: Literal["client_risk", "transaction_anomaly"]
    version: str = Field(min_length=1, max_length=80)
    evaluation: dict[str, Any]


class ModelDeploymentRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)
    confirmation: str = Field(min_length=1, max_length=120)


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []


@dataclass(slots=True)
class AnalysisJob:
    analysis_id: str
    filename: str
    source_format: SourceFormat
    upload_path: Path
    received_bytes: int
    owner_user_id: int | None = None
    status: AnalysisStatus = "queued"
    progress: int = 0
    stage: str = "uploaded"
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    inventory: SourceInventoryContract | None = None
    mapping: SemanticMappingContract | None = None
    plan: AnalysisPlanContract | None = None
    result: AnalysisResultContract | None = None
    orchestration_cancel: CancellationToken | None = field(default=None, repr=False)

    def public_status(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "filename": self.filename,
            "status": self.status,
            "progress": self.progress,
            "stage": self.stage,
            "source_format": self.source_format,
            "received_bytes": self.received_bytes,
            "can_cancel": self.status
            in {"queued", "planned", "validating", "predicting", "cancel_requested"},
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


class AnalysisCancelled(Exception):
    pass


class AnalysisManager:
    def __init__(
        self,
        model: Any | None,
        manifest: ModelManifest | None,
        store: SessionStore,
        incoming_dir: Path,
        *,
        max_input_records: int = MAX_INPUT_RECORDS,
        semantic_dictionary: SemanticDictionary | None = None,
        transaction_artifact_dir: str | Path | None = None,
    ) -> None:
        self.model = model
        self.manifest = manifest
        self.store = store
        self.incoming_dir = incoming_dir
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.max_input_records = int(max_input_records)
        self.semantic_mapper = SemanticSchemaMapper(semantic_dictionary)
        self.analysis_planner = AnalysisPlanner()
        self.transaction_artifact_dir = (
            Path(transaction_artifact_dir) if transaction_artifact_dir is not None else None
        )
        self.jobs: dict[str, AnalysisJob] = {}
        self.tasks: set[asyncio.Task[Any]] = set()
        self._lock = threading.Lock()

    def cleanup_startup(self) -> list[str]:
        deleted = self.store.cleanup_all()
        cleanup_staging_root(self.incoming_dir)
        return deleted

    def add_job(
        self,
        analysis_id: str,
        filename: str,
        source_format: SourceFormat,
        upload_path: Path,
        received_bytes: int,
        owner_user_id: int | None = None,
    ) -> AnalysisJob:
        job = AnalysisJob(
            analysis_id=analysis_id,
            filename=filename,
            source_format=source_format,
            upload_path=upload_path,
            received_bytes=received_bytes,
            owner_user_id=owner_user_id,
        )
        with self._lock:
            self.jobs[analysis_id] = job
        return job

    def get_job(self, analysis_id: str) -> AnalysisJob:
        with self._lock:
            job = self.jobs.get(analysis_id)
        if job is None:
            raise ApiError(404, "analysis_not_found", "Analysis session was not found.")
        return job

    def _build_plan(self, inventory, mapping):
        plan = self.analysis_planner.build(inventory, mapping)
        if self.model is None or self.manifest is None:
            profiles = tuple(profile.model_copy(update={"state": PlanProfileState.BLOCKED,
                "blocking_reasons": ("Клиентская модель отсутствует. Вероятность клиентского риска не вычисляется.",)})
                if profile.profile == AnalysisProfile.CLIENT_RISK and profile.state == PlanProfileState.PLANNED
                else profile for profile in plan.profiles)
            plan = plan.model_copy(update={"profiles": profiles,
                "warnings": tuple(dict.fromkeys((*plan.warnings,
                    "ML-модель клиента недоступна; транзакционные правила продолжают работать.")))})
        return plan

    def _update(self, analysis_id: str, **changes: Any) -> None:
        with self._lock:
            job = self.jobs[analysis_id]
            for key, value in changes.items():
                setattr(job, key, value)

    def _raise_if_cancelled(self, analysis_id: str) -> None:
        if self.get_job(analysis_id).cancel_event.is_set():
            raise AnalysisCancelled

    def request_cancel(self, analysis_id: str) -> AnalysisJob:
        cancel_prepared = False
        with self._lock:
            job = self.jobs.get(analysis_id)
            if job is None:
                raise ApiError(404, "analysis_not_found", "Analysis session was not found.")
            if job.status == "cancelled":
                return job
            if job.status in {"completed", "failed"}:
                raise ApiError(
                    409,
                    "analysis_not_running",
                    "Analysis is not running and cannot be cancelled.",
                )
            if job.status == "planned":
                job.cancel_event.set()
                job.status = "cancelled"
                job.stage = "cancelled"
                cancel_prepared = True
            else:
                job.cancel_event.set()
                if job.orchestration_cancel is not None:
                    job.orchestration_cancel.cancel()
                job.status = "cancel_requested"
                job.stage = "cancel_requested"
        if cancel_prepared:
            remove_staged_upload(self.incoming_dir, analysis_id)
        return job

    def cleanup_expired(self) -> list[str]:
        deleted = self.store.cleanup_expired()
        if deleted:
            with self._lock:
                for analysis_id in deleted:
                    job = self.jobs.get(analysis_id)
                    if job is not None and job.status in {
                        "completed",
                        "failed",
                        "cancelled",
                    }:
                        self.jobs.pop(analysis_id, None)
        return deleted

    def inspect_only(self, analysis_id: str, upload_path: Path) -> None:
        terminal: dict[str, Any] = {
            "status": "failed",
            "progress": 100,
            "stage": "failed",
            "errors": ["Не удалось подготовить план анализа."],
        }
        keep_upload = False
        try:
            job = self.get_job(analysis_id)
            self._update(analysis_id, status="validating", progress=5, stage="inspecting")
            adapter = DEFAULT_SOURCE_ADAPTERS.get(job.source_format)
            inventory = adapter.inspect(
                upload_path,
                analysis_id=analysis_id,
                filename=job.filename,
                source_format=job.source_format,
                max_records=self.max_input_records,
                cancel_check=lambda: self._raise_if_cancelled(analysis_id),
            )
            self._update(analysis_id, inventory=inventory, progress=20, stage="mapping")
            mapping = self.semantic_mapper.map(inventory)
            self._update(analysis_id, mapping=mapping, progress=35, stage="planning")
            plan = self._build_plan(inventory, mapping)
            terminal = {
                "status": "planned",
                "progress": 45,
                "stage": "planning",
                "plan": plan,
                "warnings": list(plan.warnings),
                "errors": [],
            }
            keep_upload = True
        except AnalysisCancelled:
            terminal = {
                "status": "cancelled",
                "stage": "cancelled",
                "warnings": ["Анализ отменён пользователем."],
                "errors": [],
            }
        except (SourceAdapterError, SourceRecordLimitError, ValueError, OSError) as error:
            terminal["errors"] = [str(error)]
        finally:
            if not keep_upload:
                try:
                    remove_staged_upload(self.incoming_dir, analysis_id)
                except OSError:
                    logger.exception("Could not remove staged source for %s", analysis_id)
            self._update(analysis_id, **terminal)

    def run_universal(self, analysis_id: str) -> None:
        job = self.get_job(analysis_id)
        if job.plan is None or job.mapping is None or job.inventory is None:
            raise ApiError(409, "analysis_not_planned", "Analysis plan is not ready.")
        token = CancellationToken()
        self._update(
            analysis_id,
            status="validating",
            stage="validating",
            progress=max(job.progress, 46),
            orchestration_cancel=token,
        )
        try:
            materialized = materialize_profiles(
                job.upload_path,
                job.source_format,
                job.mapping,
                job.plan,
                job.upload_path.parent,
                max_records=self.max_input_records,
                cancel_check=token.raise_if_cancelled,
            )
            runners: dict[Any, Any] = {}
            if materialized.client_frame is not None and self.model is not None and self.manifest is not None:
                from app.risk_ledger.domain.canonical_schema import AnalysisProfile

                runners[AnalysisProfile.CLIENT_RISK] = ClientRiskProfileRunner(
                    materialized.client_frame,
                    self.model,
                    self.manifest,
                )
            if materialized.transaction_path is not None:
                from app.risk_ledger.domain.canonical_schema import AnalysisProfile

                runners[AnalysisProfile.TRANSACTION_ANOMALY] = TransactionAnomalyProfileRunner(
                    materialized.transaction_path,
                    self.transaction_artifact_dir,
                    total_records=materialized.transaction_rows,
                )

            def observe(progress) -> None:
                # The orchestrator reaching 100% means scoring is finished, but the
                # API result is not ready until SQLite persistence has committed.
                # Keep the externally visible job non-terminal until that point.
                mapped_progress = min(99, 46 + round(progress.progress * 0.53))
                self._update(
                    analysis_id,
                    status="predicting",
                    progress=mapped_progress,
                    stage=progress.stage.value,
                    warnings=list(progress.warnings),
                    errors=list(progress.errors),
                )

            outcome = UniversalAnalysisOrchestrator(observer=observe).run(
                job.plan,
                runners,
                cancellation=token,
            )
            if outcome.cancelled:
                self._update(
                    analysis_id,
                    status="cancelled",
                    stage="cancelled",
                    orchestration_cancel=None,
                )
                return
            if outcome.result is None or outcome.result.state == "failed":
                self._update(
                    analysis_id,
                    status="failed",
                    stage="failed",
                    progress=100,
                    result=outcome.result,
                    orchestration_cancel=None,
                )
                return
            self.store.create_universal_session(
                session_id=analysis_id,
                client_records=outcome.client_records,
                transaction_records=outcome.transaction_records,
                relationships=outcome.relationships,
                result=outcome.result.model_dump(mode="json"),
                inventory=job.inventory.model_dump(mode="json"),
                mapping=job.mapping.model_dump(mode="json"),
                plan=job.plan.model_dump(mode="json"),
                threshold=self.manifest.review_threshold if self.manifest else 0.72,
            )
            self.store.set_owner(analysis_id, job.owner_user_id)
            self._update(
                analysis_id,
                status="completed",
                stage="ready",
                progress=100,
                result=outcome.result,
                orchestration_cancel=None,
            )
        except Exception as error:
            if token.is_cancelled:
                self._update(analysis_id, status="cancelled", stage="cancelled", errors=[])
            else:
                logger.exception("Universal analysis %s failed", analysis_id)
                self._update(
                    analysis_id,
                    status="failed",
                    stage="failed",
                    progress=100,
                    errors=[str(error)[:500] or "Внутренняя ошибка анализа."],
                )
            try:
                self.store.delete_session(analysis_id)
            except (SessionNotFoundError, OSError):
                pass
        finally:
            try:
                remove_staged_upload(self.incoming_dir, analysis_id)
            except OSError:
                logger.exception("Could not remove staged source for %s", analysis_id)

    def process(self, analysis_id: str, upload_path: Path) -> None:
        self.inspect_only(analysis_id, upload_path)
        job = self.get_job(analysis_id)
        if job.status != "planned":
            return
        transaction_planned = any(p.profile == AnalysisProfile.TRANSACTION_ANOMALY
                                  and p.state == PlanProfileState.PLANNED for p in job.plan.profiles)
        if self.model is not None and self.manifest is not None and job.source_format == SourceFormat.CSV and not transaction_planned:
            self._process_legacy(analysis_id, upload_path)
        else:
            self.run_universal(analysis_id)

    def _process_legacy(self, analysis_id: str, upload_path: Path) -> None:
        terminal: dict[str, Any] = {
            "status": "failed",
            "progress": 100,
            "stage": "failed",
            "errors": ["Внутренняя ошибка анализа. Повторите попытку."],
        }
        try:
            job = self.get_job(analysis_id)
            self._raise_if_cancelled(analysis_id)
            self._update(
                analysis_id, status="validating", progress=5, stage="inspecting"
            )
            if not DEFAULT_SOURCE_ADAPTERS.supports(job.source_format):
                self._raise_if_cancelled(analysis_id)
                terminal = {
                    "status": "failed",
                    "progress": 100,
                    "stage": "adapter_pending",
                    "errors": [
                        "Формат принят и безопасно проверен, но его адаптер будет "
                        "подключён на этапах D003–D004."
                    ],
                }
                return

            adapter = DEFAULT_SOURCE_ADAPTERS.get(job.source_format)
            inventory = adapter.inspect(
                upload_path,
                analysis_id=analysis_id,
                filename=job.filename,
                source_format=job.source_format,
                max_records=self.max_input_records,
                cancel_check=lambda: self._raise_if_cancelled(analysis_id),
            )
            self._update(analysis_id, inventory=inventory, progress=15)
            self._update(
                analysis_id,
                progress=25,
                stage="mapping",
            )
            mapping = self.semantic_mapper.map(inventory)
            self._update(analysis_id, mapping=mapping, progress=35)
            self._update(analysis_id, progress=40, stage="planning")
            plan = self._build_plan(inventory, mapping)
            self._update(analysis_id, plan=plan, progress=45)

            if job.source_format != SourceFormat.CSV:
                terminal = {
                    "status": "failed",
                    "progress": 100,
                    "stage": "analysis_pending",
                    "errors": [
                        "План анализа сформирован. Исполняемые fraud-профили "
                        "будут подключены на этапах D007–D011."
                    ],
                }
                return

            frame, _ = read_csv(upload_path)
            self._raise_if_cancelled(analysis_id)
            if frame.empty:
                raise ValueError("CSV contains no data rows.")
            if len(frame) > self.max_input_records:
                terminal = {
                    "status": "failed",
                    "progress": 100,
                    "stage": "input_limit_exceeded",
                    "errors": [
                        f"Источник содержит больше {self.max_input_records:,} записей."
                    ],
                }
                return

            self._update(
                analysis_id, status="validating", progress=15, stage="validating"
            )
            validation = validate_schema(frame, self.manifest)
            self._raise_if_cancelled(analysis_id)
            self._update(analysis_id, warnings=validation.warnings)
            if not validation.is_compatible:
                terminal = {
                    "status": "failed",
                    "progress": 100,
                    "stage": "schema_rejected",
                    "errors": validation.errors,
                }
                return

            self._update(
                analysis_id,
                status="predicting",
                progress=35,
                stage="client_scoring",
            )
            prediction = predict_frame(
                frame,
                self.model,
                self.manifest,
                warnings=validation.warnings,
            )
            self._raise_if_cancelled(analysis_id)
            target = (
                frame[self.manifest.target_column]
                if validation.target_present
                else None
            )
            metrics = calculate_metrics(
                target,
                prediction.probabilities,
                prediction.threshold,
            )
            risk_counts = Counter(row["risk_level"] for row in prediction.rows)
            summary = {
                "rows": len(prediction.rows),
                "requires_review": sum(
                    bool(row["requires_review"]) for row in prediction.rows
                ),
                "risk_counts": {
                    level: risk_counts.get(level, 0)
                    for level in ("low", "medium", "high", "critical")
                },
                "warnings": validation.warnings,
                "target_present": validation.target_present,
                "target_valid": validation.target_valid,
            }
            self.store.create_session(
                prediction.rows,
                threshold=prediction.threshold,
                metrics=metrics,
                model_version=self.manifest.model_version,
                session_id=analysis_id,
                summary=summary,
            )
            self.store.set_owner(analysis_id, job.owner_user_id)
            self._raise_if_cancelled(analysis_id)
            terminal = {
                "status": "completed",
                "progress": 100,
                "stage": "ready",
            }
        except AnalysisCancelled:
            terminal = {
                "status": "cancelled",
                "stage": "cancelled",
                "warnings": ["Анализ отменён пользователем."],
                "errors": [],
            }
        except SourceRecordLimitError as error:
            terminal = {
                "status": "failed",
                "progress": 100,
                "stage": "input_limit_exceeded",
                "errors": [str(error)],
            }
        except (SourceAdapterError, CsvFormatError, UnicodeError, ValueError) as error:
            terminal = {
                "status": "failed",
                "progress": 100,
                "stage": "source_invalid",
                "errors": [str(error)],
            }
        except (sqlite3.OperationalError, OSError) as error:
            logger.exception("Analysis %s could not persist its results", analysis_id)
            storage_full = "full" in str(error).lower() or getattr(error, "errno", None) == 28
            terminal = {
                "status": "failed",
                "progress": 100,
                "stage": "storage_failed",
                "errors": [
                    (
                        "Недостаточно временного места для результатов. "
                        "Завершите предыдущую сессию и повторите анализ."
                        if storage_full
                        else "Не удалось сохранить временные результаты анализа."
                    )
                ],
            }
        except Exception:
            logger.exception("Unexpected failure while processing analysis %s", analysis_id)
        finally:
            try:
                remove_staged_upload(self.incoming_dir, analysis_id)
            except OSError:
                logger.exception("Could not remove staged source for %s", analysis_id)
            if terminal["status"] in {"failed", "cancelled"}:
                try:
                    self.store.delete_session(analysis_id)
                except (SessionNotFoundError, OSError):
                    pass
            self._update(analysis_id, **terminal)

    def remove(self, analysis_id: str) -> None:
        job = self.get_job(analysis_id)
        if job.status in {"queued", "validating", "predicting", "cancel_requested"}:
            raise ApiError(409, "analysis_running", "Analysis is still running.")
        try:
            self.store.delete_session(analysis_id)
        except SessionNotFoundError:
            pass
        remove_staged_upload(self.incoming_dir, analysis_id)
        with self._lock:
            self.jobs.pop(analysis_id, None)


def analyst_access(request: Request, current: CurrentAnalyst):
    request.state.analyst = current
    analysis_id = request.path_params.get("analysis_id")
    if analysis_id is not None:
        manager = _manager(request)
        job = manager.get_job(analysis_id)
        if job.owner_user_id != current.id:
            raise ApiError(404, "analysis_not_found", "Analysis session was not found.")


router = APIRouter(prefix="/api/analyst", dependencies=[Depends(analyst_access)])


def _manager(request: Request) -> AnalysisManager:
    manager = getattr(request.app.state, "analysis_manager", None)
    if manager is None:
        raise ApiError(503, "model_unavailable", "Model artifacts are not available.")
    return manager


def _model_registry(request: Request) -> ModelRegistry:
    registry = getattr(request.app.state, "model_registry", None)
    if registry is None:
        raise ApiError(503, "model_registry_unavailable", "Model registry is not available.")
    return registry


def _ensure_no_running_analysis(manager: AnalysisManager) -> None:
    running = [
        job.analysis_id
        for job in manager.jobs.values()
        if job.status in {"queued", "validating", "predicting", "cancel_requested"}
    ]
    if running:
        raise ApiError(
            409,
            "analysis_running",
            "Model deployment is blocked while analyses are running.",
            [f"Running analyses: {len(running)}"],
        )


def _apply_champion(manager: AnalysisManager, profile: ModelProfile, artifact_path: str) -> None:
    path = Path(artifact_path)
    if profile == "client_risk":
        model, manifest = load_artifacts(path)
        with manager._lock:
            manager.model = model
            manager.manifest = manifest
    else:
        load_transaction_artifacts(path)
        with manager._lock:
            manager.transaction_artifact_dir = path


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> dict[str, Any]:
    manager = getattr(request.app.state, "analysis_manager", None)
    ready = manager is not None
    return {
        "status": "ok" if ready else "degraded",
        "model_ready": ready and manager.model is not None and manager.manifest is not None,
        "rules_ready": ready,
    }


@router.get("/model", response_model=ModelStatusResponse)
def model_status(request: Request) -> dict[str, Any]:
    manager = getattr(request.app.state, "analysis_manager", None)
    if manager is None:
        return {
            "ready": False,
            "error": getattr(request.app.state, "model_error", "Model unavailable."),
        }
    if manager.model is None or manager.manifest is None:
        return {"ready": True, "engine_mode": "rule_based_fallback", "client_model_available": False,
                "transaction_model_available": getattr(request.app.state, "transaction_model_available", False),
                "error": "Клиентская модель отсутствует; доступны транзакционные правила."}
    return {
        "ready": True,
        "version": manager.manifest.model_version,
        "features": len(manager.manifest.feature_columns),
        "review_threshold": manager.manifest.review_threshold,
        "client_model_available": True,
        "transaction_model_available": getattr(request.app.state, "transaction_model_available", False),
    }


@router.get("/models/versions")
def model_versions(
    request: Request,
    profile: Literal["client_risk", "transaction_anomaly"] | None = Query(default=None),
) -> dict[str, Any]:
    registry = _model_registry(request)
    return {
        "items": registry.list_versions(profile),
        "champions": {
            name: registry.champion(name)
            for name in ("client_risk", "transaction_anomaly")
        },
    }


@router.get("/models/audit")
def model_audit(
    request: Request,
    profile: Literal["client_risk", "transaction_anomaly"] | None = Query(default=None),
) -> dict[str, Any]:
    return {"items": _model_registry(request).audit(profile)}


@router.post("/models/candidates", status_code=status.HTTP_201_CREATED)
def register_model_candidate(
    request: Request,
    candidate: CandidateRegistration,
) -> dict[str, Any]:
    try:
        return _model_registry(request).register_candidate(
            candidate.profile, candidate.version, candidate.evaluation
        )
    except ModelRegistryError as error:
        raise ApiError(422, "candidate_rejected", str(error)) from error
    except (ValueError, RuntimeError, OSError, ImportError) as error:
        raise ApiError(422, "candidate_unavailable", "Артефакты кандидата отсутствуют, повреждены или недоступна зависимость модели.") from error


@router.post("/models/{profile}/{version}/activate")
def activate_model_candidate(
    request: Request,
    profile: Literal["client_risk", "transaction_anomaly"],
    version: str,
    deployment: ModelDeploymentRequest,
) -> dict[str, Any]:
    manager = _manager(request)
    _ensure_no_running_analysis(manager)
    registry = _model_registry(request)
    try:
        activated = registry.activate(
            profile,
            version,
            actor=f"analyst:{request.state.analyst.id}",
            reason=deployment.reason,
            confirmation=deployment.confirmation,
        )
        _apply_champion(manager, profile, activated["artifact_path"])
        return activated
    except ModelRegistryError as error:
        raise ApiError(409, "activation_blocked", str(error)) from error


@router.post("/models/{profile}/{version}/rollback")
def rollback_model(
    request: Request,
    profile: Literal["client_risk", "transaction_anomaly"],
    version: str,
    deployment: ModelDeploymentRequest,
) -> dict[str, Any]:
    manager = _manager(request)
    _ensure_no_running_analysis(manager)
    registry = _model_registry(request)
    try:
        activated = registry.rollback(
            profile,
            version,
            actor=f"analyst:{request.state.analyst.id}",
            reason=deployment.reason,
            confirmation=deployment.confirmation,
        )
        _apply_champion(manager, profile, activated["artifact_path"])
        return activated
    except ModelRegistryError as error:
        raise ApiError(409, "rollback_blocked", str(error)) from error


@router.post(
    "/analyses",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CreateAnalysisResponse,
)
async def create_analysis(
    request: Request,
    file: UploadFile = File(...),
    auto_run: bool = Query(default=True),
) -> dict[str, Any]:
    manager = _manager(request)
    manager.cleanup_expired()
    try:
        filename = safe_filename(file.filename)
        source_format = detect_source_format(filename)
    except UploadValidationError as error:
        status_code = 415 if error.code == "unsupported_file" else 400
        raise ApiError(status_code, error.code, str(error)) from error

    analysis_id = str(uuid.uuid4())
    upload_path = prepare_staged_upload(
        manager.incoming_dir,
        analysis_id,
        filename,
    )
    byte_limit = int(getattr(request.app.state, "max_input_bytes", MAX_INPUT_BYTES))
    received = 0
    try:
        with upload_path.open("wb") as stream:
            while chunk := await file.read(1024 * 1024):
                received += len(chunk)
                if received > byte_limit:
                    raise ApiError(
                        413,
                        "file_too_large",
                        f"File exceeds the {byte_limit}-byte upload limit.",
                    )
                stream.write(chunk)
    except Exception:
        remove_staged_upload(manager.incoming_dir, analysis_id)
        raise
    finally:
        await file.close()

    try:
        validate_staged_signature(upload_path, source_format)
    except UploadValidationError as error:
        remove_staged_upload(manager.incoming_dir, analysis_id)
        raise ApiError(422, error.code, str(error)) from error

    manager.add_job(
        analysis_id,
        filename,
        source_format,
        upload_path,
        received,
        owner_user_id=request.state.analyst.id,
    )
    worker = manager.process if auto_run else manager.inspect_only
    task = asyncio.create_task(asyncio.to_thread(worker, analysis_id, upload_path))
    manager.tasks.add(task)
    task.add_done_callback(manager.tasks.discard)
    return {
        "analysis_id": analysis_id,
        "status": "queued",
        "source_format": source_format,
        "status_url": f"/api/analyst/analyses/{analysis_id}/status",
    }


@router.get(
    "/analyses/{analysis_id}/status",
    response_model=AnalysisStatusResponse,
)
def analysis_status(request: Request, analysis_id: str) -> dict[str, Any]:
    return _manager(request).get_job(analysis_id).public_status()


@router.get("/analyses/{analysis_id}/inventory")
def analysis_inventory(request: Request, analysis_id: str) -> dict[str, Any]:
    job = _manager(request).get_job(analysis_id)
    if job.inventory is None:
        raise ApiError(409, "inventory_not_ready", "Source inventory is not ready.")
    return job.inventory.model_dump(mode="json")


@router.get("/analyses/{analysis_id}/plan")
def analysis_plan(request: Request, analysis_id: str) -> dict[str, Any]:
    job = _manager(request).get_job(analysis_id)
    if job.plan is None:
        raise ApiError(409, "plan_not_ready", "Analysis plan is not ready.")
    return job.plan.model_dump(mode="json")


@router.patch("/analyses/{analysis_id}/plan")
def update_analysis_plan(
    request: Request,
    analysis_id: str,
    patch: PlanPeriodPatch,
) -> dict[str, Any]:
    manager = _manager(request)
    job = manager.get_job(analysis_id)
    if job.status != "planned" or job.plan is None:
        raise ApiError(409, "plan_not_editable", "Only a prepared plan can be edited.")
    try:
        candidate = TimeRange.model_validate(patch.model_dump())
    except ValueError as error:
        raise ApiError(422, "invalid_period", str(error)) from error
    source = job.plan.time_range
    if source is None:
        raise ApiError(422, "period_unavailable", "Source period is not available.")
    if candidate.start is not None and source.start is not None and candidate.start < source.start:
        raise ApiError(422, "period_out_of_range", "Start is outside the source period.")
    if candidate.end is not None and source.end is not None and candidate.end > source.end:
        raise ApiError(422, "period_out_of_range", "End is outside the source period.")
    job.plan = job.plan.model_copy(update={"time_range": candidate})
    return job.plan.model_dump(mode="json")


@router.post("/analyses/{analysis_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_analysis(request: Request, analysis_id: str) -> dict[str, Any]:
    manager = _manager(request)
    job = manager.get_job(analysis_id)
    if job.status != "planned":
        raise ApiError(409, "analysis_not_planned", "Analysis is not ready to run.")
    task = asyncio.create_task(asyncio.to_thread(manager.run_universal, analysis_id))
    manager.tasks.add(task)
    task.add_done_callback(manager.tasks.discard)
    return {"analysis_id": analysis_id, "status": "queued"}


@router.post(
    "/analyses/{analysis_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CancelAnalysisResponse,
)
def cancel_analysis(request: Request, analysis_id: str) -> dict[str, Any]:
    job = _manager(request).request_cancel(analysis_id)
    return {
        "analysis_id": analysis_id,
        "status": job.status,
    }


def _completed_job(manager: AnalysisManager, analysis_id: str) -> AnalysisJob:
    job = manager.get_job(analysis_id)
    if job.status == "failed":
        raise ApiError(422, "analysis_failed", "Analysis failed.", job.errors)
    if job.status in {"cancel_requested", "cancelled"}:
        raise ApiError(409, "analysis_cancelled", "Analysis was cancelled.")
    if job.status != "completed":
        raise ApiError(409, "analysis_not_ready", "Analysis is not completed yet.")
    return job


@router.get(
    "/analyses/{analysis_id}/summary",
    response_model=AnalysisSummaryResponse,
)
def analysis_summary(
    request: Request,
    analysis_id: str,
    threshold: float | None = Query(default=None, ge=0, le=1),
) -> dict[str, Any]:
    manager = _manager(request)
    _completed_job(manager, analysis_id)
    metadata = manager.store.get_metadata(analysis_id)
    active_threshold = (
        float(metadata["threshold"]) if threshold is None else threshold
    )
    targets, probabilities = manager.store.metric_inputs(
        analysis_id, manager.manifest.target_column if manager.manifest else "__no_client_model_target__"
    )
    metrics = calculate_metrics(
        None if targets is None else pd.Series(targets),
        probabilities,
        active_threshold,
    )
    summary = dict(metadata["summary"])
    summary["requires_review"] = sum(
        probability >= active_threshold for probability in probabilities
    )
    # Universal sessions initially stored only profile counters in their summary.
    # Keep the legacy dashboard contract complete for both newly created and
    # already persisted sessions so the frontend never has to guess these fields.
    if "risk_counts" not in summary:
        summary["risk_counts"] = manager.store.get_distribution(
            analysis_id,
            threshold=active_threshold,
        ).risk_counts
    universal_result = metadata.get("universal_result") or {}
    summary.setdefault("warnings", list(universal_result.get("warnings") or []))
    summary.setdefault("target_present", targets is not None)
    summary.setdefault("target_valid", bool(metrics.available))
    return {
        "analysis_id": analysis_id,
        "model_version": metadata["model_version"],
        "threshold": active_threshold,
        "summary": summary,
        "metrics": metrics.to_dict(),
    }


@router.get(
    "/analyses/{analysis_id}/results",
    response_model=AnalysisResultsResponse,
)
def analysis_results(
    request: Request,
    analysis_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    risk_level: str | None = Query(default=None),
    threshold: float | None = Query(default=None, ge=0, le=1),
    requires_review: bool | None = Query(default=None),
    probability_min: float | None = Query(default=None, ge=0, le=1),
    probability_max: float | None = Query(default=None, ge=0, le=1),
    record_id: str | None = Query(default=None, max_length=200),
) -> dict[str, Any]:
    manager = _manager(request)
    _completed_job(manager, analysis_id)
    try:
        result = manager.store.get_page(
            analysis_id,
            page=page,
            page_size=page_size,
            risk_filter=risk_level,
            threshold=threshold,
            requires_review=requires_review,
            probability_min=probability_min,
            probability_max=probability_max,
            record_id=record_id,
        )
    except ValueError as error:
        raise ApiError(422, "invalid_filter", str(error)) from error
    return asdict(result)


def _universal_page(
    manager: AnalysisManager,
    analysis_id: str,
    profile: str,
    *,
    page: int,
    page_size: int,
    risk_level: str | None,
    requires_review: bool | None,
    probability_min: float | None,
    probability_max: float | None,
    search: str | None,
) -> dict[str, Any]:
    _completed_job(manager, analysis_id)
    try:
        return manager.store.get_entity_page(
            analysis_id,
            profile=profile,
            page=page,
            page_size=page_size,
            risk_level=risk_level,
            requires_review=requires_review,
            probability_min=probability_min,
            probability_max=probability_max,
            search=search,
        )
    except (ValueError, SessionNotFoundError) as error:
        raise ApiError(422, "invalid_filter", str(error)) from error


@router.get("/analyses/{analysis_id}/clients")
def analysis_clients(
    request: Request,
    analysis_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    risk_level: str | None = Query(default=None),
    requires_review: bool | None = Query(default=None),
    probability_min: float | None = Query(default=None, ge=0, le=1),
    probability_max: float | None = Query(default=None, ge=0, le=1),
    search: str | None = Query(default=None, max_length=200),
) -> dict[str, Any]:
    return _universal_page(
        _manager(request), analysis_id, "client_risk",
        page=page, page_size=page_size, risk_level=risk_level,
        requires_review=requires_review, probability_min=probability_min,
        probability_max=probability_max, search=search,
    )


@router.get("/analyses/{analysis_id}/transactions")
def analysis_transactions(
    request: Request,
    analysis_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    risk_level: str | None = Query(default=None),
    requires_review: bool | None = Query(default=None),
    probability_min: float | None = Query(default=None, ge=0, le=1),
    probability_max: float | None = Query(default=None, ge=0, le=1),
    search: str | None = Query(default=None, max_length=200),
) -> dict[str, Any]:
    return _universal_page(
        _manager(request), analysis_id, "transaction_anomaly",
        page=page, page_size=page_size, risk_level=risk_level,
        requires_review=requires_review, probability_min=probability_min,
        probability_max=probability_max, search=search,
    )


@router.get("/analyses/{analysis_id}/relationships")
def analysis_relationships(
    request: Request,
    analysis_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    kind: str | None = Query(default=None, max_length=100),
    entity_id: str | None = Query(default=None, max_length=200),
    probability_min: float | None = Query(default=None, ge=0, le=1),
) -> dict[str, Any]:
    manager = _manager(request)
    _completed_job(manager, analysis_id)
    try:
        return manager.store.get_relationship_page(
            analysis_id,
            page=page,
            page_size=page_size,
            kind=kind,
            entity_id=entity_id,
            probability_min=probability_min,
        )
    except ValueError as error:
        raise ApiError(422, "invalid_filter", str(error)) from error


@router.patch("/analyses/{analysis_id}/investigations/{entity_id}")
def update_investigation(
    request: Request,
    analysis_id: str,
    entity_id: str,
    patch: FeedbackPatch,
) -> dict[str, Any]:
    manager = _manager(request)
    _completed_job(manager, analysis_id)
    saved = manager.store.save_feedback(
        analysis_id,
        entity_id.strip(),
        patch.status,
        patch.comment.strip(),
    )
    return {"analysis_id": analysis_id, "entity_id": entity_id, **saved}


@router.get("/analyses/{analysis_id}/investigations/{entity_id}")
def investigation_details(
    request: Request,
    analysis_id: str,
    entity_id: str,
) -> dict[str, Any]:
    manager = _manager(request)
    _completed_job(manager, analysis_id)
    return {
        "analysis_id": analysis_id,
        "entity_id": entity_id,
        **manager.store.get_feedback(analysis_id, entity_id),
    }


@router.get("/analyses/{analysis_id}/feedback")
def confirmed_feedback(
    request: Request,
    analysis_id: str,
) -> dict[str, Any]:
    manager = _manager(request)
    _completed_job(manager, analysis_id)
    return {"analysis_id": analysis_id, **manager.store.feedback_summary(analysis_id)}


@router.get("/analyses/{analysis_id}/exports/{kind}")
def download_universal_export(
    request: Request,
    analysis_id: str,
    kind: Literal[
        "full", "review", "transactions", "relationships", "mapping_quality", "technical_plan"
    ],
    masked: bool = Query(default=True),
) -> Response:
    manager = _manager(request)
    job = _completed_job(manager, analysis_id)
    if kind == "technical_plan":
        if job.plan is None:
            raise ApiError(409, "plan_not_ready", "Analysis plan is not ready.")
        return JSONResponse(
            job.plan.model_dump(mode="json"),
            headers={
                "Content-Disposition": f'attachment; filename="analysis-{analysis_id}-technical-plan.json"'
            },
        )
    if kind == "mapping_quality":
        if job.mapping is None:
            raise ApiError(409, "mapping_not_ready", "Semantic mapping is not ready.")
        records = (
            {
                "display_label": item.display_label,
                "canonical_field": item.canonical_field,
                "confidence": item.confidence,
                "confidence_level": item.confidence_level.value,
                "status": item.status.value,
                "warnings": list(item.warnings),
            }
            for item in job.mapping.mappings
        )
        compact = False
    elif kind == "relationships":
        records = manager.store.iter_relationship_payloads(analysis_id)
        compact = False
    elif kind == "transactions":
        records = manager.store.iter_entity_payloads(analysis_id, "transaction_anomaly")
        compact = False
    else:
        records = manager.store.iter_entity_payloads(
            analysis_id,
            review_only=kind == "review",
        )
        compact = True
    suffix = kind.replace("_", "-")
    return StreamingResponse(
        iter_csv_records(records, masked=masked, compact_payload=compact),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="analysis-{analysis_id}-{suffix}.csv"',
            "X-Data-Masking": "masked" if masked else "full-sensitive",
        },
    )


@router.get(
    "/analyses/{analysis_id}/distribution",
    response_model=RiskDistributionResponse,
)
def analysis_distribution(
    request: Request,
    analysis_id: str,
    bins: int = Query(default=10, ge=2, le=50),
    threshold: float | None = Query(default=None, ge=0, le=1),
) -> dict[str, Any]:
    manager = _manager(request)
    _completed_job(manager, analysis_id)
    try:
        distribution = manager.store.get_distribution(
            analysis_id,
            bins=bins,
            threshold=threshold,
        )
    except ValueError as error:
        raise ApiError(422, "invalid_filter", str(error)) from error
    payload = asdict(distribution)
    payload["probability_histogram"] = [
        {
            "from": item["from_value"],
            "to": item["to_value"],
            "count": item["count"],
        }
        for item in payload["probability_histogram"]
    ]
    return payload


@router.get("/analyses/{analysis_id}/report.csv")
def download_report(
    request: Request,
    analysis_id: str,
    threshold: float | None = Query(default=None, ge=0, le=1),
    requires_review: bool = Query(default=False),
) -> StreamingResponse:
    manager = _manager(request)
    _completed_job(manager, analysis_id)
    return StreamingResponse(
        manager.store.iter_csv(
            analysis_id,
            threshold=threshold,
            requires_review=requires_review,
        ),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="analysis-{analysis_id}-review.csv"'
                if requires_review
                else f'attachment; filename="analysis-{analysis_id}.csv"'
            )
        },
    )


@router.delete("/analyses/{analysis_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_analysis(request: Request, analysis_id: str) -> Response:
    _manager(request).remove(analysis_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
