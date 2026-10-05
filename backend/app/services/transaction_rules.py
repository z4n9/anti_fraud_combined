from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.services.transaction_features import (
    CONTROL_ONLY_COLUMNS,
    TransactionFeatureReference,
    iter_transaction_csv,
)
from app.services.transaction_model import TransactionArtifactError, load_transaction_artifacts, score_transaction_file


SCENARIO_ENGINE_VERSION = "1.1.0"
RISK_FORMULA_VERSION = "1.1"


def default_rule_reference() -> TransactionFeatureReference:
    """Fixed demo baseline; never derive a decision baseline from the scored file."""
    return TransactionFeatureReference(
        schema_version="1.0", amount_median=50_000.0, amount_mad=25_000.0,
        categories={"currency": [], "channel": [], "country": [], "direction": []},
    )

CONTROL_SCENARIO_SIGNALS = {
    "large_amount": "large_amount",
    "frequency_spike": "daily_velocity",
    "rapid_series": "rapid_velocity",
    "structuring": "structuring",
    "new_recipient": "new_recipient",
    "night_activity": "night_activity",
    "many_to_one": "many_to_one",
    "cycle": "short_cycle",
    "rapid_cashout": "rapid_cashout",
    "dormant_reactivation": "dormant_account",
    "behavior_deviation": "behavior_deviation",
}


@dataclass(frozen=True, slots=True)
class ScenarioRuleConfig:
    large_amount_robust_z: float = 12.0
    rapid_velocity_count_5m: int = 6
    daily_velocity_count_1d: int = 12
    structuring_count_30m: int = 8
    structuring_total_30m: float = 700_000.0
    structuring_max_single: float = 150_000.0
    new_recipient_amount_ratio: float = 1.8
    night_hour_end: int = 5
    night_amount_ratio: float = 1.5
    many_to_one_senders_1h: int = 10
    rapid_cashout_minutes: int = 10
    rapid_cashout_ratio: float = 0.9
    dormant_days: int = 90
    behavior_amount_ratio: float = 5.0
    graph_window_hours: int = 24
    cycle_window_hours: int = 1
    max_graph_edges: int = 50_000
    outbound_history_only: bool = False
    night_utc_offset_hours: int = 0


@dataclass(frozen=True, slots=True)
class _Edge:
    timestamp: pd.Timestamp
    sender: str
    recipient: str
    amount: float


def _signal(
    code: str,
    label: str,
    strength: float,
    evidence: dict[str, Any],
    *,
    explanation_type: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "strength": round(float(np.clip(strength, 0, 1)), 6),
        "evidence": evidence,
        "explanation_type": explanation_type,
        "engine_version": SCENARIO_ENGINE_VERSION,
    }


class BoundedTransactionGraph:
    """A time- and size-bounded directed transaction graph."""

    def __init__(self, config: ScenarioRuleConfig) -> None:
        self.config = config
        self._edges: deque[_Edge] = deque()
        self._outgoing: dict[str, deque[_Edge]] = defaultdict(deque)
        self._incoming: dict[str, deque[_Edge]] = defaultdict(deque)
        self._max_observed_edges = 0
        self.size_limit_reached = False

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    @property
    def max_observed_edges(self) -> int:
        return self._max_observed_edges

    def _drop_oldest(self) -> None:
        edge = self._edges.popleft()
        outgoing = self._outgoing[edge.sender]
        incoming = self._incoming[edge.recipient]
        if outgoing and outgoing[0] is edge:
            outgoing.popleft()
        else:
            outgoing.remove(edge)
        if incoming and incoming[0] is edge:
            incoming.popleft()
        else:
            incoming.remove(edge)
        if not outgoing:
            del self._outgoing[edge.sender]
        if not incoming:
            del self._incoming[edge.recipient]

    def _prune(self, timestamp: pd.Timestamp) -> None:
        cutoff = timestamp - timedelta(hours=self.config.graph_window_hours)
        while self._edges and (
            self._edges[0].timestamp < cutoff
            or len(self._edges) > self.config.max_graph_edges
        ):
            if len(self._edges) > self.config.max_graph_edges:
                self.size_limit_reached = True
            self._drop_oldest()

    def evaluate_and_add(
        self,
        *,
        timestamp: pd.Timestamp,
        sender: str,
        recipient: str,
        amount: float,
    ) -> tuple[list[dict[str, Any]], dict[str, float]]:
        self._prune(timestamp)
        hour_cutoff = timestamp - timedelta(hours=1)
        incoming = [edge for edge in self._incoming.get(recipient, ()) if hour_cutoff <= edge.timestamp < timestamp]
        unique_senders = {edge.sender for edge in incoming}
        incoming_total = sum(edge.amount for edge in incoming)
        graph_signals: list[dict[str, Any]] = []

        if sender not in unique_senders and len(unique_senders) + 1 >= self.config.many_to_one_senders_1h:
            sender_count = len(unique_senders) + 1
            graph_signals.append(
                _signal(
                    "many_to_one",
                    "Много отправителей переводят одному получателю",
                    min(1.0, sender_count / self.config.many_to_one_senders_1h),
                    {
                        "unique_senders_1h": sender_count,
                        "incoming_amount_1h": round(incoming_total + amount, 2),
                    },
                    explanation_type="graph_signal",
                )
            )

        cycle_cutoff = timestamp - timedelta(hours=self.config.cycle_window_hours)
        cycle_path: list[str] | None = None
        for first in self._outgoing.get(recipient, ()):
            if not cycle_cutoff <= first.timestamp < timestamp:
                continue
            if first.recipient == sender:
                cycle_path = [sender, recipient, sender]
                break
            for second in self._outgoing.get(first.recipient, ()):
                if cycle_cutoff <= second.timestamp < timestamp and second.recipient == sender:
                    cycle_path = [sender, recipient, first.recipient, sender]
                    break
            if cycle_path:
                break
        if cycle_path:
            graph_signals.append(
                _signal(
                    "short_cycle",
                    "Короткий замкнутый цикл переводов",
                    1.0,
                    {"path": cycle_path, "window_hours": self.config.cycle_window_hours},
                    explanation_type="graph_signal",
                )
            )

        edge = _Edge(timestamp, sender, recipient, amount)
        self._edges.append(edge)
        self._outgoing[sender].append(edge)
        self._incoming[recipient].append(edge)
        self._prune(timestamp)
        self._max_observed_edges = max(self._max_observed_edges, len(self._edges))

        # Other rows at the same timestamp are not prior evidence. The current
        # candidate contributes only to this prospective graph snapshot.
        outgoing_snapshot = [item for item in self._outgoing.get(sender, ()) if item.timestamp < timestamp or item is edge]
        incoming_snapshot = [item for item in self._incoming.get(recipient, ()) if item.timestamp < timestamp or item is edge]
        out_degree = len({item.recipient for item in outgoing_snapshot})
        in_degree = len({item.sender for item in incoming_snapshot})
        recipient_total = sum(item.amount for item in incoming_snapshot)
        sender_amounts: dict[str, float] = defaultdict(float)
        for item in incoming_snapshot:
            sender_amounts[item.sender] += item.amount
        concentration = max(sender_amounts.values(), default=0.0) / max(recipient_total, 1.0)
        graph_features = {
            "sender_out_degree_24h": float(out_degree),
            "recipient_in_degree_24h": float(in_degree),
            "recipient_incoming_amount_24h": float(recipient_total),
            "recipient_sender_concentration_24h": float(concentration),
            "new_connection": float(not any(item.recipient == recipient for item in outgoing_snapshot if item is not edge)),
        }
        return graph_signals, graph_features


class TransactionScenarioEngine:
    def __init__(
        self,
        reference: TransactionFeatureReference,
        config: ScenarioRuleConfig | None = None,
    ) -> None:
        self.reference = reference
        self.config = config or ScenarioRuleConfig()
        self.graph = BoundedTransactionGraph(self.config)
        self._history: dict[str, deque[tuple[pd.Timestamp, float, str]]] = defaultdict(deque)
        self._pair_history: dict[tuple[str, str], deque[tuple[pd.Timestamp, float]]] = defaultdict(deque)
        self._inbound: dict[str, deque[tuple[pd.Timestamp, float]]] = defaultdict(deque)
        self._seen_devices: dict[str, dict[str, pd.Timestamp]] = defaultdict(dict)
        self._seen_countries: dict[str, dict[str, pd.Timestamp]] = defaultdict(dict)
        self._seen_currencies: dict[str, dict[str, pd.Timestamp]] = defaultdict(dict)
        self._seen_recipients: dict[str, dict[str, pd.Timestamp]] = defaultdict(dict)
        self._last_seen: dict[str, pd.Timestamp] = {}
        self._previous_seen: dict[str, pd.Timestamp] = {}
        self._first_global_timestamp: pd.Timestamp | None = None
        self._last_global_timestamp: pd.Timestamp | None = None

    def _client_baseline(self, client: str, timestamp: pd.Timestamp) -> tuple[float, float]:
        amounts = np.asarray([item[1] for item in self._history[client] if item[0] < timestamp], dtype="float64")
        if len(amounts) < 5:
            return self.reference.amount_median, max(self.reference.amount_mad, 1.0)
        median = float(np.median(amounts))
        mad = float(np.median(np.abs(amounts - median)))
        return median, max(mad, float(np.std(amounts)), 1.0)

    def evaluate_chunk(self, chunk: pd.DataFrame) -> pd.DataFrame:
        timestamps = pd.to_datetime(chunk["transaction_timestamp"], utc=True, errors="coerce")
        amounts = pd.to_numeric(chunk["transaction_amount"], errors="coerce")
        if timestamps.isna().any() or amounts.isna().any() or (amounts <= 0).any():
            raise ValueError("Scenario analysis requires valid timestamps and positive amounts.")
        if not timestamps.is_monotonic_increasing:
            raise ValueError("Transactions must be ordered by timestamp for scenario analysis.")
        if self._last_global_timestamp is not None and timestamps.iloc[0] < self._last_global_timestamp:
            raise ValueError("Transaction chunks are not globally ordered by timestamp.")

        records: list[dict[str, Any]] = []
        for position, (_, row) in enumerate(chunk.iterrows()):
            timestamp = timestamps.iloc[position]
            local_hour = (timestamp.hour + self.config.night_utc_offset_hours) % 24
            amount = float(amounts.iloc[position])
            client = str(row["client_id"]).strip()
            sender = str(row["sender_account_id"]).strip()
            recipient = str(row["recipient_account_id"]).strip()
            direction = str(row.get("direction", "outbound")).strip().casefold()
            device = str(row.get("device_id", "")).strip()
            country = str(row.get("country", "")).strip()
            currency = str(row.get("currency", "")).strip()
            if not client or not sender or not recipient:
                raise ValueError("Scenario analysis requires non-empty entity identifiers.")
            if self._first_global_timestamp is None:
                self._first_global_timestamp = timestamp

            history = self._history[client]
            cutoff_30d = timestamp - timedelta(days=30)
            while history and history[0][0] < cutoff_30d:
                history.popleft()
            prior_history = [item for item in history if item[0] < timestamp]
            median, scale = self._client_baseline(client, timestamp)
            amount_ratio = amount / max(median, 1.0)
            robust_z = (amount - median) / scale
            prior_5m = [item for item in prior_history if item[0] >= timestamp - timedelta(minutes=5)]
            prior_1d = [item for item in prior_history if item[0] >= timestamp - timedelta(days=1)]
            inferred_new_recipient = self._seen_recipients[client].get(recipient, timestamp) >= timestamp
            declared_new_recipient = str(row.get("is_new_recipient", "")).strip().casefold() in {
                "1", "true", "yes", "да"
            }
            is_new_recipient = inferred_new_recipient or declared_new_recipient
            rules: list[dict[str, Any]] = []

            if robust_z >= self.config.large_amount_robust_z:
                rules.append(_signal(
                    "large_amount", "Крупная сумма относительно истории", min(1.0, robust_z / 20),
                    {"amount": round(amount, 2), "baseline_median": round(median, 2), "robust_z": round(robust_z, 3)},
                    explanation_type="deterministic_rule",
                ))
            if len(prior_5m) + 1 >= self.config.rapid_velocity_count_5m:
                rules.append(_signal(
                    "rapid_velocity", "Серия операций за несколько минут", min(1.0, (len(prior_5m) + 1) / 10),
                    {"transactions_5m": len(prior_5m) + 1}, explanation_type="deterministic_rule",
                ))
            if len(prior_1d) + 1 >= self.config.daily_velocity_count_1d:
                rules.append(_signal(
                    "daily_velocity", "Необычно много операций за сутки", min(1.0, (len(prior_1d) + 1) / 20),
                    {"transactions_1d": len(prior_1d) + 1}, explanation_type="deterministic_rule",
                ))

            pair = self._pair_history[(client, recipient)]
            pair_cutoff = timestamp - timedelta(minutes=30)
            while pair and pair[0][0] < pair_cutoff:
                pair.popleft()
            pair_values = [value for moment, value in pair if moment < timestamp] + [amount]
            if (
                len(pair_values) >= self.config.structuring_count_30m
                and sum(pair_values) >= self.config.structuring_total_30m
                and max(pair_values) <= self.config.structuring_max_single
            ):
                rules.append(_signal(
                    "structuring", "Дробление суммы на серию переводов", 1.0,
                    {"transactions_30m": len(pair_values), "total_30m": round(sum(pair_values), 2), "recipient": recipient},
                    explanation_type="deterministic_rule",
                ))

            if (
                (declared_new_recipient or len(prior_history) >= 5)
                and is_new_recipient
                and (
                    declared_new_recipient
                    or
                    amount_ratio >= self.config.new_recipient_amount_ratio
                    or robust_z >= 3.5
                )
            ):
                rules.append(_signal(
                    "new_recipient", "Крупный перевод новому получателю", min(1.0, amount_ratio / 4),
                    {"amount_to_client_median": round(amount_ratio, 3), "recipient": recipient},
                    explanation_type="deterministic_rule",
                ))
            if (
                local_hour < self.config.night_hour_end
                and (
                    amount_ratio >= self.config.night_amount_ratio
                    or robust_z >= 3.5
                    or len(prior_history) >= 1
                )
            ):
                rules.append(_signal(
                    "night_activity", "Нетипичная операция ночью", min(1.0, amount_ratio / 4),
                    {"hour_utc": int(timestamp.hour), "hour_local": int(local_hour),
                     "utc_offset_hours": self.config.night_utc_offset_hours,
                     "amount_to_client_median": round(amount_ratio, 3)},
                    explanation_type="deterministic_rule",
                ))

            recent_inbound = self._inbound[client]
            cashout_cutoff = timestamp - timedelta(minutes=self.config.rapid_cashout_minutes)
            while recent_inbound and recent_inbound[0][0] < cashout_cutoff:
                recent_inbound.popleft()
            inbound_total = sum(value for moment, value in recent_inbound if moment < timestamp)
            if direction == "outbound" and inbound_total > 0 and amount >= inbound_total * self.config.rapid_cashout_ratio:
                rules.append(_signal(
                    "rapid_cashout", "Быстрый вывод недавно поступивших средств", min(1.0, amount / inbound_total),
                    {"inbound_amount": round(inbound_total, 2), "outbound_ratio": round(amount / inbound_total, 3), "window_minutes": self.config.rapid_cashout_minutes},
                    explanation_type="deterministic_rule",
                ))

            global_age_days = (
                (timestamp - self._first_global_timestamp).total_seconds() / 86_400
                if self._first_global_timestamp is not None else 0.0
            )
            previous_seen = self._last_seen.get(client)
            if previous_seen == timestamp:
                previous_seen = self._previous_seen.get(client)
            if previous_seen is None and global_age_days >= self.config.dormant_days:
                rules.append(_signal(
                    "dormant_account", "Операция по счёту без активности в доступной истории", 1.0,
                    {"history_span_days": round(global_age_days, 1), "prior_operations": 0},
                    explanation_type="deterministic_rule",
                ))
            elif previous_seen is not None:
                inactive_days = (timestamp - previous_seen).total_seconds() / 86_400
                if inactive_days >= self.config.dormant_days:
                    rules.append(_signal(
                        "dormant_account", "Возобновление активности после долгого перерыва", 1.0,
                        {"inactive_days": round(inactive_days, 1)}, explanation_type="deterministic_rule",
                    ))

            enough_history = len(prior_history) >= 1
            behavior_evidence = {
                "new_device": bool(device and self._seen_devices[client].get(device, timestamp) >= timestamp),
                "unusual_country": bool(country and self._seen_countries[client].get(country, timestamp) >= timestamp),
                "unusual_currency": bool(currency and self._seen_currencies[client].get(currency, timestamp) >= timestamp),
                "night": local_hour < self.config.night_hour_end,
            }
            behavior_changes = sum(behavior_evidence.values())
            if (
                enough_history
                and (amount_ratio >= self.config.behavior_amount_ratio or robust_z >= 8.0)
                and behavior_changes >= 3
            ):
                rules.append(_signal(
                    "behavior_deviation", "Одновременное отклонение суммы, устройства и географии", 1.0,
                    {**behavior_evidence, "amount_to_client_median": round(amount_ratio, 3)},
                    explanation_type="deterministic_rule",
                ))

            graph_signals, graph_features = self.graph.evaluate_and_add(
                timestamp=timestamp, sender=sender, recipient=recipient, amount=amount
            )
            scenario_score = max((item["strength"] for item in rules), default=0.0)
            graph_score = max((item["strength"] for item in graph_signals), default=0.0)
            record = {
                "transaction_id": str(row["transaction_id"]),
                "client_id": client,
                "sender_account_id": sender,
                "recipient_account_id": recipient,
                "transaction_timestamp": str(row["transaction_timestamp"]),
                "scenario_score": scenario_score,
                "graph_score": graph_score,
                "rule_explanation": json.dumps(rules, ensure_ascii=False),
                "graph_explanation": json.dumps(graph_signals, ensure_ascii=False),
                **graph_features,
            }
            for control in ("scenario", "is_anomaly", "synthetic_only"):
                if control in chunk.columns:
                    record[control] = row[control]
            records.append(record)

            if direction == "inbound":
                recent_inbound.append((timestamp, amount))
            if not self.config.outbound_history_only or direction == "outbound":
                history.append((timestamp, amount, recipient))
                pair.append((timestamp, amount))
                self._seen_recipients[client].setdefault(recipient, timestamp)
                if device:
                    self._seen_devices[client].setdefault(device, timestamp)
                if country:
                    self._seen_countries[client].setdefault(country, timestamp)
                if currency:
                    self._seen_currencies[client].setdefault(currency, timestamp)
            if client in self._last_seen and self._last_seen[client] < timestamp:
                self._previous_seen[client] = self._last_seen[client]
            self._last_seen[client] = timestamp

        self._last_global_timestamp = timestamps.iloc[-1]
        return pd.DataFrame.from_records(records)


def score_transaction_rules(
    input_path: str | Path,
    reference: TransactionFeatureReference,
    *,
    chunk_size: int = 2_000,
    config: ScenarioRuleConfig | None = None,
    cancel_check: Callable[[], None] | None = None,
    progress_callback: Callable[[int], None] | None = None,
) -> tuple[pd.DataFrame, TransactionScenarioEngine]:
    engine = TransactionScenarioEngine(reference, config)
    batches: list[pd.DataFrame] = []
    processed = 0
    for chunk in iter_transaction_csv(input_path, chunk_size=chunk_size):
        if cancel_check is not None:
            cancel_check()
        batches.append(engine.evaluate_chunk(chunk))
        processed += len(chunk)
        if progress_callback is not None:
            progress_callback(processed)
    if cancel_check is not None:
        cancel_check()
    return pd.concat(batches, ignore_index=True), engine


def analyze_transaction_risk(
    input_path: str | Path,
    artifact_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    chunk_size: int = 2_000,
    config: ScenarioRuleConfig | None = None,
    data_quality_score: float = 1.0,
    cancel_check: Callable[[], None] | None = None,
    ml_progress_callback: Callable[[int], None] | None = None,
    rule_progress_callback: Callable[[int], None] | None = None,
    fallback_reference: TransactionFeatureReference | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not 0 <= data_quality_score <= 1:
        raise ValueError("data_quality_score must be between 0 and 1.")
    warning = None
    model_manifest = None
    ml = None
    try:
        _, model_manifest = load_transaction_artifacts(artifact_dir)
        reference = TransactionFeatureReference.from_dict(model_manifest["feature_reference"])
        ml = score_transaction_file(
            input_path, artifact_dir, chunk_size=chunk_size,
            cancel_check=cancel_check, progress_callback=ml_progress_callback,
        ).rename(columns={"anomaly_score": "ml_anomaly_score", "requires_review": "ml_requires_review"})
        ml = ml.drop(columns=["rank", *CONTROL_ONLY_COLUMNS], errors="ignore")
    except TransactionArtifactError:
        warning = "ML-модель недоступна или повреждена; оценка выполнена по правилам и графу."
        model_manifest = None
        reference = fallback_reference or default_rule_reference()
    rules, engine = score_transaction_rules(
        input_path,
        reference,
        chunk_size=chunk_size,
        config=config,
        cancel_check=cancel_check,
        progress_callback=rule_progress_callback,
    )
    if ml is None:
        result = rules.copy()
        result["ml_anomaly_score"] = None
        result["ml_requires_review"] = None
        result["ml_explanation"] = "[]"
        result["risk_signal_score"] = np.maximum(
            0.90 * result["scenario_score"], 0.90 * result["graph_score"]
        ).clip(0, 1)
    else:
        result = rules.merge(ml, on="transaction_id", how="inner", validate="one_to_one")
        weighted = (
            0.60 * result["ml_anomaly_score"]
            + 0.25 * result["scenario_score"]
            + 0.15 * result["graph_score"]
        )
        # Keep the original signal weights, reporting uncertainty separately.
        result["risk_signal_score"] = np.maximum.reduce(
            [weighted.to_numpy(), 0.85 * result["ml_anomaly_score"].to_numpy(),
             0.90 * result["scenario_score"].to_numpy(), 0.90 * result["graph_score"].to_numpy()]
        ).clip(0, 1)
    result["data_quality_score"] = float(data_quality_score)
    result["data_uncertainty"] = 1.0 - float(data_quality_score)
    result["engine_mode"] = "rule_based_fallback" if ml is None else "ml_rules_graph"
    result["requires_review"] = result["risk_signal_score"] >= 0.72
    result = result.sort_values(
        ["risk_signal_score", "transaction_id"], ascending=[False, True], kind="stable"
    ).reset_index(drop=True)
    result.insert(0, "rank", np.arange(1, len(result) + 1))

    config_value = config or ScenarioRuleConfig()
    manifest: dict[str, Any] = {
        "manifest_schema_version": "1.0",
        "scenario_engine_version": SCENARIO_ENGINE_VERSION,
        "model_version": model_manifest["model_version"] if model_manifest else None,
        "engine_mode": "rule_based_fallback" if ml is None else "ml_rules_graph",
        "warning": warning,
        "feature_reference": reference.to_dict(),
        "reference_origin": ("caller_supplied" if fallback_reference else "fixed_demo_baseline") if ml is None else "trained_artifact",
        "profile": "transaction_risk_signal",
        "result_term": "risk_signal_not_fraud_proof",
        "rule_config": asdict(config_value),
        "graph": {
            "directed": True,
            "window_hours": config_value.graph_window_hours,
            "max_edges": config_value.max_graph_edges,
            "observed_max_edges": engine.graph.max_observed_edges,
            "size_limit_reached": engine.graph.size_limit_reached,
        },
        "risk_formula": {
            "version": "fallback-1.0" if ml is None else RISK_FORMULA_VERSION,
            "expression": "max(0.90*scenario, 0.90*graph)" if ml is None else "max(0.60*ml + 0.25*scenario + 0.15*graph, 0.85*ml, 0.90*scenario, 0.90*graph)",
            "review_threshold": 0.72,
            "higher_means_more_risky": True,
        },
        "explanations": {
            "ml": "deviation_from_training_norm",
            "rules": "deterministic_rule",
            "graph": "graph_signal",
        },
    }
    if output_dir is not None:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        result.to_csv(output / "combined_scores.csv", index=False, encoding="utf-8", lineterminator="\n")
        (output / "rules_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        if "scenario" in result.columns:
            scenario_checks: dict[str, Any] = {}
            for scenario, expected in CONTROL_SCENARIO_SIGNALS.items():
                subset = result.loc[result["scenario"].eq(scenario)]
                found_codes = sorted({
                    item["code"]
                    for column in ("rule_explanation", "graph_explanation")
                    for payload in subset[column]
                    for item in json.loads(payload)
                })
                scenario_checks[scenario] = {
                    "expected_signal": expected,
                    "detected": expected in found_codes,
                    "control_rows": len(subset),
                    "observed_signals": found_codes,
                }
            normal = result.loc[result["scenario"].eq("normal")]
            normal_signaled = normal["scenario_score"].gt(0) | normal["graph_score"].gt(0)
            metrics = {
                "data_origin": "synthetic",
                "synthetic_only": True,
                "suitable_for_real_quality_claims": False,
                "warning": "Контроль сценариев на synthetic fixture не подтверждает качество на реальных операциях.",
                "rows": len(result),
                "review_rows": int(result["requires_review"].sum()),
                "normal_rows": len(normal),
                "normal_rule_or_graph_signal_rate": float(normal_signaled.mean()) if len(normal) else None,
                "all_control_scenarios_detected": all(item["detected"] for item in scenario_checks.values()),
                "scenario_checks": scenario_checks,
            }
            (output / "rules_metrics.json").write_text(
                json.dumps(metrics, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
    return result, manifest
