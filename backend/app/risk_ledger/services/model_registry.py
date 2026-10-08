from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from app.risk_ledger.core.model_loader import load_artifacts
from app.risk_ledger.services.transaction_model import load_transaction_artifacts


ModelProfile = Literal["client_risk", "transaction_anomaly"]
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
REQUIRED_GATES = (
    "holdout",
    "temporal_backtest",
    "drift",
    "leakage",
    "calibration",
    "alert_volume",
)


class ModelRegistryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GateResult:
    name: str
    passed: bool
    message: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "message": self.message,
            "evidence": self.evidence,
        }


def evaluate_candidate_gates(evaluation: dict[str, Any]) -> list[GateResult]:
    missing = [name for name in REQUIRED_GATES if not isinstance(evaluation.get(name), dict)]
    if missing:
        return [
            GateResult(name, False, "Отчёт проверки отсутствует.", {})
            for name in missing
        ] + [
            GateResult(name, bool(evaluation[name].get("passed")), "Проверка заявлена источником.", evaluation[name])
            for name in REQUIRED_GATES if name not in missing
        ]

    holdout = evaluation["holdout"]
    holdout_passed = (
        int(holdout.get("rows", 0)) >= 100
        and float(holdout.get("roc_auc", 0.0)) >= 0.65
        and float(holdout.get("pr_auc", 0.0)) >= 0.05
    )
    temporal = evaluation["temporal_backtest"]
    temporal_passed = (
        int(temporal.get("windows", 0)) >= 2
        and float(temporal.get("max_metric_drop", 1.0)) <= 0.10
    )
    drift = evaluation["drift"]
    drift_passed = float(drift.get("max_psi", 1.0)) <= 0.25
    leakage = evaluation["leakage"]
    leakage_passed = (
        not bool(leakage.get("target_as_feature", True))
        and not list(leakage.get("suspected_features", ["missing_evidence"]))
    )
    calibration = evaluation["calibration"]
    calibration_passed = (
        int(calibration.get("rows", 0)) >= 100
        and float(calibration.get("expected_calibration_error", 1.0)) <= 0.10
    )
    alert = evaluation["alert_volume"]
    alert_rate = float(alert.get("rate", -1.0))
    alert_delta = abs(float(alert.get("champion_delta", 1.0)))
    alert_passed = 0.001 <= alert_rate <= 0.25 and alert_delta <= 0.10
    values = {
        "holdout": (holdout_passed, "Holdout содержит не менее 100 строк; ROC-AUC ≥ 0.65 и PR-AUC ≥ 0.05."),
        "temporal_backtest": (temporal_passed, "Проверено не менее двух временных окон; падение метрики ≤ 0.10."),
        "drift": (drift_passed, "Максимальный PSI не превышает 0.25."),
        "leakage": (leakage_passed, "Цель и подозрительные прокси не используются как признаки."),
        "calibration": (calibration_passed, "ECE не превышает 0.10 на выборке от 100 строк."),
        "alert_volume": (alert_passed, "Доля сигналов 0.1–25%, отклонение от champion ≤ 10 п.п."),
    }
    return [GateResult(name, passed, message, dict(evaluation[name])) for name, (passed, message) in values.items()]


class ModelRegistry:
    def __init__(
        self,
        root: str | Path,
        *,
        candidate_root: str | Path | None = None,
        artifact_validator: Callable[[ModelProfile, Path], str] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "model-registry.sqlite3"
        self.candidate_root = Path(candidate_root).resolve() if candidate_root is not None else self.root
        self.artifact_validator = artifact_validator or self._validate_artifacts
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_versions (
                    profile TEXT NOT NULL,
                    version TEXT NOT NULL,
                    state TEXT NOT NULL,
                    artifact_path TEXT NOT NULL,
                    evaluation_json TEXT NOT NULL,
                    gates_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(profile, version)
                );
                CREATE TABLE IF NOT EXISTS champions (
                    profile TEXT PRIMARY KEY,
                    version TEXT NOT NULL,
                    activated_at REAL NOT NULL,
                    activated_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_audit (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile TEXT NOT NULL,
                    version TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    previous_version TEXT,
                    occurred_at REAL NOT NULL
                );
                """
            )
            connection.commit()

    @staticmethod
    def _validate_artifacts(profile: ModelProfile, path: Path) -> str:
        if profile == "client_risk":
            _, manifest = load_artifacts(path)
            return manifest.model_version
        _, manifest = load_transaction_artifacts(path)
        return str(manifest["model_version"])

    def candidate_path(self, profile: ModelProfile, version: str) -> Path:
        if profile not in {"client_risk", "transaction_anomaly"}:
            raise ModelRegistryError("Unknown model profile.")
        if not VERSION_PATTERN.fullmatch(version):
            raise ModelRegistryError("Invalid model version.")
        path = (self.candidate_root / "candidates" / profile / version).resolve()
        if self.candidate_root not in path.parents:
            raise ModelRegistryError("Unsafe candidate path.")
        return path

    def bootstrap_champion(self, profile: ModelProfile, version: str, artifact_path: str | Path) -> None:
        """Register an already loaded deployment as the initial champion."""
        if self.champion(profile) is not None:
            return
        path = Path(artifact_path).resolve()
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO model_versions(
                    profile,version,state,artifact_path,evaluation_json,gates_json,created_at
                ) VALUES (?,?,?,?,?,?,?)""",
                (profile, version, "champion", str(path), "{}", "[]", now),
            )
            connection.execute(
                "INSERT INTO champions(profile,version,activated_at,activated_by) VALUES (?,?,?,?)",
                (profile, version, now, "bootstrap"),
            )
            connection.execute(
                "INSERT INTO model_audit(profile,version,action,actor,reason,previous_version,occurred_at) VALUES (?,?,?,?,?,?,?)",
                (profile, version, "bootstrap", "system", "Existing production model imported as champion", None, now),
            )
            connection.commit()

    def register_candidate(self, profile: ModelProfile, version: str, evaluation: dict[str, Any]) -> dict[str, Any]:
        path = self.candidate_path(profile, version)
        if not path.is_dir():
            raise ModelRegistryError(f"Candidate artifacts were not found: {path}")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM model_versions WHERE profile = ? AND version = ?",
                (profile, version),
            ).fetchone()
        if existing is not None:
            raise ModelRegistryError("This model version is already registered and cannot be overwritten.")
        artifact_version = self.artifact_validator(profile, path)
        if artifact_version != version:
            raise ModelRegistryError("Artifact manifest version does not match the candidate version.")
        gates = evaluate_candidate_gates(evaluation)
        state = "eligible" if all(gate.passed for gate in gates) else "blocked"
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO model_versions(
                    profile,version,state,artifact_path,evaluation_json,gates_json,created_at
                ) VALUES (?,?,?,?,?,?,?)""",
                (profile, version, state, str(path), json.dumps(evaluation, ensure_ascii=False), json.dumps([gate.to_dict() for gate in gates], ensure_ascii=False), now),
            )
            connection.execute(
                "INSERT INTO model_audit(profile,version,action,actor,reason,previous_version,occurred_at) VALUES (?,?,?,?,?,?,?)",
                (profile, version, "candidate_registered", "system", "Automated gate evaluation", None, now),
            )
            connection.commit()
        return self.get_version(profile, version)

    def get_version(self, profile: ModelProfile, version: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM model_versions WHERE profile = ? AND version = ?", (profile, version)
            ).fetchone()
        if row is None:
            raise ModelRegistryError("Model version was not found.")
        result = dict(row)
        result["evaluation"] = json.loads(result.pop("evaluation_json"))
        result["gates"] = json.loads(result.pop("gates_json"))
        return result

    def list_versions(self, profile: ModelProfile | None = None) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT profile,version FROM model_versions" + (" WHERE profile = ?" if profile else "") + " ORDER BY created_at DESC",
                () if profile is None else (profile,),
            ).fetchall()
        return [self.get_version(row["profile"], row["version"]) for row in rows]

    def champion(self, profile: ModelProfile) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM champions WHERE profile = ?", (profile,)).fetchone()
        return None if row is None else dict(row)

    def activate(self, profile: ModelProfile, version: str, *, actor: str, reason: str, confirmation: str) -> dict[str, Any]:
        candidate = self.get_version(profile, version)
        if candidate["state"] not in {"eligible", "retired"}:
            raise ModelRegistryError("Candidate has not passed every required gate.")
        if confirmation != f"ACTIVATE {version}":
            raise ModelRegistryError("Manual activation confirmation does not match.")
        if not actor.strip() or not reason.strip():
            raise ModelRegistryError("Actor and activation reason are required.")
        validated_version = self.artifact_validator(profile, Path(candidate["artifact_path"]))
        if validated_version != version:
            raise ModelRegistryError("Candidate artifacts changed after gate evaluation.")
        previous = self.champion(profile)
        now = time.time()
        with self._connect() as connection:
            if previous:
                connection.execute("UPDATE model_versions SET state = 'retired' WHERE profile = ? AND version = ?", (profile, previous["version"]))
            connection.execute("UPDATE model_versions SET state = 'champion' WHERE profile = ? AND version = ?", (profile, version))
            connection.execute(
                "INSERT OR REPLACE INTO champions(profile,version,activated_at,activated_by) VALUES (?,?,?,?)",
                (profile, version, now, actor.strip()),
            )
            connection.execute(
                "INSERT INTO model_audit(profile,version,action,actor,reason,previous_version,occurred_at) VALUES (?,?,?,?,?,?,?)",
                (profile, version, "activated", actor.strip(), reason.strip(), None if previous is None else previous["version"], now),
            )
            connection.commit()
        return self.get_version(profile, version)

    def rollback(self, profile: ModelProfile, target_version: str, *, actor: str, reason: str, confirmation: str) -> dict[str, Any]:
        if confirmation != f"ROLLBACK {target_version}":
            raise ModelRegistryError("Manual rollback confirmation does not match.")
        target = self.get_version(profile, target_version)
        if target["state"] not in {"retired", "eligible"}:
            raise ModelRegistryError("Rollback target is not an eligible previous version.")
        current = self.champion(profile)
        activated = self.activate(profile, target_version, actor=actor, reason=reason, confirmation=f"ACTIVATE {target_version}")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO model_audit(profile,version,action,actor,reason,previous_version,occurred_at) VALUES (?,?,?,?,?,?,?)",
                (profile, target_version, "rollback", actor.strip(), reason.strip(), None if current is None else current["version"], time.time()),
            )
            connection.commit()
        return activated

    def audit(self, profile: ModelProfile | None = None) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM model_audit" + (" WHERE profile = ?" if profile else "") + " ORDER BY event_id DESC",
                () if profile is None else (profile,),
            )]
