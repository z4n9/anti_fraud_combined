"""Read-only analyst aggregation over bank events or an owned completed result."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.analyst_security import CurrentAnalyst
from app.core.database import get_db
from app.domain.models import Card, Transaction, User
from app.risk_ledger.api.routes import ApiError, _completed_job, _manager, analyst_access
from app.risk_ledger.services.session_store import SessionNotFoundError
from app.services.recipient_monitoring import CONFIG, detect_recipient_alerts

router = APIRouter(prefix="/api/analyst", dependencies=[Depends(analyst_access)], tags=["Мониторинг получателей"])
DB = Annotated[Session, Depends(get_db)]
BANK_LIMIT = 50_000
ANALYSIS_LIMIT = 100_000


def bank_records(db, as_of):
    # Select debits only. Each destination is proven by its matching credit,
    # never guessed from a mutable telephone number or a reciprocal transaction.
    rows = list(db.scalars(select(Transaction).where(Transaction.type == "transfer",
        Transaction.status == "completed", Transaction.amount < 0,
        Transaction.created_at <= as_of.replace(tzinfo=None))
        .order_by(Transaction.created_at.desc(), Transaction.id.desc()).limit(BANK_LIMIT + 1)))
    truncated = len(rows) > BANK_LIMIT
    rows = rows[:BANK_LIMIT]
    records, skipped = [], 0
    # Batched joins avoid both N+1 queries and SQLite's bound-parameter limit.
    for offset in range(0, len(rows), 400):
        batch = rows[offset:offset + 400]
        credits = list(db.scalars(select(Transaction).where(
            Transaction.request_key.in_([f"credit:{row.id}" for row in batch]))))
        credit_by_key = {row.request_key: row for row in credits}
        card_ids = {row.card_id for row in batch + credits}
        cards = {card.id: card for card in db.scalars(select(Card).where(Card.id.in_(card_ids)))}
        user_ids = {row.user_id for row in batch + credits}
        names = {user.id: user.name for user in db.scalars(select(User).where(User.id.in_(user_ids)))}
        for debit in batch:
            credit = credit_by_key.get(f"credit:{debit.id}")
            sender = cards.get(debit.card_id)
            target = cards.get(credit.card_id) if credit else None
            if (not credit or not sender or not target or credit.type != "income" or credit.status != "completed"
                or credit.amount != -debit.amount or credit.user_id == debit.user_id
                or sender.user_id != debit.user_id or target.user_id != credit.user_id
                or sender.currency != "KZT" or target.currency != "KZT"
                or credit.created_at < debit.created_at or credit.created_at > as_of.replace(tzinfo=None)):
                skipped += 1
                continue
            stamp = debit.created_at.replace(tzinfo=timezone.utc) if debit.created_at.tzinfo is None else debit.created_at
            records.append({"id": str(debit.id), "timestamp": stamp, "sender_id": f"card:{debit.card_id}",
                "recipient_id": f"card:{credit.card_id}", "label": names.get(credit.user_id, f"Карта {credit.card_id}"),
                "amount": -debit.amount / 100, "currency": "KZT"})
    warnings = ["Источник: только завершённые банковские списания с совпадающей встречной проводкой KZT. История до доступной выборки неизвестна."]
    if skipped:
        warnings.append(f"Банковских списаний без корректной пары KZT исключено: {skipped}.")
    if truncated:
        warnings.append("Проверены последние 50000 банковских списаний; более ранняя история недоступна этому расчёту, новизна может быть завышена.")
    return records, len(rows), truncated, warnings


@router.get("/recipient-monitoring")
def recipient_monitoring(request: Request, db: DB, current: CurrentAnalyst,
                         analysis_id: str | None = Query(default=None, max_length=80),
                         page: int = Query(default=1, ge=1),
                         page_size: int = Query(default=20, ge=1, le=100),
                         recipient_id: str | None = Query(default=None, max_length=200)):
    as_of = datetime.now(timezone.utc)
    if analysis_id is None:
        records, scanned, truncated, warnings = bank_records(db, as_of)
        kind = "bank"
    else:
        manager = _manager(request)
        # Query parameters are not covered by the existing path-based guard.
        job = manager.get_job(analysis_id)
        if job.owner_user_id != current.id:
            raise ApiError(404, "analysis_not_found", "Analysis session was not found.")
        _completed_job(manager, analysis_id)
        try:
            payloads = manager.store.monitoring_transaction_payloads(analysis_id, ANALYSIS_LIMIT + 1)
        except SessionNotFoundError as error:
            raise ApiError(404, "analysis_not_found", "Analysis session was not found.") from error
        if not payloads:
            raise ApiError(409, "transaction_profile_unavailable", "Завершённый анализ не содержит транзакционных результатов.")
        truncated = len(payloads) > ANALYSIS_LIMIT
        payloads = payloads[:ANALYSIS_LIMIT]
        scanned = len(payloads)
        records = [{"id": row.get("transaction_id", row.get("record_id")),
            "timestamp": row.get("transaction_timestamp"), "sender_id": row.get("sender_account_id"),
            "recipient_id": row.get("recipient_account_id"), "amount": row.get("transaction_amount"),
            "currency": row.get("currency")} for row in payloads]
        missing_metadata = sum(record["amount"] in (None, "") or record["currency"] in (None, "")
                               for record in records)
        if missing_metadata == len(records):
            raise ApiError(409, "transaction_metadata_unavailable",
                "Сохранённый анализ не содержит сумм/валют для мониторинга. Повторите анализ исходного файла.")
        warnings = ["Источник: сохранённые транзакционные результаты выбранного анализа; история вне выбранного периода неизвестна."]
        if missing_metadata:
            warnings.append(f"Сохранённых записей без суммы или валюты исключено: {missing_metadata}.")
        warnings.extend(str(warning) for warning in job.warnings)
        if truncated:
            warnings.append("Расчёт ограничен первыми 100000 сохранённых транзакционных результатов; выборка неполная, новизна может быть завышена.")
        kind = "analysis"
        # Files may describe historical or synthetic periods; their maximum valid
        # timestamp is the observation horizon, not today's bank server time.
    items, engine_warnings = detect_recipient_alerts(records, as_of=as_of if kind == "bank" else None)
    if kind == "analysis":
        valid_times = []
        for record in records:
            try:
                value = datetime.fromisoformat(str(record["timestamp"]).replace("Z", "+00:00"))
                if value.tzinfo is not None:
                    valid_times.append(value.astimezone(timezone.utc))
            except (ValueError, TypeError):
                pass
        as_of = max(valid_times) if valid_times else as_of
    if recipient_id is not None:
        items = [item for item in items if item["recipient_id"] == recipient_id]
    return {"source": {"kind": kind, "analysis_id": analysis_id, "records_scanned": scanned,
                       "truncated": truncated, "warnings": warnings + engine_warnings, "as_of": as_of.isoformat()},
            "config": dict(CONFIG), "total": len(items), "page": page, "page_size": page_size,
            "items": items[(page - 1) * page_size:page * page_size]}
