from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.risk_ledger.domain.canonical_schema import AnalysisProfile
from app.risk_ledger.domain.contracts import (
    AnalysisPlanContract,
    AnalysisStage,
    AnalysisState,
    PlanProfileState,
    ProfilePlan,
    ProfileState,
)
from app.risk_ledger.services.analysis_orchestrator import (
    CancellationToken,
    ClientRiskProfileRunner,
    ProfileExecution,
    TransactionAnomalyProfileRunner,
    UniversalAnalysisOrchestrator,
)
from app.risk_ledger.services.preprocessing import build_manifest
from app.risk_ledger.training.generate_synthetic_transactions import generate_synthetic_dataset
from app.risk_ledger.training.train_transactions import train_transaction_model


class _ClientModel:
    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        probabilities = np.clip(features["signal"].to_numpy(dtype=float) / 10, 0, 1)
        return np.column_stack([1 - probabilities, probabilities])

    def get_feature_importance(self, pool, type: str) -> np.ndarray:  # noqa: A002
        assert type == "ShapValues"
        values = np.ones((pool.num_row(), pool.num_col()), dtype="float64")
        return np.column_stack([values, np.zeros(pool.num_row())])


def _client_runner(rows: int = 8) -> ClientRiskProfileRunner:
    pytest.importorskip("catboost", reason="Client scoring requires optional CatBoost; no substitute model is created.")
    training = pd.DataFrame(
        {
            "signal": [1, 9, 4, 7],
            "amount": [100, 900, 400, 700],
            "term": [12, 24, 18, 30],
            "income": [500, 400, 300, 600],
            "age": [20, 50, 35, 42],
            "GENDER": ["515", "516", "515", "516"],
            "GB_flag": [0, 1, 0, 1],
        }
    )
    manifest = build_manifest(training)
    manifest.model_version = "client-integration-2.0"
    manifest.review_threshold = 0.5
    manifest.risk_boundaries = {"medium": 0.25, "high": 0.5, "critical": 0.85}
    frame = pd.DataFrame(
        {
            "client_id": [f"SYN-CL-{index:06d}" for index in range(1, rows + 1)],
            "signal": [(index % 10) + 1 for index in range(rows)],
            "amount": [100 + index * 25 for index in range(rows)],
            "term": [12] * rows,
            "income": [500] * rows,
            "age": [30 + index for index in range(rows)],
            "GENDER": ["515" if index % 2 == 0 else "516" for index in range(rows)],
        }
    )
    return ClientRiskProfileRunner(frame, _ClientModel(), manifest, batch_size=3)


def _profile_plan(profile: AnalysisProfile, state: PlanProfileState) -> ProfilePlan:
    return ProfilePlan(
        profile=profile,
        state=state,
        required_fields=(
            ("transaction.timestamp", "transaction.amount")
            if profile == AnalysisProfile.TRANSACTION_ANOMALY
            else ()
        ),
        available_fields=(),
        blocking_reasons=("Контрольная блокировка профиля",)
        if state == PlanProfileState.BLOCKED
        else (),
        comparison_mode="mixed"
        if profile == AnalysisProfile.TRANSACTION_ANOMALY
        else "not_applicable",
    )


def _plan(
    analysis_id: str,
    client: PlanProfileState,
    transactions: PlanProfileState,
) -> AnalysisPlanContract:
    return AnalysisPlanContract(
        analysis_id=analysis_id,
        canonical_schema_version="1.0",
        dictionary_version="integration-1",
        datasets=(),
        profiles=(
            _profile_plan(AnalysisProfile.CLIENT_RISK, client),
            _profile_plan(AnalysisProfile.TRANSACTION_ANOMALY, transactions),
        ),
    )


@pytest.fixture(scope="module")
def transaction_fixture(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, int]:
    root = tmp_path_factory.mktemp("d011-transactions")
    fixture = root / "fixture"
    artifacts = root / "artifacts"
    manifest = generate_synthetic_dataset(fixture, seed=211, normal_rows=1_000)
    train_transaction_model(
        fixture / "transactions.csv",
        artifacts,
        model_version="transaction-integration-1.0",
        random_seed=211,
        estimators=20,
        chunk_size=127,
    )
    return fixture / "transactions.csv", artifacts, int(manifest["rows"])


def _transaction_runner(
    transaction_fixture: tuple[Path, Path, int],
) -> TransactionAnomalyProfileRunner:
    source, artifacts, rows = transaction_fixture
    return TransactionAnomalyProfileRunner(
        source,
        artifacts,
        chunk_size=73,
        total_records=rows,
    )


def test_client_only_orchestration_reaches_ready() -> None:
    outcome = UniversalAnalysisOrchestrator().run(
        _plan("client-only", PlanProfileState.PLANNED, PlanProfileState.SKIPPED),
        {AnalysisProfile.CLIENT_RISK: _client_runner()},
    )

    assert outcome.progress.state == AnalysisState.READY
    assert outcome.result is not None and outcome.result.state == "ready"
    assert len(outcome.client_records) == 8
    assert not outcome.transaction_records
    assert not outcome.relationships
    assert [item.state for item in outcome.result.profiles] == [
        ProfileState.READY,
        ProfileState.SKIPPED,
    ]


def test_transaction_only_orchestration_builds_relationships(
    transaction_fixture: tuple[Path, Path, int],
) -> None:
    _, _, rows = transaction_fixture
    outcome = UniversalAnalysisOrchestrator().run(
        _plan("transaction-only", PlanProfileState.SKIPPED, PlanProfileState.PLANNED),
        {AnalysisProfile.TRANSACTION_ANOMALY: _transaction_runner(transaction_fixture)},
    )

    assert outcome.progress.state == AnalysisState.READY
    assert len(outcome.transaction_records) == rows
    assert len(outcome.relationships) == rows * 2
    assert outcome.transaction_records[0]["risk_probability"] >= outcome.transaction_records[-1]["risk_probability"]
    stages = [snapshot.stage for snapshot in outcome.progress_history]
    assert AnalysisStage.TRANSACTION_FEATURES in stages
    assert AnalysisStage.TRANSACTION_SCORING in stages
    assert AnalysisStage.RELATIONSHIPS in stages


def test_mixed_orchestration_joins_client_risk_to_transactions(
    transaction_fixture: tuple[Path, Path, int],
) -> None:
    outcome = UniversalAnalysisOrchestrator().run(
        _plan("mixed", PlanProfileState.PLANNED, PlanProfileState.PLANNED),
        {
            AnalysisProfile.CLIENT_RISK: _client_runner(),
            AnalysisProfile.TRANSACTION_ANOMALY: _transaction_runner(transaction_fixture),
        },
    )

    assert outcome.result is not None and outcome.result.state == "ready"
    assert len(outcome.client_records) == 8
    joined = [item for item in outcome.transaction_records if "client_risk_probability" in item]
    assert joined
    assert {item["client_id"] for item in joined} <= {
        f"SYN-CL-{index:06d}" for index in range(1, 9)
    }
    progress_values = [snapshot.progress for snapshot in outcome.progress_history]
    assert progress_values == sorted(progress_values)


@dataclass
class _FailingRunner:
    profile: AnalysisProfile = AnalysisProfile.TRANSACTION_ANOMALY

    def run(self, *, cancel_check, report_progress) -> ProfileExecution:
        cancel_check()
        raise ValueError("Контрольный сбой транзакционного профиля")


def test_one_failed_profile_returns_partial_result() -> None:
    outcome = UniversalAnalysisOrchestrator().run(
        _plan("partial", PlanProfileState.PLANNED, PlanProfileState.PLANNED),
        {
            AnalysisProfile.CLIENT_RISK: _client_runner(),
            AnalysisProfile.TRANSACTION_ANOMALY: _FailingRunner(),
        },
    )

    assert outcome.progress.state == AnalysisState.PARTIALLY_READY
    assert outcome.result is not None and outcome.result.state == "partially_ready"
    assert [item.state for item in outcome.result.profiles] == [
        ProfileState.READY,
        ProfileState.FAILED,
    ]
    assert outcome.client_records
    assert not outcome.transaction_records


def test_cancellation_is_observed_between_batches_and_discards_results() -> None:
    token = CancellationToken()

    def observer(progress) -> None:
        if progress.processed_records >= 3 and progress.state == AnalysisState.RUNNING:
            token.cancel()

    outcome = UniversalAnalysisOrchestrator(observer=observer).run(
        _plan("cancelled", PlanProfileState.PLANNED, PlanProfileState.SKIPPED),
        {AnalysisProfile.CLIENT_RISK: _client_runner()},
        cancellation=token,
    )

    assert outcome.cancelled is True
    assert outcome.result is None
    assert not outcome.client_records
    states = [snapshot.state for snapshot in outcome.progress_history]
    assert AnalysisState.CANCEL_REQUESTED in states
    assert states[-1] == AnalysisState.CANCELLED
