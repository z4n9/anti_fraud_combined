"""Advisory recipient aggregation, independent of transfer execution rules."""
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import math

CONFIG = {"window_minutes": 60, "max_small_amount": 10000, "currency": "KZT",
          "min_small_transfers": 10, "min_unique_senders": 8, "min_new_senders": 8,
          "min_new_sender_share": 0.8, "detail_limit": 200}


def _timestamp(value):
    if isinstance(value, datetime):
        stamp = value
    else:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    # Naive times have no reliable UTC interpretation; do not invent one.
    if stamp.tzinfo is None:
        raise ValueError("timezone required")
    return stamp.astimezone(timezone.utc)


def _id(value):
    if value is None or isinstance(value, bool):
        return None
    result = str(value).strip()
    return result if result and result.lower() not in {"nan", "none", "null"} else None


class _FirstSeenCounts:
    """Fenwick tree of currently present unique senders' first-seen times."""
    def __init__(self, times):
        self.times = sorted(set(times))
        self.tree = [0] * (len(self.times) + 1)

    def add(self, stamp, delta):
        index = bisect_left(self.times, stamp) + 1
        while index < len(self.tree):
            self.tree[index] += delta
            index += index & -index

    def before(self, stamp):
        index = bisect_left(self.times, stamp)
        total = 0
        while index:
            total += self.tree[index]
            index -= index & -index
        return total


def detect_recipient_alerts(records, *, as_of=None):
    """Return one strongest qualifying 60-minute window per recipient.

    IDs are deduplicated before any history calculation. Conflicting copies of
    an ID are all excluded. Same-time events enter the window together, so input
    ordering cannot create an artificial prior relationship. All valid earlier
    transfers, including amounts above the small-transfer limit, inform first seen.
    """
    cutoff = _timestamp(as_of) if as_of is not None else None
    canonical, conflicts = {}, set()
    skipped = 0
    duplicate_count = 0
    for record in records:
        try:
            txid, sender, recipient = (_id(record.get(key)) for key in ("id", "sender_id", "recipient_id"))
            stamp = _timestamp(record.get("timestamp"))
            amount = Decimal(str(record.get("amount")))
            if (not txid or not sender or not recipient or sender == recipient
                    or str(record.get("currency", "")).upper().strip() != "KZT"
                    or not amount.is_finite() or amount <= 0 or not math.isfinite(float(amount))
                    or (cutoff is not None and stamp > cutoff)):
                skipped += 1
                continue
            signature = (stamp, sender, recipient, amount)
            if txid in canonical:
                duplicate_count += 1
                if canonical[txid][0] != signature:
                    conflicts.add(txid)
                else:
                    canonical[txid] = (signature, min(canonical[txid][1], str(record.get("label") or recipient)))
                continue
            canonical[txid] = (signature, str(record.get("label") or recipient))
        except (ValueError, TypeError, InvalidOperation, OverflowError):
            skipped += 1
    recipients = defaultdict(list)
    labels = defaultdict(set)
    for txid, (signature, label) in canonical.items():
        if txid in conflicts:
            continue
        stamp, sender, recipient, amount = signature
        recipients[recipient].append((stamp, txid, sender, amount))
        labels[recipient].add(label)
    alerts = []
    span = timedelta(minutes=CONFIG["window_minutes"])
    for recipient, history in recipients.items():
        history.sort()
        first = {}
        for stamp, _, sender, _ in history:
            first.setdefault(sender, stamp)
        small = [row for row in history if row[3] <= CONFIG["max_small_amount"]]
        tree = _FirstSeenCounts(first.values())
        counts = defaultdict(int)
        left = right = 0
        total_amount = Decimal(0)
        best, best_rank = None, None
        while right < len(small):
            end = small[right][0]
            while right < len(small) and small[right][0] == end:
                _, _, sender, amount = small[right]
                if counts[sender] == 0:
                    tree.add(first[sender], 1)
                counts[sender] += 1
                total_amount += amount
                right += 1
            start = end - span
            while left < right and small[left][0] < start:
                _, _, sender, amount = small[left]
                counts[sender] -= 1
                if counts[sender] == 0:
                    del counts[sender]
                    tree.add(first[sender], -1)
                total_amount -= amount
                left += 1
            unique = len(counts)
            new = unique - tree.before(start)
            count = right - left
            if (count >= CONFIG["min_small_transfers"] and unique >= CONFIG["min_unique_senders"]
                    and new >= CONFIG["min_new_senders"]
                    and new * 5 >= unique * 4):
                rank = (count, unique, new / unique, total_amount)
                if best_rank is None or rank > best_rank:
                    best_rank = rank
                    best = (left, right, start, end, unique, new, total_amount)
        if best is None:
            continue
        left, right, start, end, unique, new, amount = best
        window = small[left:right]
        senders = defaultdict(lambda: [0, Decimal(0)])
        for _, _, sender, value in window:
            senders[sender][0] += 1
            senders[sender][1] += value
        sender_rows = [{"sender_id": sender, "count": values[0], "total_amount": float(values[1]),
                        "new_to_recipient": first[sender] >= start}
                       for sender, values in sorted(senders.items(), key=lambda pair: (-pair[1][0], pair[0]))]
        alerts.append({"recipient_id": recipient, "label": min(labels[recipient]),
            "window_start": start.isoformat(), "window_end": end.isoformat(),
            "small_transfer_count": len(window), "total_amount": float(amount),
            "unique_senders": unique, "new_senders": new, "new_sender_share": new / unique,
            "level": "high", "reasons": [
                "За 60 минут получено не менее 10 переводов до 10 000 KZT от не менее 8 новых отправителей.",
                "Не менее 80% отправителей впервые наблюдаются у получателя в этом окне доступной истории.",
                "Сигнал для проверки аналитиком; он не доказывает мошенничество и не меняет переводы."],
            "sender_breakdown": sender_rows[:CONFIG["detail_limit"]],
            "transactions": [{"id": txid, "timestamp": stamp.isoformat(), "sender_id": sender,
                              "recipient_id": recipient, "amount": float(value)}
                             for stamp, txid, sender, value in window[:CONFIG["detail_limit"]]],
            "details_truncated": len(window) > CONFIG["detail_limit"] or unique > CONFIG["detail_limit"]})
    alerts.sort(key=lambda item: (-item["small_transfer_count"], -item["unique_senders"], item["recipient_id"]))
    warnings = ["Новизна означает первое наблюдение пары в доступной истории, а не реальное знакомство. История до начала выборки неизвестна.",
                "Пороговые значения демонстрационные; сигнал не является ML-вероятностью мошенничества."]
    if skipped:
        warnings.append(f"Исключено неподходящих записей: {skipped} (валюта, реквизиты, время, сумма или self-transfer).")
    if duplicate_count:
        warnings.append(f"Удалено повторных ID: {duplicate_count}; конфликтующих ID исключено полностью: {len(conflicts)}.")
    if any(item["details_truncated"] for item in alerts):
        warnings.append("Детали каждого сигнала ограничены 200 переводами и 200 отправителями; итоговые счётчики полные для сканированной выборки.")
    return alerts, warnings
