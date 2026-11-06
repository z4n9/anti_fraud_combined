"""Original S0_CLEAN builder and Tx, using an in-memory read adapter instead of SQLite.

Copied from anti_fraud_1/antifraud-runtime/src/online_features.py. All feature
formulae, NaN handling, ordered metadata and velocity semantics are preserved.
The adapter enforces strictly preceding history, including equal-time exclusion.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
import numpy as np
from .history import MemoryHistory as HistoryStore

H = 3600.0
NIGHT_BEFORE_HOUR = 6

@dataclass
class Tx:
    transaction_id: str
    sender_id: str
    receiver_id: str
    amount: float
    timestamp: datetime              # как пришло (tz-aware или naive)
    channel: Optional[str] = None
    sender_balance: Optional[float] = None

    @property
    def ts(self) -> float:
        """UTC epoch. Naive timestamp трактуется как UTC."""
        dt = self.timestamp
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()



class OnlineFeatureBuilder:
    """Считает признаки для одной транзакции по истории ДО неё."""

    def __init__(self, store: HistoryStore, feature_names):
        self.store = store
        self.feature_names = list(feature_names)
        missing = [f for f in self.feature_names if f not in self._all_names()]
        if missing:
            raise ValueError(f"онлайн-расчёт не реализован для признаков модели: {missing}")

    @staticmethod
    def _all_names():
        return {
            "amount", "hour", "day_of_week", "is_weekend", "is_night_transaction",
            "is_new_recipient",
            "sender_tx_count_1h", "sender_tx_count_6h", "sender_tx_count_24h",
            "sender_unique_receivers_24h", "sender_unique_receivers_7d",
            "receiver_tx_count_24h", "receiver_unique_senders_7d",
            "sender_avg_amount_7d", "sender_avg_amount_30d", "amount_vs_sender_avg",
            "time_since_last_transaction", "time_since_last_transaction_to_receiver",
            "transactions_to_same_receiver_24h", "amount_sum_1h", "amount_sum_24h",
        }

    def compute_all(self, tx: Tx, max_seq: Optional[int] = None) -> dict:
        """Все поддерживаемые признаки. max_seq - только для воспроизведения
        исторических строк (parity-тест): учитывать лишь записи с seq < max_seq."""
        st, t = self.store, tx.ts
        s, r, amt = tx.sender_id, tx.receiver_id, float(tx.amount)
        f = {}

        # --- время (локальное время из timestamp запроса)
        dt = tx.timestamp
        f["amount"] = amt
        f["hour"] = dt.hour
        f["day_of_week"] = dt.weekday()           # Пн=0, как в обучении
        f["is_weekend"] = int(dt.weekday() >= 5)
        f["is_night_transaction"] = int(dt.hour < NIGHT_BEFORE_HOUR)

        # --- отправитель: окна до 30 дней одним запросом
        rows = st.sender_rows(s, t - 720 * H, t, max_seq)
        ts = np.array([x[0] for x in rows], dtype=float)
        am = np.array([x[1] for x in rows], dtype=float)
        rc = np.array([x[2] for x in rows], dtype=object)

        def win(hours):
            return ts >= t - hours * H

        for h, tag in ((1, "1h"), (6, "6h"), (24, "24h")):
            f[f"sender_tx_count_{tag}"] = int(win(h).sum())
        for h, tag in ((1, "1h"), (24, "24h")):
            f[f"amount_sum_{tag}"] = float(am[win(h)].sum()) if len(am) else 0.0
        for h, tag in ((168, "7d"), (720, "30d")):
            m = win(h)
            f[f"sender_avg_amount_{tag}"] = float(am[m].mean()) if m.any() else math.nan
        avg30 = f["sender_avg_amount_30d"]
        f["amount_vs_sender_avg"] = amt / avg30 if not math.isnan(avg30) else math.nan
        f["sender_unique_receivers_24h"] = int(len(set(rc[win(24)]))) if len(rc) else 0
        f["sender_unique_receivers_7d"] = int(len(set(rc[win(168)]))) if len(rc) else 0

        last_s = st.last_ts("sender_id=?", [s], t, max_seq)
        f["time_since_last_transaction"] = (t - last_s) / H if last_s is not None else math.nan

        # --- получатель (по всем отправителям)
        rrows = st.receiver_rows(r, t - 168 * H, t, max_seq)
        rts = np.array([x[0] for x in rrows], dtype=float)
        rsd = [x[1] for x in rrows]
        f["receiver_tx_count_24h"] = int((rts >= t - 24 * H).sum()) if len(rts) else 0
        f["receiver_unique_senders_7d"] = int(len(set(rsd)))

        # --- пара sender -> receiver
        last_p = st.last_ts("sender_id=? AND receiver_id=?", [s, r], t, max_seq)
        f["is_new_recipient"] = int(last_p is None)
        f["time_since_last_transaction_to_receiver"] = (
            (t - last_p) / H if last_p is not None else math.nan)
        prow = st.pair_rows(s, r, t - 24 * H, t, max_seq)
        f["transactions_to_same_receiver_24h"] = len(prow)
        return f

    def velocity(self, tx: Tx, statuses=None, max_seq: Optional[int] = None) -> dict:
        """Velocity за последний час по ПОПЫТКАМ (статусы из конфига, в т.ч. отклонённые
        и ожидающие проверки), ВКЛЮЧАЯ текущую операцию. Это не признаки модели."""
        t = tx.ts
        rows = self.store.velocity_rows(tx.sender_id, t - H, t, statuses, max_seq)
        rec = [r[2] for r in rows] + [tx.receiver_id]
        amounts = [r[1] for r in rows] + [float(tx.amount)]
        return {
            "transfers_1h": len(rows) + 1,
            "unique_receivers_1h": len(set(rec)),
            "amount_sum_1h": float(sum(amounts)),
            "same_recipient_1h": sum(1 for r in rec if r == tx.receiver_id),
        }

    def build(self, tx: Tx, max_seq: Optional[int] = None) -> dict:
        """Признаки в порядке модели. Ключи == feature_names модели (assert)."""
        allf = self.compute_all(tx, max_seq)
        out = {k: allf[k] for k in self.feature_names}
        assert list(out) == self.feature_names
        return out
