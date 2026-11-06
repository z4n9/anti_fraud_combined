"""Read-only Python history for the original feature-builder interface.

No database is created. Financial input remains integer tiyn; native KZT floats
exist only in transient model feature rows.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import math

STATUSES = frozenset({"REQUESTED", "PENDING_REVIEW", "REJECTED", "COMPLETED"})


def aware_timestamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("ML timestamps must be timezone-aware datetimes")
    return value.timestamp()


def positive_cents(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("ML amounts must be positive integer tiyn")
    return value


def kzt_amount(cents):
    # Tiyn -> KZT only. No FX, rounding to another currency, or minimum clamp.
    value = float(Decimal(positive_cents(cents)) / Decimal(100))
    if not math.isfinite(value) or value <= 0:
        raise ValueError("ML amount conversion must be finite and positive")
    return value


@dataclass(frozen=True)
class HistoryRow:
    seq: int
    ts: float
    amount: float
    sender_id: str
    receiver_id: str
    status: str


class MemoryHistory:
    def __init__(self, history, *, evaluated_at):
        cutoff = aware_timestamp(evaluated_at)
        seen, rows = set(), []
        for item in history:
            ts = aware_timestamp(item["timestamp"])
            if ts >= cutoff:
                continue  # Equal timestamps and future rows cannot affect this decision.
            identifier = str(item["id"])
            if identifier in seen:
                raise ValueError("Duplicate ML history transaction ID")
            seen.add(identifier)
            sender, receiver, status = item["sender_id"], item["receiver_id"], item["status"]
            if not isinstance(sender, str) or not sender or not isinstance(receiver, str) or not receiver:
                raise ValueError("ML history identities must be nonempty strings")
            if status not in STATUSES:
                raise ValueError("Unknown ML history transaction status")
            rows.append(HistoryRow(len(rows) + 1, ts, kzt_amount(item["amount_cents"]),
                                   sender, receiver, status))
        self.rows = tuple(rows)
        self.count_statuses = ["COMPLETED"]

    def _rows(self, t_from, t, max_seq, statuses):
        return (r for r in self.rows if t_from <= r.ts < t
                and (max_seq is None or r.seq < int(max_seq))
                and (not statuses or r.status in statuses))

    def sender_rows(self, sender, t_from, t, max_seq=None):
        return [(r.ts, r.amount, r.receiver_id) for r in self._rows(t_from, t, max_seq, self.count_statuses)
                if r.sender_id == sender]

    def receiver_rows(self, receiver, t_from, t, max_seq=None):
        return [(r.ts, r.sender_id) for r in self._rows(t_from, t, max_seq, self.count_statuses)
                if r.receiver_id == receiver]

    def pair_rows(self, sender, receiver, t_from, t, max_seq=None):
        return [(r.ts,) for r in self._rows(t_from, t, max_seq, self.count_statuses)
                if r.sender_id == sender and r.receiver_id == receiver]

    def last_ts(self, where_sql, params, t, max_seq=None):
        if where_sql not in {"sender_id=?", "sender_id=? AND receiver_id=?"}:
            raise ValueError("Unsupported feature-history lookup")
        return max((r.ts for r in self._rows(float("-inf"), t, max_seq, self.count_statuses)
                    if r.sender_id == params[0] and (len(params) == 1 or r.receiver_id == params[1])), default=None)

    def velocity_rows(self, sender, t_from, t, statuses, max_seq=None):
        return [(r.ts, r.amount, r.receiver_id) for r in self._rows(t_from, t, max_seq, statuses)
                if r.sender_id == sender]
