from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from app.risk_ledger.domain.canonical_schema import AnalysisProfile
from app.risk_ledger.domain.contracts import (
    AnalysisPlanContract,
    AnalysisProgressContract,
    AnalysisResultContract,
    AnalysisStage,
    AnalysisState,
    PlanProfileState,
    ProfileProgress,
    ProfileResultSummary,
    ProfileState,
)
from app.risk_ledger.domain.models import ModelManifest
from app.risk_ledger.core.config import DEFAULT_TRANSACTION_ARTIFACT_DIR
from app.risk_ledger.services.predictor import predict_frame, risk_level
from app.risk_ledger.services.schema_validator import validate_schema
from app.risk_ledger.services.transaction_rules import analyze_transaction_risk


ProgressReporter = Callable[[AnalysisStage, int, int | None], None]
ProgressObserver = Callable[[AnalysisProgressContract], None]


class AnalysisCancelled(RuntimeError):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise AnalysisCancelled("Analysis was cancelled.")


@dataclass(frozen=True, slots=True)
class ProfileExecution:
    profile: AnalysisProfile
    records: tuple[dict[str, Any], ...]
    model_version: str | None
    warnings: tuple[str, ...] = ()

    @property
    def requires_review(self) -> int:
        return sum(bool(record.get("requires_review")) for record in self.records)


class ProfileRunner(Protocol):
    profile: AnalysisProfile

    def run(
        self,
        *,
        cancel_check: Callable[[], None],
        report_progress: ProgressReporter,
    ) -> ProfileExecution: ...


@dataclass(slots=True)
class ClientRiskProfileRunner:
    frame: pd.DataFrame
    model: Any
    manifest: ModelManifest
    batch_size: int = 2_000
    warnings: tuple[str, ...] = ()
    profile: AnalysisProfile = field(default=AnalysisProfile.CLIENT_RISK, init=False)

    def run(
        self,
        *,
        cancel_check: Callable[[], None],
        report_progress: ProgressReporter,
    ) -> ProfileExecution:
        cancel_check()
        validation = validate_schema(self.frame, self.manifest)
        if not validation.is_compatible:
            raise ValueError("; ".join(validation.errors))
        all_warnings = tuple(dict.fromkeys((*self.warnings, *validation.warnings)))
        prediction = predict_frame(
            self.frame,
            self.model,
            self.manifest,
            warnings=list(all_warnings),
            batch_size=self.batch_size,
            cancel_check=cancel_check,
            progress_callback=lambda processed, total: report_progress(
                AnalysisStage.CLIENT_SCORING, processed, total
            ),
        )
        records = tuple(
            sorted(
                (dict(row) for row in prediction.rows),
                key=lambda row: (-float(row["risk_probability"]), str(row["record_id"])),
            )
        )
        return ProfileExecution(
            profile=self.profile,
            records=records,
            model_version=self.manifest.model_version,
            warnings=all_warnings,
        )


@dataclass(slots=True)
class TransactionAnomalyProfileRunner:
    input_path: Path
    artifact_dir: Path | None
    chunk_size: int = 2_000
    total_records: int | None = None
    data_quality_score: float = 1.0
    profile: AnalysisProfile = field(
        default=AnalysisProfile.TRANSACTION_ANOMALY,
        init=False,
    )

    def run(
        self,
        *,
        cancel_check: Callable[[], None],
        report_progress: ProgressReporter,
    ) -> ProfileExecution:
        cancel_check()
        scores, manifest = analyze_transaction_risk(
            self.input_path,
            self.artifact_dir or DEFAULT_TRANSACTION_ARTIFACT_DIR,
            chunk_size=self.chunk_size,
            data_quality_score=self.data_quality_score,
            cancel_check=cancel_check,
            ml_progress_callback=lambda processed: report_progress(
                AnalysisStage.TRANSACTION_FEATURES,
                processed,
                self.total_records,
            ),
            rule_progress_callback=lambda processed: report_progress(
                AnalysisStage.TRANSACTION_SCORING,
                processed,
                self.total_records,
            ),
        )
        records: list[dict[str, Any]] = []
        boundaries = {"medium": 0.40, "high": 0.72, "critical": 0.95}
        for item in scores.to_dict(orient="records"):
            score = float(item["risk_signal_score"])
            item["record_id"] = str(item["transaction_id"])
            item["risk_probability"] = score
            item["score_type"] = "risk_signal_not_fraud_probability"
            item["engine_mode"] = manifest.get("engine_mode", "ml_and_rules")
            item["risk_level"] = risk_level(score, boundaries)
            item["requires_review"] = bool(item["requires_review"])
            records.append(item)
        synthetic_only = bool(records) and all(
            str(record.get("synthetic_only", "")).strip().casefold()
            in {"1", "1.0", "true"}
            for record in records
        )
        warnings = (
            "Транзакционные контрольные данные синтетические; результат не подтверждает качество на реальных операциях.",
        ) if synthetic_only else ()
        if manifest.get("engine_mode") == "rule_based_fallback":
            warnings += ("ML-модель транзакций недоступна: применяются правила и граф; оценка не является вероятностью мошенничества.",)
        return ProfileExecution(
            profile=self.profile,
            records=tuple(records),
            model_version=manifest.get("model_version") or "rule_based_fallback",
            warnings=warnings,
        )


@dataclass(frozen=True, slots=True)
class OrchestrationOutcome:
    progress: AnalysisProgressContract
    progress_history: tuple[AnalysisProgressContract, ...]
    result: AnalysisResultContract | None
    client_records: tuple[dict[str, Any], ...] = ()
    transaction_records: tuple[dict[str, Any], ...] = ()
    relationships: tuple[dict[str, Any], ...] = ()

    @property
    def cancelled(self) -> bool:
        return self.progress.state == AnalysisState.CANCELLED


_STAGE_ORDER = {
    stage: index
    for index, stage in enumerate(
        (
            AnalysisStage.UPLOADED,
            AnalysisStage.INSPECTING,
            AnalysisStage.MAPPING,
            AnalysisStage.PLANNING,
            AnalysisStage.VALIDATING,
            AnalysisStage.TRANSFORMING,
            AnalysisStage.CLIENT_SCORING,
            AnalysisStage.TRANSACTION_FEATURES,
            AnalysisStage.TRANSACTION_SCORING,
            AnalysisStage.RELATIONSHIPS,
            AnalysisStage.EXPLAINING,
            AnalysisStage.EXPORTING,
            AnalysisStage.READY,
        )
    )
}


class AnalysisStateMachine:
    def __init__(self) -> None:
        self.state = AnalysisState.QUEUED
        self.stage = AnalysisStage.PLANNING
        self.progress = 0

    def transition(
        self,
        state: AnalysisState,
        stage: AnalysisStage,
        progress: int,
    ) -> None:
        allowed = {
            AnalysisState.QUEUED: {
                AnalysisState.RUNNING,
                AnalysisState.CANCEL_REQUESTED,
                AnalysisState.FAILED,
            },
            AnalysisState.RUNNING: {
                AnalysisState.RUNNING,
                AnalysisState.READY,
                AnalysisState.PARTIALLY_READY,
                AnalysisState.CANCEL_REQUESTED,
                AnalysisState.FAILED,
            },
            AnalysisState.CANCEL_REQUESTED: {AnalysisState.CANCELLED},
        }
        if state != self.state and state not in allowed.get(self.state, set()):
            raise RuntimeError(f"Invalid analysis state transition: {self.state} -> {state}")
        if progress < self.progress:
            raise RuntimeError("Analysis progress cannot move backwards.")
        if stage not in {AnalysisStage.FAILED, AnalysisStage.CANCELLED}:
            previous = _STAGE_ORDER.get(self.stage, -1)
            current = _STAGE_ORDER.get(stage, -1)
            if current < previous:
                raise RuntimeError(f"Analysis stage cannot move backwards: {self.stage} -> {stage}")
        self.state = state
        self.stage = stage
        self.progress = progress


class UniversalAnalysisOrchestrator:
    def __init__(self, *, observer: ProgressObserver | None = None) -> None:
        self.observer = observer
        self._lock = threading.Lock()

    def run(
        self,
        plan: AnalysisPlanContract,
        runners: Mapping[AnalysisProfile, ProfileRunner],
        *,
        cancellation: CancellationToken | None = None,
    ) -> OrchestrationOutcome:
        token = cancellation or CancellationToken()
        machine = AnalysisStateMachine()
        history: list[AnalysisProgressContract] = []
        executions: dict[AnalysisProfile, ProfileExecution] = {}
        summaries: dict[AnalysisProfile, ProfileResultSummary] = {}
        profile_progress = self._initial_profile_progress(plan)
        overall_warnings = list(plan.warnings)
        overall_errors: list[str] = []

        def emit(
            state: AnalysisState,
            stage: AnalysisStage,
            progress: int,
            *,
            can_cancel: bool,
        ) -> AnalysisProgressContract:
            with self._lock:
                machine.transition(state, stage, progress)
                profiles = tuple(profile_progress[profile] for profile in AnalysisProfile)
                processed = sum(item.processed_records for item in profiles)
                known_totals = [item.total_records for item in profiles if item.total_records is not None]
                total = sum(known_totals) if known_totals else None
                snapshot = AnalysisProgressContract(
                    analysis_id=plan.analysis_id,
                    state=state,
                    stage=stage,
                    progress=progress,
                    processed_records=processed,
                    total_records=total,
                    profiles=profiles,
                    warnings=tuple(dict.fromkeys(overall_warnings)),
                    errors=tuple(dict.fromkeys(overall_errors)),
                    can_cancel=can_cancel,
                )
                history.append(snapshot)
            if self.observer is not None:
                self.observer(snapshot)
            return snapshot

        def check_cancelled() -> None:
            if token.is_cancelled:
                emit(
                    AnalysisState.CANCEL_REQUESTED,
                    machine.stage,
                    machine.progress,
                    can_cancel=False,
                )
                raise AnalysisCancelled

        try:
            emit(AnalysisState.RUNNING, AnalysisStage.VALIDATING, 5, can_cancel=True)
            check_cancelled()
            emit(AnalysisState.RUNNING, AnalysisStage.TRANSFORMING, 10, can_cancel=True)

            planned_profiles = [
                profile.profile
                for profile in plan.profiles
                if profile.state == PlanProfileState.PLANNED
            ]
            for profile in (AnalysisProfile.CLIENT_RISK, AnalysisProfile.TRANSACTION_ANOMALY):
                if profile not in planned_profiles:
                    continue
                check_cancelled()
                runner = runners.get(profile)
                if runner is None:
                    message = "Исполнитель профиля не настроен."
                    profile_progress[profile] = ProfileProgress(
                        profile=profile,
                        state=ProfileState.FAILED,
                        stage=AnalysisStage.VALIDATING,
                        progress=100,
                        errors=(message,),
                    )
                    summaries[profile] = ProfileResultSummary(
                        profile=profile,
                        state=ProfileState.FAILED,
                        records=0,
                        requires_review=0,
                        errors=(message,),
                    )
                    overall_errors.append(f"{profile.value}: {message}")
                    continue

                def report(stage: AnalysisStage, processed: int, total: int | None) -> None:
                    check_cancelled()
                    fraction = min(processed / total, 1.0) if total else 0.5
                    start, end = self._stage_range(stage)
                    overall = max(machine.progress, round(start + (end - start) * fraction))
                    profile_progress[profile] = ProfileProgress(
                        profile=profile,
                        state=ProfileState.RUNNING,
                        stage=stage,
                        progress=round(fraction * 100),
                        processed_records=processed,
                        total_records=total,
                    )
                    emit(AnalysisState.RUNNING, stage, overall, can_cancel=True)

                start_stage = (
                    AnalysisStage.CLIENT_SCORING
                    if profile == AnalysisProfile.CLIENT_RISK
                    else AnalysisStage.TRANSACTION_FEATURES
                )
                profile_progress[profile] = ProfileProgress(
                    profile=profile,
                    state=ProfileState.RUNNING,
                    stage=start_stage,
                    progress=0,
                )
                emit(
                    AnalysisState.RUNNING,
                    start_stage,
                    max(machine.progress, self._stage_range(start_stage)[0]),
                    can_cancel=True,
                )
                try:
                    execution = runner.run(
                        cancel_check=check_cancelled,
                        report_progress=report,
                    )
                    if execution.profile != profile:
                        raise ValueError("Profile runner returned a different profile.")
                    executions[profile] = execution
                    profile_progress[profile] = ProfileProgress(
                        profile=profile,
                        state=ProfileState.READY,
                        stage=(
                            AnalysisStage.CLIENT_SCORING
                            if profile == AnalysisProfile.CLIENT_RISK
                            else AnalysisStage.TRANSACTION_SCORING
                        ),
                        progress=100,
                        processed_records=len(execution.records),
                        total_records=len(execution.records),
                        warnings=execution.warnings,
                    )
                    summaries[profile] = ProfileResultSummary(
                        profile=profile,
                        state=ProfileState.READY,
                        records=len(execution.records),
                        requires_review=execution.requires_review,
                        model_version=execution.model_version,
                        warnings=execution.warnings,
                    )
                    overall_warnings.extend(execution.warnings)
                except AnalysisCancelled:
                    raise
                except Exception as error:
                    message = self._safe_profile_error(error)
                    profile_progress[profile] = ProfileProgress(
                        profile=profile,
                        state=ProfileState.FAILED,
                        stage=start_stage,
                        progress=100,
                        errors=(message,),
                    )
                    summaries[profile] = ProfileResultSummary(
                        profile=profile,
                        state=ProfileState.FAILED,
                        records=0,
                        requires_review=0,
                        errors=(message,),
                    )
                    overall_errors.append(f"{profile.value}: {message}")

            check_cancelled()
            relationship_progress = max(machine.progress, 86)
            emit(
                AnalysisState.RUNNING,
                AnalysisStage.RELATIONSHIPS,
                relationship_progress,
                can_cancel=True,
            )
            clients, transactions, relationships = self._combine(executions)
            check_cancelled()
            emit(
                AnalysisState.RUNNING,
                AnalysisStage.EXPLAINING,
                max(machine.progress, 94),
                can_cancel=True,
            )
            emit(
                AnalysisState.RUNNING,
                AnalysisStage.EXPORTING,
                max(machine.progress, 98),
                can_cancel=True,
            )

            for item in plan.profiles:
                if item.profile in summaries:
                    continue
                state = (
                    ProfileState.BLOCKED
                    if item.state == PlanProfileState.BLOCKED
                    else ProfileState.SKIPPED
                )
                summaries[item.profile] = ProfileResultSummary(
                    profile=item.profile,
                    state=state,
                    records=0,
                    requires_review=0,
                    warnings=item.warnings,
                    errors=item.blocking_reasons,
                )

            ready_count = sum(item.state == ProfileState.READY for item in summaries.values())
            unsuccessful_count = sum(
                item.state in {ProfileState.BLOCKED, ProfileState.FAILED}
                for item in summaries.values()
            )
            if ready_count and unsuccessful_count:
                final_state = AnalysisState.PARTIALLY_READY
                result_state = "partially_ready"
            elif ready_count:
                final_state = AnalysisState.READY
                result_state = "ready"
            else:
                final_state = AnalysisState.FAILED
                result_state = "failed"
            result = AnalysisResultContract(
                analysis_id=plan.analysis_id,
                state=result_state,
                canonical_schema_version=plan.canonical_schema_version,
                dictionary_version=plan.dictionary_version,
                source_period=plan.time_range,
                profiles=tuple(summaries[profile] for profile in AnalysisProfile),
                warnings=tuple(dict.fromkeys(overall_warnings)),
                errors=tuple(dict.fromkeys(overall_errors)),
                metadata={
                    "client_records": len(clients),
                    "transaction_records": len(transactions),
                    "relationships": len(relationships),
                },
            )
            terminal_stage = AnalysisStage.READY if ready_count else AnalysisStage.FAILED
            if ready_count:
                for profile, current in tuple(profile_progress.items()):
                    if current.state == ProfileState.READY:
                        profile_progress[profile] = current.model_copy(
                            update={"stage": AnalysisStage.READY}
                        )
            final_progress = emit(final_state, terminal_stage, 100, can_cancel=False)
            return OrchestrationOutcome(
                progress=final_progress,
                progress_history=tuple(history),
                result=result,
                client_records=clients,
                transaction_records=transactions,
                relationships=relationships,
            )
        except AnalysisCancelled:
            executions.clear()
            for profile, current in tuple(profile_progress.items()):
                if current.state not in {ProfileState.BLOCKED, ProfileState.SKIPPED}:
                    profile_progress[profile] = ProfileProgress(
                        profile=profile,
                        state=ProfileState.CANCELLED,
                        stage=AnalysisStage.CANCELLED,
                        progress=current.progress,
                        processed_records=current.processed_records,
                        total_records=current.total_records,
                        warnings=current.warnings,
                        errors=(),
                    )
            cancelled = emit(
                AnalysisState.CANCELLED,
                AnalysisStage.CANCELLED,
                machine.progress,
                can_cancel=False,
            )
            return OrchestrationOutcome(
                progress=cancelled,
                progress_history=tuple(history),
                result=None,
            )

    @staticmethod
    def _initial_profile_progress(
        plan: AnalysisPlanContract,
    ) -> dict[AnalysisProfile, ProfileProgress]:
        planned = {item.profile: item for item in plan.profiles}
        result: dict[AnalysisProfile, ProfileProgress] = {}
        for profile in AnalysisProfile:
            item = planned[profile]
            if item.state == PlanProfileState.BLOCKED:
                state = ProfileState.BLOCKED
                progress = 100
                errors = item.blocking_reasons
            elif item.state == PlanProfileState.SKIPPED:
                state = ProfileState.SKIPPED
                progress = 100
                errors = ()
            else:
                state = ProfileState.PENDING
                progress = 0
                errors = ()
            result[profile] = ProfileProgress(
                profile=profile,
                state=state,
                stage=AnalysisStage.PLANNING,
                progress=progress,
                warnings=item.warnings,
                errors=errors,
            )
        return result

    @staticmethod
    def _stage_range(stage: AnalysisStage) -> tuple[int, int]:
        return {
            AnalysisStage.CLIENT_SCORING: (12, 42),
            AnalysisStage.TRANSACTION_FEATURES: (43, 64),
            AnalysisStage.TRANSACTION_SCORING: (65, 85),
        }[stage]

    @staticmethod
    def _safe_profile_error(error: Exception) -> str:
        text = str(error).strip()
        return text[:500] if text else "Профиль завершился с ошибкой."

    @staticmethod
    def _combine(
        executions: Mapping[AnalysisProfile, ProfileExecution],
    ) -> tuple[
        tuple[dict[str, Any], ...],
        tuple[dict[str, Any], ...],
        tuple[dict[str, Any], ...],
    ]:
        clients = tuple(
            dict(item)
            for item in executions.get(
                AnalysisProfile.CLIENT_RISK,
                ProfileExecution(AnalysisProfile.CLIENT_RISK, (), None),
            ).records
        )
        client_risk: dict[str, float] = {}
        for item in clients:
            score = float(item.get("risk_probability", 0.0))
            for key in ("record_id", "client_id", "customer_id", "subject_id"):
                value = str(item.get(key, "")).strip()
                if value:
                    client_risk[value] = score

        transaction_source = executions.get(
            AnalysisProfile.TRANSACTION_ANOMALY,
            ProfileExecution(AnalysisProfile.TRANSACTION_ANOMALY, (), None),
        ).records
        transactions: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []
        for raw in transaction_source:
            item = dict(raw)
            transaction_id = str(item.get("transaction_id", item.get("record_id", "")))
            client_id = str(item.get("client_id", "")).strip()
            sender = str(item.get("sender_account_id", "")).strip()
            recipient = str(item.get("recipient_account_id", "")).strip()
            if client_id in client_risk:
                item["client_risk_probability"] = client_risk[client_id]
            transactions.append(item)
            if client_id:
                relationships.append({
                    "relationship_id": f"transaction-client:{transaction_id}:{client_id}",
                    "kind": "transaction_client",
                    "from_type": "transaction",
                    "from_id": transaction_id,
                    "to_type": "client",
                    "to_id": client_id,
                    "risk_signal_score": float(item.get("risk_probability", 0.0)),
                })
            if sender and recipient:
                relationships.append({
                    "relationship_id": f"transfer:{transaction_id}:{sender}:{recipient}",
                    "kind": "transfer",
                    "from_type": "account",
                    "from_id": sender,
                    "to_type": "account",
                    "to_id": recipient,
                    "transaction_id": transaction_id,
                    "risk_signal_score": float(item.get("risk_probability", 0.0)),
                })
        return clients, tuple(transactions), tuple(relationships)
