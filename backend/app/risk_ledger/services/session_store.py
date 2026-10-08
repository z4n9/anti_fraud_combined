from __future__ import annotations

import csv
import io
import json
import math
import shutil
import sqlite3
import time
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.risk_ledger.core.config import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, SESSION_TTL_SECONDS
from app.risk_ledger.domain.models import AnalysisMetrics, ProbabilityBin, ResultPage, RiskDistribution
from app.risk_ledger.services.export_service import safe_csv_cell


RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})


class SessionNotFoundError(KeyError):
    pass


class SessionStore:
    """Ephemeral, one-SQLite-file-per-analysis result storage."""

    def __init__(self, root: str | Path, *, ttl_seconds: int = SESSION_TTL_SECONDS):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = int(ttl_seconds)
        self._initialize_feedback_store()

    @property
    def _feedback_database(self) -> Path:
        return self.root / "confirmed-feedback.sqlite3"

    def _initialize_feedback_store(self) -> None:
        with self._connect(self._feedback_database) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS confirmed_labels (
                    analysis_id TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    human_label INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    profile TEXT,
                    model_version TEXT,
                    risk_probability REAL,
                    confirmed_at REAL NOT NULL,
                    PRIMARY KEY(analysis_id, entity_key)
                );
                CREATE INDEX IF NOT EXISTS idx_confirmed_labels_time
                    ON confirmed_labels(confirmed_at DESC);
                """
            )
            connection.commit()

    def _session_dir(self, session_id: str) -> Path:
        try:
            normalized = str(uuid.UUID(session_id))
        except ValueError as error:
            raise SessionNotFoundError(session_id) from error
        return self.root / normalized

    def set_owner(self, session_id: str, owner_user_id: int | None) -> None:
        with self._connect(self._database_path(session_id)) as connection:
            connection.execute("INSERT OR REPLACE INTO session_metadata(key,value) VALUES (?,?)",
                               ("owner_user_id", json.dumps(owner_user_id)))
            connection.commit()

    def _database_path(self, session_id: str) -> Path:
        directory = self._session_dir(session_id)
        path = directory / "results.sqlite3"
        if not path.is_file():
            raise SessionNotFoundError(session_id)
        return path

    @staticmethod
    def _open_connection(
        path: Path,
        *,
        check_same_thread: bool = True,
    ) -> sqlite3.Connection:
        connection = sqlite3.connect(path, check_same_thread=check_same_thread)
        connection.row_factory = sqlite3.Row
        return connection

    @classmethod
    @contextmanager
    def _connect(cls, path: Path) -> Iterator[sqlite3.Connection]:
        connection = cls._open_connection(path)
        try:
            yield connection
        finally:
            connection.close()

    def create_session(
        self,
        rows: Iterable[dict[str, Any]],
        *,
        threshold: float,
        metrics: AnalysisMetrics | None = None,
        model_version: str = "",
        session_id: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> str:
        if not 0 <= threshold <= 1:
            raise ValueError("threshold must be between 0 and 1.")
        session_id = str(uuid.uuid4()) if session_id is None else str(uuid.UUID(session_id))
        directory = self.root / session_id
        directory.mkdir(parents=False, exist_ok=False)
        database = directory / "results.sqlite3"

        with self._connect(database) as connection:
            connection.executescript(
                """
                CREATE TABLE session_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE results (
                    row_position INTEGER PRIMARY KEY,
                    record_id TEXT NOT NULL,
                    risk_probability REAL NOT NULL,
                    risk_level TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX idx_results_risk
                    ON results(risk_probability DESC, row_position ASC);
                CREATE INDEX idx_results_level_risk
                    ON results(risk_level, risk_probability DESC, row_position ASC);
                CREATE TABLE entity_results (
                    profile TEXT NOT NULL,
                    row_position INTEGER NOT NULL,
                    record_id TEXT NOT NULL,
                    risk_probability REAL NOT NULL,
                    risk_level TEXT NOT NULL,
                    requires_review INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(profile, row_position)
                );
                CREATE INDEX idx_entity_results_risk
                    ON entity_results(profile, risk_probability DESC, row_position ASC);
                CREATE INDEX idx_entity_results_review
                    ON entity_results(profile, requires_review, risk_probability DESC);
                CREATE TABLE relationships (
                    row_position INTEGER PRIMARY KEY,
                    relationship_id TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    from_id TEXT NOT NULL,
                    to_id TEXT NOT NULL,
                    risk_probability REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX idx_relationships_risk
                    ON relationships(risk_probability DESC, row_position ASC);
                CREATE INDEX idx_relationships_kind
                    ON relationships(kind, risk_probability DESC, row_position ASC);
                CREATE TABLE investigation_feedback (
                    entity_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    comment TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE investigation_audit (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_key TEXT NOT NULL,
                    previous_status TEXT,
                    status TEXT NOT NULL,
                    comment TEXT NOT NULL,
                    occurred_at REAL NOT NULL
                );
                CREATE INDEX idx_investigation_audit_entity
                    ON investigation_audit(entity_key, event_id DESC);
                CREATE TABLE confirmed_feedback_labels (
                    entity_key TEXT PRIMARY KEY,
                    human_label INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    profile TEXT,
                    model_version TEXT,
                    risk_probability REAL,
                    confirmed_at REAL NOT NULL
                );
                """
            )
            metadata = {
                "created_at": time.time(),
                "threshold": float(threshold),
                "model_version": model_version,
                "metrics": None if metrics is None else metrics.to_dict(),
                "summary": summary or {},
            }
            connection.executemany(
                "INSERT INTO session_metadata(key, value) VALUES (?, ?)",
                [
                    (key, json.dumps(value, ensure_ascii=False, allow_nan=False))
                    for key, value in metadata.items()
                ],
            )
            prepared = []
            for position, row in enumerate(rows):
                probability = float(row["risk_probability"])
                level = str(row["risk_level"])
                if not 0 <= probability <= 1 or level not in RISK_LEVELS:
                    raise ValueError("Invalid prediction row.")
                prepared.append(
                    (
                        position,
                        str(row["record_id"]),
                        probability,
                        level,
                        json.dumps(row, ensure_ascii=False, allow_nan=False),
                    )
                )
            connection.executemany(
                """
                INSERT INTO results(
                    row_position, record_id, risk_probability, risk_level, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                prepared,
            )
            connection.commit()
        return session_id

    def create_universal_session(
        self,
        *,
        session_id: str,
        client_records: Iterable[dict[str, Any]],
        transaction_records: Iterable[dict[str, Any]],
        relationships: Iterable[dict[str, Any]],
        result: dict[str, Any],
        inventory: dict[str, Any],
        mapping: dict[str, Any],
        plan: dict[str, Any],
        threshold: float = 0.5,
    ) -> str:
        clients = [self._json_safe(dict(item)) for item in client_records]
        transactions = [self._json_safe(dict(item)) for item in transaction_records]
        links = [self._json_safe(dict(item)) for item in relationships]
        legacy_rows = clients or transactions
        self.create_session(
            legacy_rows,
            threshold=threshold,
            model_version="universal-1.0",
            session_id=session_id,
            summary={
                "rows": len(legacy_rows),
                "requires_review": sum(bool(item.get("requires_review")) for item in legacy_rows),
                "client_records": len(clients),
                "transaction_records": len(transactions),
                "relationships": len(links),
            },
        )
        database = self._database_path(session_id)
        with self._connect(database) as connection:
            for profile, records in (("client_risk", clients), ("transaction_anomaly", transactions)):
                prepared = []
                for position, item in enumerate(records):
                    probability = float(item.get("risk_probability", 0.0))
                    level = str(item.get("risk_level", "low"))
                    prepared.append((
                        profile,
                        position,
                        str(item.get("record_id", item.get("transaction_id", f"row-{position + 1}"))),
                        probability,
                        level,
                        int(bool(item.get("requires_review"))),
                        json.dumps(item, ensure_ascii=False, allow_nan=False),
                    ))
                connection.executemany(
                    "INSERT INTO entity_results(profile,row_position,record_id,risk_probability,risk_level,requires_review,payload_json) VALUES (?,?,?,?,?,?,?)",
                    prepared,
                )
            connection.executemany(
                "INSERT INTO relationships(row_position,relationship_id,kind,from_id,to_id,risk_probability,payload_json) VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        position,
                        str(item["relationship_id"]),
                        str(item["kind"]),
                        str(item["from_id"]),
                        str(item["to_id"]),
                        float(item.get("risk_signal_score", 0.0)),
                        json.dumps(item, ensure_ascii=False, allow_nan=False),
                    )
                    for position, item in enumerate(links)
                ],
            )
            for key, value in {
                "universal_result": result,
                "inventory": inventory,
                "mapping": mapping,
                "plan": plan,
            }.items():
                connection.execute(
                    "INSERT OR REPLACE INTO session_metadata(key,value) VALUES (?,?)",
                    (key, json.dumps(self._json_safe(value), ensure_ascii=False, allow_nan=False)),
                )
            connection.commit()
        return session_id

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if hasattr(value, "item"):
            return cls._json_safe(value.item())
        if hasattr(value, "isoformat") and not isinstance(value, str):
            return value.isoformat()
        return value

    def get_entity_page(
        self,
        session_id: str,
        *,
        profile: str,
        page: int = 1,
        page_size: int = 50,
        risk_level: str | None = None,
        requires_review: bool | None = None,
        probability_min: float | None = None,
        probability_max: float | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError("Invalid pagination.")
        if risk_level is not None and risk_level not in RISK_LEVELS:
            raise ValueError("Unknown risk level filter.")
        if probability_min is not None and not 0 <= probability_min <= 1:
            raise ValueError("probability_min must be between 0 and 1.")
        if probability_max is not None and not 0 <= probability_max <= 1:
            raise ValueError("probability_max must be between 0 and 1.")
        if probability_min is not None and probability_max is not None and probability_min > probability_max:
            raise ValueError("probability_min must not exceed probability_max.")
        conditions = ["profile = ?"]
        parameters: list[Any] = [profile]
        if risk_level is not None:
            conditions.append("risk_level = ?")
            parameters.append(risk_level)
        if requires_review is not None:
            conditions.append("requires_review = ?")
            parameters.append(int(requires_review))
        if probability_min is not None:
            conditions.append("risk_probability >= ?")
            parameters.append(probability_min)
        if probability_max is not None:
            conditions.append("risk_probability <= ?")
            parameters.append(probability_max)
        if (search or "").strip():
            escaped = self._escape_like(search.strip())
            conditions.append("LOWER(record_id) LIKE LOWER(?) ESCAPE '\\'")
            parameters.append(f"%{escaped}%")
        where = " WHERE " + " AND ".join(conditions)
        with self._connect(self._database_path(session_id)) as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) AS total FROM entity_results" + where, parameters
            ).fetchone()["total"])
            rows = connection.execute(
                "SELECT payload_json FROM entity_results" + where
                + " ORDER BY risk_probability DESC, row_position ASC LIMIT ? OFFSET ?",
                [*parameters, page_size, (page - 1) * page_size],
            ).fetchall()
        return {
            "items": [json.loads(row["payload_json"]) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def get_relationship_page(
        self,
        session_id: str,
        *,
        page: int = 1,
        page_size: int = 50,
        kind: str | None = None,
        entity_id: str | None = None,
        probability_min: float | None = None,
    ) -> dict[str, Any]:
        if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError("Invalid pagination.")
        if probability_min is not None and not 0 <= probability_min <= 1:
            raise ValueError("probability_min must be between 0 and 1.")
        conditions: list[str] = []
        parameters: list[Any] = []
        if (kind or "").strip():
            conditions.append("kind = ?")
            parameters.append(kind.strip())
        if (entity_id or "").strip():
            conditions.append("(from_id = ? OR to_id = ?)")
            parameters.extend([entity_id.strip(), entity_id.strip()])
        if probability_min is not None:
            conditions.append("risk_probability >= ?")
            parameters.append(probability_min)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connect(self._database_path(session_id)) as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) AS total FROM relationships" + where, parameters
            ).fetchone()["total"])
            rows = connection.execute(
                "SELECT payload_json FROM relationships" + where
                + " ORDER BY risk_probability DESC, row_position ASC LIMIT ? OFFSET ?",
                [*parameters, page_size, (page - 1) * page_size],
            ).fetchall()
        return {"items": [json.loads(row["payload_json"]) for row in rows], "total": total, "page": page, "page_size": page_size}

    @staticmethod
    def _feedback_digest(entity_key: str) -> str:
        import hashlib

        return hashlib.sha256(entity_key.encode("utf-8")).hexdigest()

    @staticmethod
    def _ensure_investigation_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS investigation_feedback (
                entity_key TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                comment TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS investigation_audit (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_key TEXT NOT NULL,
                previous_status TEXT,
                status TEXT NOT NULL,
                comment TEXT NOT NULL,
                occurred_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_investigation_audit_entity
                ON investigation_audit(entity_key, event_id DESC);
            CREATE TABLE IF NOT EXISTS confirmed_feedback_labels (
                entity_key TEXT PRIMARY KEY,
                human_label INTEGER NOT NULL,
                source TEXT NOT NULL,
                profile TEXT,
                model_version TEXT,
                risk_probability REAL,
                confirmed_at REAL NOT NULL
            );
            """
        )

    def save_feedback(self, session_id: str, entity_key: str, status: str, comment: str) -> dict[str, Any]:
        if status not in {"new", "in_review", "confirmed", "dismissed"}:
            raise ValueError("Unknown investigation status.")
        normalized_key = entity_key.strip()
        if not normalized_key:
            raise ValueError("entity_key must not be empty.")

        digest = self._feedback_digest(normalized_key)
        updated_at = time.time()
        with self._connect(self._database_path(session_id)) as connection:
            self._ensure_investigation_schema(connection)
            previous = connection.execute(
                "SELECT status FROM investigation_feedback WHERE entity_key = ?", (digest,)
            ).fetchone()
            connection.execute(
                "INSERT OR REPLACE INTO investigation_feedback(entity_key,status,comment,updated_at) VALUES (?,?,?,?)",
                (digest, status, comment, updated_at),
            )
            connection.execute(
                "INSERT INTO investigation_audit(entity_key,previous_status,status,comment,occurred_at) VALUES (?,?,?,?,?)",
                (digest, None if previous is None else previous["status"], status, comment, updated_at),
            )
            if status in {"confirmed", "dismissed"}:
                entity = connection.execute(
                    "SELECT profile,risk_probability FROM entity_results WHERE record_id = ? LIMIT 1",
                    (normalized_key,),
                ).fetchone()
                metadata = connection.execute(
                    "SELECT value FROM session_metadata WHERE key = 'model_version'"
                ).fetchone()
                model_version = json.loads(metadata["value"]) if metadata else None
                connection.execute(
                    """INSERT OR REPLACE INTO confirmed_feedback_labels(
                        entity_key,human_label,source,profile,model_version,risk_probability,confirmed_at
                    ) VALUES (?,?,?,?,?,?,?)""",
                    (
                        digest,
                        1 if status == "confirmed" else 0,
                        "human_confirmed",
                        None if entity is None else entity["profile"],
                        model_version,
                        None if entity is None else entity["risk_probability"],
                        updated_at,
                    ),
                )
            else:
                connection.execute(
                    "DELETE FROM confirmed_feedback_labels WHERE entity_key = ?", (digest,)
                )
            connection.commit()
        if status in {"confirmed", "dismissed"}:
            label = self.get_feedback(session_id, normalized_key)["confirmed_label"]
            with self._connect(self._feedback_database) as feedback_connection:
                feedback_connection.execute(
                    """INSERT OR REPLACE INTO confirmed_labels(
                        analysis_id,entity_key,human_label,source,profile,model_version,
                        risk_probability,confirmed_at
                    ) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        session_id,
                        digest,
                        label["human_label"],
                        label["source"],
                        label["profile"],
                        label["model_version"],
                        label["risk_probability"],
                        label["confirmed_at"],
                    ),
                )
                feedback_connection.commit()
        else:
            with self._connect(self._feedback_database) as feedback_connection:
                feedback_connection.execute(
                    "DELETE FROM confirmed_labels WHERE analysis_id = ? AND entity_key = ?",
                    (session_id, digest),
                )
                feedback_connection.commit()
        return self.get_feedback(session_id, normalized_key)

    def get_feedback(self, session_id: str, entity_key: str) -> dict[str, Any]:
        digest = self._feedback_digest(entity_key.strip())
        with self._connect(self._database_path(session_id)) as connection:
            self._ensure_investigation_schema(connection)
            current = connection.execute(
                "SELECT status,comment,updated_at FROM investigation_feedback WHERE entity_key = ?",
                (digest,),
            ).fetchone()
            history = connection.execute(
                """SELECT event_id,previous_status,status,comment,occurred_at
                FROM investigation_audit WHERE entity_key = ? ORDER BY event_id DESC""",
                (digest,),
            ).fetchall()
            label = connection.execute(
                """SELECT human_label,source,profile,model_version,risk_probability,confirmed_at
                FROM confirmed_feedback_labels WHERE entity_key = ?""",
                (digest,),
            ).fetchone()
        return {
            "status": "new" if current is None else current["status"],
            "comment": "" if current is None else current["comment"],
            "updated_at": None if current is None else current["updated_at"],
            "confirmed_label": None if label is None else dict(label),
            "history": [dict(item) for item in history],
        }

    def feedback_summary(self, session_id: str) -> dict[str, Any]:
        with self._connect(self._database_path(session_id)) as connection:
            self._ensure_investigation_schema(connection)
            statuses = {row["status"]: row["count"] for row in connection.execute(
                "SELECT status,COUNT(*) AS count FROM investigation_feedback GROUP BY status"
            )}
            labels = [dict(row) for row in connection.execute(
                """SELECT entity_key,human_label,source,profile,model_version,risk_probability,confirmed_at
                FROM confirmed_feedback_labels ORDER BY confirmed_at DESC"""
            )]
        return {"statuses": statuses, "confirmed_labels": labels, "total_confirmed": len(labels)}

    def confirmed_feedback_labels(self) -> list[dict[str, Any]]:
        """Return only anonymized, explicitly human-confirmed labels for future training."""
        with self._connect(self._feedback_database) as connection:
            return [dict(row) for row in connection.execute(
                """SELECT analysis_id,entity_key,human_label,source,profile,model_version,
                risk_probability,confirmed_at FROM confirmed_labels ORDER BY confirmed_at ASC"""
            )]

    def iter_entity_payloads(self, session_id: str, profile: str | None = None, review_only: bool = False) -> Iterator[dict[str, Any]]:
        conditions: list[str] = []
        parameters: list[Any] = []
        if profile is not None:
            conditions.append("profile = ?")
            parameters.append(profile)
        if review_only:
            conditions.append("requires_review = 1")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connect(self._database_path(session_id)) as connection:
            for row in connection.execute(
                "SELECT profile,payload_json FROM entity_results" + where
                + " ORDER BY risk_probability DESC, profile ASC, row_position ASC",
                parameters,
            ):
                item = json.loads(row["payload_json"])
                item["profile"] = row["profile"]
                yield item

    def iter_relationship_payloads(self, session_id: str) -> Iterator[dict[str, Any]]:
        with self._connect(self._database_path(session_id)) as connection:
            for row in connection.execute(
                "SELECT payload_json FROM relationships ORDER BY risk_probability DESC, row_position ASC"
            ):
                yield json.loads(row["payload_json"])

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def metric_inputs(
        self,
        session_id: str,
        target_column: str,
    ) -> tuple[list[Any] | None, list[float]]:
        targets: list[Any] = []
        probabilities: list[float] = []
        with self._connect(self._database_path(session_id)) as connection:
            records = connection.execute(
                "SELECT payload_json FROM results ORDER BY row_position ASC"
            )
            target_present = True
            for record in records:
                item = json.loads(record["payload_json"])
                probabilities.append(float(item["risk_probability"]))
                if target_column in item:
                    targets.append(item[target_column])
                else:
                    target_present = False
        return (targets if target_present else None), probabilities

    def save_upload(self, session_id: str, content: bytes) -> Path:
        directory = self._session_dir(session_id)
        if not (directory / "results.sqlite3").is_file():
            raise SessionNotFoundError(session_id)
        path = directory / "upload.csv"
        path.write_bytes(content)
        return path

    def get_metadata(self, session_id: str) -> dict[str, Any]:
        with self._connect(self._database_path(session_id)) as connection:
            records = connection.execute(
                "SELECT key, value FROM session_metadata"
            ).fetchall()
        return {record["key"]: json.loads(record["value"]) for record in records}

    def get_page(
        self,
        session_id: str,
        *,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        risk_filter: str | None = None,
        threshold: float | None = None,
        requires_review: bool | None = None,
        probability_min: float | None = None,
        probability_max: float | None = None,
        record_id: str | None = None,
    ) -> ResultPage:
        if page < 1:
            raise ValueError("page must be at least 1.")
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}.")
        if risk_filter is not None and risk_filter not in RISK_LEVELS:
            raise ValueError("Unknown risk level filter.")
        if probability_min is not None and not 0 <= probability_min <= 1:
            raise ValueError("probability_min must be between 0 and 1.")
        if probability_max is not None and not 0 <= probability_max <= 1:
            raise ValueError("probability_max must be between 0 and 1.")
        if (
            probability_min is not None
            and probability_max is not None
            and probability_min > probability_max
        ):
            raise ValueError("probability_min must not exceed probability_max.")

        metadata = self.get_metadata(session_id)
        active_threshold = (
            float(metadata["threshold"]) if threshold is None else float(threshold)
        )
        if not 0 <= active_threshold <= 1:
            raise ValueError("threshold must be between 0 and 1.")

        conditions: list[str] = []
        parameters: list[Any] = []
        if risk_filter:
            conditions.append("risk_level = ?")
            parameters.append(risk_filter)
        if requires_review is not None:
            conditions.append(
                "risk_probability >= ?" if requires_review else "risk_probability < ?"
            )
            parameters.append(active_threshold)
        if probability_min is not None:
            conditions.append("risk_probability >= ?")
            parameters.append(float(probability_min))
        if probability_max is not None:
            conditions.append("risk_probability <= ?")
            parameters.append(float(probability_max))
        normalized_record_id = (record_id or "").strip()
        if normalized_record_id:
            escaped_record_id = (
                normalized_record_id.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            conditions.append("LOWER(record_id) LIKE LOWER(?) ESCAPE '\\'")
            parameters.append(f"%{escaped_record_id}%")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connect(self._database_path(session_id)) as connection:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) AS total FROM results" + where,
                    parameters,
                ).fetchone()["total"]
            )
            records = connection.execute(
                "SELECT payload_json FROM results"
                + where
                + " ORDER BY risk_probability DESC, row_position ASC LIMIT ? OFFSET ?",
                [*parameters, page_size, (page - 1) * page_size],
            ).fetchall()

        items = []
        for record in records:
            item = json.loads(record["payload_json"])
            item["requires_review"] = (
                float(item["risk_probability"]) >= active_threshold
            )
            items.append(item)
        return ResultPage(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            threshold=active_threshold,
        )

    def get_distribution(
        self,
        session_id: str,
        *,
        bins: int = 10,
        threshold: float | None = None,
    ) -> RiskDistribution:
        if not 2 <= bins <= 50:
            raise ValueError("bins must be between 2 and 50.")
        metadata = self.get_metadata(session_id)
        active_threshold = (
            float(metadata["threshold"]) if threshold is None else float(threshold)
        )
        if not 0 <= active_threshold <= 1:
            raise ValueError("threshold must be between 0 and 1.")

        counts = {level: 0 for level in ("low", "medium", "high", "critical")}
        histogram_counts = [0] * bins
        with self._connect(self._database_path(session_id)) as connection:
            records = connection.execute(
                "SELECT risk_probability, risk_level FROM results"
            )
            for record in records:
                probability = float(record["risk_probability"])
                counts[str(record["risk_level"])] += 1
                index = min(int(probability * bins), bins - 1)
                histogram_counts[index] += 1

        histogram = [
            ProbabilityBin(
                from_value=index / bins,
                to_value=(index + 1) / bins,
                count=count,
            )
            for index, count in enumerate(histogram_counts)
        ]
        return RiskDistribution(
            analysis_id=session_id,
            threshold=active_threshold,
            risk_counts=counts,
            probability_histogram=histogram,
        )

    def iter_csv(
        self,
        session_id: str,
        *,
        threshold: float | None = None,
        requires_review: bool = False,
    ) -> Iterator[str]:
        metadata = self.get_metadata(session_id)
        active_threshold = (
            float(metadata["threshold"]) if threshold is None else float(threshold)
        )
        if not 0 <= active_threshold <= 1:
            raise ValueError("threshold must be between 0 and 1.")

        # Starlette advances sync streaming iterators in a worker pool and may
        # resume consecutive iterations on different threads. Access remains
        # sequential, so this connection can safely cross those worker threads.
        connection = self._open_connection(
            self._database_path(session_id),
            check_same_thread=False,
        )
        try:
            first = connection.execute(
                "SELECT payload_json FROM results "
                "ORDER BY risk_probability DESC, row_position ASC LIMIT 1"
            ).fetchone()
            if first is None:
                return
            first_item = json.loads(first["payload_json"])
            fieldnames = list(first_item)
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)

            if requires_review:
                cursor = connection.execute(
                    "SELECT payload_json FROM results "
                    "WHERE risk_probability >= ? "
                    "ORDER BY risk_probability DESC, row_position ASC",
                    (active_threshold,),
                )
            else:
                cursor = connection.execute(
                    "SELECT payload_json FROM results "
                    "ORDER BY risk_probability DESC, row_position ASC"
                )

            for record in cursor:
                item = json.loads(record["payload_json"])
                item["requires_review"] = (
                    float(item["risk_probability"]) >= active_threshold
                )
                serialized = {
                    key: (
                        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                        if isinstance(value, (dict, list))
                        else value
                    )
                    for key, value in item.items()
                }
                writer.writerow(
                    {key: safe_csv_cell(value) for key, value in serialized.items()}
                )
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)
        finally:
            connection.close()

    def delete_session(self, session_id: str) -> None:
        directory = self._session_dir(session_id)
        if not directory.is_dir():
            raise SessionNotFoundError(session_id)
        if directory.parent != self.root:
            raise RuntimeError("Unsafe session path.")
        shutil.rmtree(directory)

    def cleanup_expired(self, *, now: float | None = None) -> list[str]:
        current_time = time.time() if now is None else float(now)
        deleted: list[str] = []
        for directory in self.root.iterdir():
            if not directory.is_dir():
                continue
            try:
                session_id = str(uuid.UUID(directory.name))
                metadata = self.get_metadata(session_id)
                created_at = float(metadata["created_at"])
            except (ValueError, KeyError, SessionNotFoundError, sqlite3.Error):
                continue
            if current_time - created_at > self.ttl_seconds:
                self.delete_session(session_id)
                deleted.append(session_id)
        return deleted

    def cleanup_all(self) -> list[str]:
        deleted: list[str] = []
        for directory in self.root.iterdir():
            if not directory.is_dir():
                continue
            try:
                session_id = str(uuid.UUID(directory.name))
            except ValueError:
                continue
            self.delete_session(session_id)
            deleted.append(session_id)
        return deleted
