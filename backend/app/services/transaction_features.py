from __future__ import annotations

import math
import re
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pandas as pd


TRANSACTION_FEATURE_SCHEMA_VERSION = "1.0"
DEFAULT_TRANSACTION_CHUNK_SIZE = 2_000
MAX_REFERENCE_VALUES = 100_000
MAX_CATEGORY_VALUES = 20

REQUIRED_COLUMNS = (
    "transaction_id",
    "client_id",
    "sender_account_id",
    "recipient_account_id",
    "transaction_timestamp",
    "transaction_amount",
)
CATEGORY_COLUMNS = ("currency", "channel", "country", "direction")
CONTROL_ONLY_COLUMNS = frozenset({"scenario", "is_anomaly", "synthetic_only"})

BASE_FEATURE_LABELS = {
    "amount": "Сумма операции",
    "log_amount": "Логарифм суммы",
    "hour_sin": "Время суток",
    "hour_cos": "Время суток",
    "weekday_sin": "День недели",
    "weekday_cos": "День недели",
    "is_weekend": "Операция в выходной",
    "amount_to_balance": "Доля доступного баланса",
    "minutes_since_previous": "Время с предыдущей операции",
    "minutes_since_inbound": "Время после входящего поступления",
    "prior_count_5m": "Операций за предыдущие 5 минут",
    "prior_amount_5m": "Сумма за предыдущие 5 минут",
    "prior_count_1h": "Операций за предыдущий час",
    "prior_amount_1h": "Сумма за предыдущий час",
    "prior_count_1d": "Операций за предыдущие сутки",
    "prior_amount_1d": "Сумма за предыдущие сутки",
    "prior_count_30d": "Операций за предыдущие 30 дней",
    "prior_amount_30d": "Сумма за предыдущие 30 дней",
    "unique_recipients_30d": "Получателей за предыдущие 30 дней",
    "is_new_recipient": "Новый получатель",
    "client_amount_robust_z": "Отклонение суммы от истории клиента",
    "cohort_amount_robust_z": "Отклонение суммы от общей группы",
}


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")
    return text or "missing"


def _robust_stats(values: list[float] | np.ndarray) -> tuple[float, float]:
    array = np.asarray(values, dtype="float64")
    if array.size == 0:
        return 0.0, 1.0
    median = float(np.median(array))
    mad = float(np.median(np.abs(array - median)))
    if not np.isfinite(mad) or mad <= 1e-12:
        mad = float(np.std(array))
    return median, max(mad, 1.0)


@dataclass(frozen=True, slots=True)
class TransactionFeatureReference:
    schema_version: str
    amount_median: float
    amount_mad: float
    categories: dict[str, list[str]]

    @property
    def feature_columns(self) -> list[str]:
        columns = list(BASE_FEATURE_LABELS)
        for field in CATEGORY_COLUMNS:
            columns.extend(
                f"{field}__{_slug(value)}" for value in self.categories.get(field, [])
            )
            columns.append(f"{field}__other")
        return columns

    @property
    def feature_labels(self) -> dict[str, str]:
        labels = dict(BASE_FEATURE_LABELS)
        for field in CATEGORY_COLUMNS:
            readable = {
                "currency": "Валюта",
                "channel": "Канал операции",
                "country": "Страна операции",
                "direction": "Направление операции",
            }[field]
            for value in self.categories.get(field, []):
                labels[f"{field}__{_slug(value)}"] = f"{readable}: {value}"
            labels[f"{field}__other"] = f"{readable}: новое значение"
        return labels

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TransactionFeatureReference":
        return cls(
            schema_version=str(payload["schema_version"]),
            amount_median=float(payload["amount_median"]),
            amount_mad=float(payload["amount_mad"]),
            categories={
                str(key): [str(value) for value in values]
                for key, values in payload["categories"].items()
            },
        )


def iter_transaction_csv(
    path: str | Path,
    *,
    chunk_size: int = DEFAULT_TRANSACTION_CHUNK_SIZE,
) -> Iterator[pd.DataFrame]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive.")
    for chunk in pd.read_csv(
        path,
        dtype="string",
        keep_default_na=False,
        chunksize=chunk_size,
        low_memory=False,
    ):
        chunk.columns = [str(column).strip() for column in chunk.columns]
        missing = sorted(set(REQUIRED_COLUMNS) - set(chunk.columns))
        if missing:
            raise ValueError("Missing required transaction columns: " + ", ".join(missing))
        yield chunk


def fit_transaction_reference(
    path: str | Path,
    *,
    chunk_size: int = DEFAULT_TRANSACTION_CHUNK_SIZE,
) -> TransactionFeatureReference:
    amounts: list[float] = []
    category_counts = {field: Counter() for field in CATEGORY_COLUMNS}
    for chunk in iter_transaction_csv(path, chunk_size=chunk_size):
        if "is_anomaly" in chunk.columns:
            normal = pd.to_numeric(chunk["is_anomaly"], errors="coerce").fillna(0).eq(0)
        else:
            normal = pd.Series(True, index=chunk.index)
        numeric = pd.to_numeric(chunk.loc[normal, "transaction_amount"], errors="coerce")
        finite = numeric[np.isfinite(numeric) & (numeric > 0)].astype(float).tolist()
        remaining = MAX_REFERENCE_VALUES - len(amounts)
        if remaining > 0:
            amounts.extend(finite[:remaining])
        for field in CATEGORY_COLUMNS:
            if field not in chunk.columns:
                continue
            category_counts[field].update(
                value.strip() or "__MISSING__"
                for value in chunk.loc[normal, field].astype(str)
            )
    if not amounts:
        raise ValueError("No valid positive transaction amounts were found.")
    median, mad = _robust_stats(amounts)
    categories = {
        field: [
            value
            for value, _ in sorted(
                category_counts[field].items(),
                key=lambda item: (-item[1], item[0]),
            )[:MAX_CATEGORY_VALUES]
        ]
        for field in CATEGORY_COLUMNS
    }
    return TransactionFeatureReference(
        schema_version=TRANSACTION_FEATURE_SCHEMA_VERSION,
        amount_median=median,
        amount_mad=mad,
        categories=categories,
    )


class TransactionFeatureBuilder:
    def __init__(self, reference: TransactionFeatureReference) -> None:
        self.reference = reference
        self._history: dict[str, deque[tuple[pd.Timestamp, float, str]]] = defaultdict(deque)
        self._last_timestamp: dict[str, pd.Timestamp] = {}
        self._previous_timestamp: dict[str, pd.Timestamp] = {}
        self._last_inbound: dict[str, pd.Timestamp] = {}
        self._previous_inbound: dict[str, pd.Timestamp] = {}
        self._seen_recipients: dict[str, dict[str, pd.Timestamp]] = defaultdict(dict)
        self._last_global_timestamp: pd.Timestamp | None = None

    def transform_chunk(self, chunk: pd.DataFrame) -> pd.DataFrame:
        missing = sorted(set(REQUIRED_COLUMNS) - set(chunk.columns))
        if missing:
            raise ValueError("Missing required transaction columns: " + ", ".join(missing))
        timestamps = pd.to_datetime(chunk["transaction_timestamp"], utc=True, errors="coerce")
        amounts = pd.to_numeric(chunk["transaction_amount"], errors="coerce")
        balances = (
            pd.to_numeric(chunk["balance_before"], errors="coerce")
            if "balance_before" in chunk.columns
            else pd.Series(np.nan, index=chunk.index)
        )
        if timestamps.isna().any():
            raise ValueError("Transaction timestamp contains invalid values.")
        if amounts.isna().any() or (amounts <= 0).any():
            raise ValueError("Transaction amount must contain positive numeric values.")
        if not timestamps.is_monotonic_increasing:
            raise ValueError("Transactions must be ordered by timestamp for historical features.")
        if self._last_global_timestamp is not None and timestamps.iloc[0] < self._last_global_timestamp:
            raise ValueError("Transaction chunks are not globally ordered by timestamp.")

        records: list[dict[str, float]] = []
        for position, (_, row) in enumerate(chunk.iterrows()):
            timestamp = timestamps.iloc[position]
            amount = float(amounts.iloc[position])
            client = str(row["client_id"]).strip()
            recipient = str(row["recipient_account_id"]).strip()
            direction = str(row.get("direction", "outbound")).strip().casefold()
            if not client or not recipient:
                raise ValueError("Client and recipient identifiers must be non-empty.")

            history = self._history[client]
            cutoff = timestamp - timedelta(days=30)
            while history and history[0][0] < cutoff:
                history.popleft()
            prior_history = [item for item in history if item[0] < timestamp]
            previous_amounts = np.asarray([item[1] for item in prior_history], dtype="float64")
            previous_timestamp = self._last_timestamp.get(client)
            if previous_timestamp == timestamp:
                previous_timestamp = self._previous_timestamp.get(client)
            previous_inbound = self._last_inbound.get(client)
            if previous_inbound == timestamp:
                previous_inbound = self._previous_inbound.get(client)
            historical_median, historical_mad = (
                _robust_stats(previous_amounts)
                if previous_amounts.size >= 5
                else (self.reference.amount_median, self.reference.amount_mad)
            )

            feature: dict[str, float] = {
                "amount": amount,
                "log_amount": math.log1p(amount),
                "hour_sin": math.sin(2 * math.pi * timestamp.hour / 24),
                "hour_cos": math.cos(2 * math.pi * timestamp.hour / 24),
                "weekday_sin": math.sin(2 * math.pi * timestamp.dayofweek / 7),
                "weekday_cos": math.cos(2 * math.pi * timestamp.dayofweek / 7),
                "is_weekend": float(timestamp.dayofweek >= 5),
                "amount_to_balance": (
                    amount / float(balances.iloc[position])
                    if pd.notna(balances.iloc[position]) and float(balances.iloc[position]) > 0
                    else 0.0
                ),
                "minutes_since_previous": min(
                    (timestamp - previous_timestamp).total_seconds() / 60,
                    30 * 24 * 60,
                )
                if previous_timestamp is not None
                else 30 * 24 * 60,
                "minutes_since_inbound": min(
                    (timestamp - previous_inbound).total_seconds() / 60,
                    30 * 24 * 60,
                )
                if direction == "outbound" and previous_inbound is not None
                else 30 * 24 * 60,
                "unique_recipients_30d": float(len({item[2] for item in prior_history})),
                "is_new_recipient": float(self._seen_recipients[client].get(recipient, timestamp) >= timestamp),
                "client_amount_robust_z": (amount - historical_median) / historical_mad,
                "cohort_amount_robust_z": (
                    amount - self.reference.amount_median
                )
                / self.reference.amount_mad,
            }
            for suffix, window in (
                ("5m", timedelta(minutes=5)),
                ("1h", timedelta(hours=1)),
                ("1d", timedelta(days=1)),
                ("30d", timedelta(days=30)),
            ):
                values = [item[1] for item in prior_history if item[0] >= timestamp - window]
                feature[f"prior_count_{suffix}"] = float(len(values))
                feature[f"prior_amount_{suffix}"] = float(sum(values))

            for field in CATEGORY_COLUMNS:
                raw = str(row.get(field, "")).strip() or "__MISSING__"
                known = self.reference.categories.get(field, [])
                for value in known:
                    feature[f"{field}__{_slug(value)}"] = float(raw == value)
                feature[f"{field}__other"] = float(raw not in known)

            records.append(feature)
            history.append((timestamp, amount, recipient))
            self._seen_recipients[client].setdefault(recipient, timestamp)
            if client in self._last_timestamp and self._last_timestamp[client] < timestamp:
                self._previous_timestamp[client] = self._last_timestamp[client]
            self._last_timestamp[client] = timestamp
            if direction == "inbound":
                if client in self._last_inbound and self._last_inbound[client] < timestamp:
                    self._previous_inbound[client] = self._last_inbound[client]
                self._last_inbound[client] = timestamp

        self._last_global_timestamp = timestamps.iloc[-1]
        result = pd.DataFrame.from_records(records, index=chunk.index)
        return result.reindex(columns=self.reference.feature_columns, fill_value=0.0).astype("float64")


def iter_feature_batches(
    path: str | Path,
    reference: TransactionFeatureReference,
    *,
    chunk_size: int = DEFAULT_TRANSACTION_CHUNK_SIZE,
) -> Iterator[tuple[pd.DataFrame, pd.DataFrame]]:
    builder = TransactionFeatureBuilder(reference)
    for chunk in iter_transaction_csv(path, chunk_size=chunk_size):
        yield builder.transform_chunk(chunk), chunk
