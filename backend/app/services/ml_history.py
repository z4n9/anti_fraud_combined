"""Read-only projection of bank attempts and legacy ledger into model history."""
from datetime import timezone
import hashlib
import json

from sqlalchemy import select

from app.domain.models import TransferRequest

HISTORY_LIMIT = 50_000


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def build_ml_history(db, edges, now, *, valid_cards=None, ledger_truncated=False):
    """The candidate and previews are absent. Paired credits never add a transfer.

    `edges` contains only proven debit/credit pairs from the bank detector.
    An as-of evaluation must not learn that a request was resolved in the future.
    All money remains integer tiyn until the inference boundary.
    """
    now = _utc(now)
    requests = list(db.scalars(select(TransferRequest).where(
        TransferRequest.created_at < now.replace(tzinfo=None))
        .order_by(TransferRequest.created_at.desc(), TransferRequest.id.desc()).limit(HISTORY_LIMIT + 1)))
    truncated = len(requests) > HISTORY_LIMIT or ledger_truncated
    requests = requests[:HISTORY_LIMIT]
    history, posted_ids, posted_keys = [], set(), set()
    for row in requests:
        stamp = _utc(row.created_at)
        if stamp >= now:
            continue
        if valid_cards is not None:
            sender, recipient = valid_cards.get(row.sender_card_id), valid_cards.get(row.recipient_card_id)
            if not sender or not recipient or sender.user_id != row.sender_user_id or recipient.user_id != row.recipient_user_id:
                continue
        status = {"completed": "COMPLETED", "rejected": "REJECTED", "cancelled": "REJECTED",
                  "expired": "REJECTED", "blocked_no_trusted": "REJECTED"}.get(row.status, "PENDING_REVIEW")
        if row.decided_at is not None and _utc(row.decided_at) >= now:
            status = "PENDING_REVIEW"
        elif status == "COMPLETED" and row.decided_at is None:
            # Without a known posting time, do not invent a completed observation.
            status = "PENDING_REVIEW"
        history.append({"id": f"request:{row.id}", "sender_id": f"card:{row.sender_card_id}",
            "receiver_id": f"card:{row.recipient_card_id}", "amount_cents": row.amount_cents,
            "timestamp": stamp, "status": status})
        if row.transaction_id is not None:
            posted_ids.add(row.transaction_id)
        posted_keys.add(row.request_key)
    for row, destination in edges:
        stamp = _utc(row.created_at)
        if stamp >= now or row.id in posted_ids or row.request_key in posted_keys:
            continue
        history.append({"id": f"ledger:{row.id}", "sender_id": f"card:{row.card_id}",
            "receiver_id": f"card:{destination}", "amount_cents": -row.amount,
            "timestamp": stamp, "status": "COMPLETED"})
    history.sort(key=lambda row: (row["timestamp"], row["id"]))
    digest = hashlib.sha256(json.dumps(history, default=lambda value: value.isoformat(),
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return history, {"history_count": len(history), "history_truncated": truncated,
        "history_sha256": digest, "history_source": "bank_requests_and_paired_ledger",
        "history_as_of": now.isoformat()}
