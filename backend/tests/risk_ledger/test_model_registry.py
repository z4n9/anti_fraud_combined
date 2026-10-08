from __future__ import annotations

from pathlib import Path

import pytest

from app.risk_ledger.services.model_registry import ModelRegistry, ModelRegistryError, evaluate_candidate_gates


def passing_evaluation() -> dict:
    return {
        "holdout": {"rows": 500, "roc_auc": 0.82, "pr_auc": 0.22},
        "temporal_backtest": {"windows": 3, "max_metric_drop": 0.04},
        "drift": {"max_psi": 0.12},
        "leakage": {"target_as_feature": False, "suspected_features": []},
        "calibration": {"rows": 500, "expected_calibration_error": 0.06},
        "alert_volume": {"rate": 0.04, "champion_delta": 0.01},
    }


def registry(tmp_path: Path) -> ModelRegistry:
    return ModelRegistry(
        tmp_path / "state",
        candidate_root=tmp_path / "artifacts",
        artifact_validator=lambda _profile, path: path.name,
    )


def candidate(store: ModelRegistry, profile: str, version: str) -> None:
    store.candidate_path(profile, version).mkdir(parents=True)


def test_missing_or_failed_gate_blocks_activation(tmp_path: Path) -> None:
    store = registry(tmp_path)
    candidate(store, "client_risk", "2.0.0")
    result = store.register_candidate("client_risk", "2.0.0", {"holdout": {"passed": True}})
    assert result["state"] == "blocked"
    assert {gate["name"] for gate in result["gates"]} == {
        "holdout", "temporal_backtest", "drift", "leakage", "calibration", "alert_volume",
    }
    with pytest.raises(ModelRegistryError, match="every required gate"):
        store.activate("client_risk", "2.0.0", actor="analyst", reason="release", confirmation="ACTIVATE 2.0.0")


def test_activation_requires_exact_manual_confirmation_and_rollback_is_audited(tmp_path: Path) -> None:
    store = registry(tmp_path)
    store.bootstrap_champion("client_risk", "1.0.0", tmp_path / "initial")
    for version in ("2.0.0", "3.0.0"):
        candidate(store, "client_risk", version)
        assert store.register_candidate("client_risk", version, passing_evaluation())["state"] == "eligible"
    with pytest.raises(ModelRegistryError, match="confirmation"):
        store.activate("client_risk", "2.0.0", actor="owner", reason="passed", confirmation="yes")
    store.activate("client_risk", "2.0.0", actor="owner", reason="passed", confirmation="ACTIVATE 2.0.0")
    store.activate("client_risk", "3.0.0", actor="owner", reason="passed", confirmation="ACTIVATE 3.0.0")
    rolled_back = store.rollback("client_risk", "2.0.0", actor="owner", reason="incident", confirmation="ROLLBACK 2.0.0")
    assert rolled_back["state"] == "champion"
    assert store.champion("client_risk")["version"] == "2.0.0"
    assert [event["action"] for event in store.audit("client_risk")][:2] == ["rollback", "activated"]


def test_gate_thresholds_are_computed_internally() -> None:
    report = passing_evaluation()
    assert all(gate.passed for gate in evaluate_candidate_gates(report))
    report["drift"]["max_psi"] = 0.26
    gates = {gate.name: gate for gate in evaluate_candidate_gates(report)}
    assert not gates["drift"].passed


def test_registered_version_cannot_be_overwritten(tmp_path: Path) -> None:
    store = registry(tmp_path)
    candidate(store, "transaction_anomaly", "1.1.0")
    store.register_candidate("transaction_anomaly", "1.1.0", passing_evaluation())
    with pytest.raises(ModelRegistryError, match="cannot be overwritten"):
        store.register_candidate("transaction_anomaly", "1.1.0", passing_evaluation())
