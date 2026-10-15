"""Independent adversarial checks of advisory recipient monitoring."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from app.domain.models import Card, Transaction, TransferRequest
from app.main import app
from app.services.recipient_monitoring import detect_recipient_alerts


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
BASE = "/api/analyst/recipient-monitoring"


def row(index, *, sender=None, stamp=NOW, amount="100.01"):
    return {"id": str(index), "sender_id": sender or f"payer-{index}",
            "recipient_id": "target", "timestamp": stamp,
            "amount": amount, "currency": "KZT"}


def snapshot(client):
    with client.test_factory() as db:
        return (list(db.execute(select(Card.id, Card.balance).order_by(Card.id))),
                list(db.execute(select(Transaction.id, Transaction.amount).order_by(Transaction.id))),
                list(db.execute(select(TransferRequest.id, TransferRequest.status).order_by(TransferRequest.id))))


def test_at_least_eight_new_payers_required_even_with_eighty_percent_new():
    records = [row("old", sender="payer-0", stamp=NOW-timedelta(hours=2), amount="20000")]
    records += [row(index, sender=f"payer-{index % 8}") for index in range(10)]
    # Eight unique payers, seven first observed now: 87.5% is insufficient.
    items, _ = detect_recipient_alerts(records)
    assert items == []


def test_future_records_cannot_complete_a_signal_before_observation_horizon():
    records = [row(index, stamp=NOW+timedelta(minutes=index)) for index in range(10)]
    records += [row(f"future-{index}", stamp=NOW+timedelta(days=1)) for index in range(20)]
    assert detect_recipient_alerts(records, as_of=NOW+timedelta(minutes=8))[0] == []
    items, _ = detect_recipient_alerts(records, as_of=NOW+timedelta(minutes=9))
    assert len(items) == 1 and items[0]["small_transfer_count"] == 10
    assert items[0]["window_end"] == (NOW+timedelta(minutes=9)).isoformat()


def test_detail_cap_preserves_full_counts_and_same_timestamp_permutation():
    records = [row(index) for index in range(250)]
    items, warnings = detect_recipient_alerts(records)
    reversed_items, _ = detect_recipient_alerts(reversed(records))
    assert reversed_items == items
    result = items[0]
    assert result["small_transfer_count"] == result["unique_senders"] == result["new_senders"] == 250
    assert len(result["transactions"]) == len(result["sender_breakdown"]) == 200
    assert result["details_truncated"] and any("200" in warning for warning in warnings)


def test_one_recipient_hundred_thousand_repeated_payers_does_not_become_distinct():
    records = (row(index, sender=f"payer-{index % 7}", stamp=NOW+timedelta(seconds=index))
               for index in range(100_000))
    assert detect_recipient_alerts(records)[0] == []


def test_query_filters_and_role_headers_do_not_grant_access_or_change_money(client):
    before = snapshot(client)
    for params in ({}, {"analysis_id": "../../bank.db"}, {"recipient_id": "card:1"}):
        response = client.get(BASE, params=params, headers={"X-Role": "analyst"})
        assert response.status_code == 403
    assert client.post("/api/auth/login", json={"test_iin": "TEST0099", "password": "Aman-Test-0099!"}).status_code == 200
    assert client.get(BASE, headers={"X-Account-ID": "1"}).status_code == 409
    assert client.get(BASE, params={"page_size": 101}).status_code == 422
    response = client.get(BASE)
    assert response.status_code == 200, response.text
    assert snapshot(client) == before
    client.post("/api/auth/logout")
    assert client.get(BASE).status_code == 401


def test_query_analysis_ownership_precedes_status_and_storage_reads(client, monkeypatch):
    actor = client.post("/api/auth/login", json={"test_iin": "TEST0099", "password": "Aman-Test-0099!"})
    assert actor.status_code == 200
    manager = app.state.analysis_manager
    foreign, own = str(uuid4()), str(uuid4())
    manager.jobs[foreign] = SimpleNamespace(owner_user_id=actor.json()["id"]+1000, status="completed")
    manager.jobs[own] = SimpleNamespace(owner_user_id=actor.json()["id"], status="queued")

    def forbidden_read(*args, **kwargs):
        raise AssertionError("Storage must not be touched before ownership and completion checks")

    monkeypatch.setattr(manager.store, "monitoring_transaction_payloads", forbidden_read)
    before = snapshot(client)
    assert client.get(BASE, params={"analysis_id": foreign}).status_code == 404
    assert client.get(BASE, params={"analysis_id": own}).status_code == 409
    assert client.get(BASE, params={"analysis_id": "../../results.sqlite3"}).status_code == 404
    monkeypatch.setattr(app.state, "analysis_manager", None)
    assert client.get(BASE, params={"analysis_id": own}).status_code == 503
    assert snapshot(client) == before
