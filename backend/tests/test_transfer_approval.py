"""Financial atomicity and persistent lifecycle of family transfer commands."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.domain.models import Card, Transaction, TransferRequest, User, utcnow
from app.main import app

BODY = {"recipient": "+77010000043", "recipient_name": "ignored client name",
        "amount": "1000000.25", "message": "fixed payload"}


def login(client, code):
    assert client.post("/api/auth/login", json={"test_iin": code,
        "password": f"Aman-Test-{code[-4:]}!"}).status_code == 200


@pytest.fixture
def family(client):
    login(client, "TEST0006")
    assert client.put("/api/protection-settings", json={"protection_active": True,
        "notifications_enabled": True, "confirmation_enabled": True}).status_code == 200
    invite = client.post("/api/trusted-invitations", json={"trusted_iin": "TEST0003"}).json()
    with TestClient(app) as son:
        login(son, "TEST0003")
        assert son.post(f"/api/trusted-invitations/{invite['id']}/accept").status_code == 200
        yield client, son


def command(owner, body=BODY, key=None):
    key = key or str(uuid4())
    response = owner.post("/api/transfers", json=body, headers={"Idempotency-Key": key})
    assert response.status_code == 200, response.text
    return response.json(), key


def balances(client):
    with client.test_factory() as db:
        return list(db.execute(select(Card.id, Card.balance).order_by(Card.id)).all())


def endpoint(row, verb):
    return f"/api/transfers/requests/{row['request_id']}/{verb}"


def test_high_without_trusted_is_persisted_and_cannot_be_retried_as_low(client):
    login(client, "TEST0006")
    before = balances(client)
    row, key = command(client)
    assert row["status"] == "blocked_no_trusted" and not row["success"]
    assert row["risk"]["transfer_enforcement"] and not row["risk"]["advisory"]
    assert command(client, key=key)[0] == row
    assert balances(client) == before
    assert client.post(endpoint(row, "approve")).status_code == 403
    assert client.post("/api/transfers", json={**BODY, "amount": "1"},
                       headers={"Idempotency-Key": key}).status_code == 409
    with client.test_factory() as db:
        saved = db.get(TransferRequest, row["request_id"])
        assert saved.amount_cents == 100000025 and saved.transaction_id is None
        assert "family-v1" in saved.policy_json


def test_pending_replay_no_debit_and_approval_exact_tiyn(family):
    owner, son = family
    before = balances(owner)
    row, key = command(owner)
    assert row["status"] == "pending_approval" and row["new_balance"] == 1200000
    assert command(owner, key=key)[0] == row
    assert balances(owner) == before
    approved = son.post(endpoint(row, "approve"))
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "completed"
    assert "new_balance" not in approved.json()
    with owner.test_factory() as db:
        saved = db.get(TransferRequest, row["request_id"])
        debit = db.get(Transaction, saved.transaction_id)
        credit = db.scalar(select(Transaction).where(Transaction.request_key == f"credit:{debit.id}"))
        assert debit.amount == -100000025 and credit.amount == 100000025
        assert saved.decided_by_user_id == saved.trusted_user_id
        assert db.get(Card, saved.sender_card_id).balance == 19999975
    completed, _ = command(owner, key=key)
    assert completed["status"] == "completed" and completed["new_balance"] == 199999.75


@pytest.mark.parametrize("problem", ["insufficient", "recipient_blocked", "sender_currency"])
def test_approval_revalidates_frozen_cards_and_funds(family, problem):
    owner, son = family
    row, _ = command(owner)
    with owner.test_factory() as db:
        saved = db.get(TransferRequest, row["request_id"])
        if problem == "insufficient":
            db.get(Card, saved.sender_card_id).balance = 100
        elif problem == "recipient_blocked":
            db.get(Card, saved.recipient_card_id).status = "blocked"
        else:
            db.get(Card, saved.sender_card_id).currency = "USD"
        db.commit()
    before = balances(owner)
    assert son.post(endpoint(row, "approve")).status_code == 400
    assert balances(owner) == before
    with owner.test_factory() as db:
        saved = db.get(TransferRequest, row["request_id"])
        assert saved.status == "pending_approval" and saved.transaction_id is None


def test_income_failure_rolls_back_approval_and_can_retry(family):
    owner, son = family
    row, _ = command(owner)
    before = balances(owner)

    def fail_income(mapper, connection, target):
        if target.type == "income":
            raise RuntimeError("income write failure")

    event.listen(Transaction, "before_insert", fail_income)
    try:
        with pytest.raises(RuntimeError):
            son.post(endpoint(row, "approve"))
    finally:
        event.remove(Transaction, "before_insert", fail_income)
    assert balances(owner) == before
    with owner.test_factory() as db:
        saved = db.get(TransferRequest, row["request_id"])
        assert saved.status == "pending_approval" and saved.transaction_id is None
        assert db.scalar(select(Transaction).where(Transaction.request_key == saved.request_key)) is None
    assert son.post(endpoint(row, "approve")).json()["status"] == "completed"


def test_expiry_is_durable_and_replay_does_not_reopen(family):
    owner, son = family
    row, key = command(owner)
    with owner.test_factory() as db:
        db.get(TransferRequest, row["request_id"]).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    before = balances(owner)
    response = son.post(endpoint(row, "approve"))
    assert response.status_code == 200 and response.json()["status"] == "expired"
    assert balances(owner) == before
    assert son.get("/api/transfers/pending-requests").json() == []
    assert command(owner, key=key)[0]["status"] == "expired"
    with owner.test_factory() as db:
        assert db.get(TransferRequest, row["request_id"]).status == "expired"


def test_concurrent_commands_create_one_pending_request(family):
    owner, _ = family
    before = balances(owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: command(owner, key="pending-concurrent")[0], range(2)))
    assert rows[0] == rows[1]
    assert balances(owner) == before
    with owner.test_factory() as db:
        assert len(list(db.scalars(select(TransferRequest)))) == 1


def test_low_risk_transfer_immediately_posts_with_persistent_snapshot(client):
    before = balances(client)
    row, key = command(client, body={**BODY, "amount": "1"})
    assert row["status"] == "completed" and row["transaction_id"] is not None
    assert command(client, body={**BODY, "amount": "1"}, key=key)[0] == row
    assert balances(client) != before
    with client.test_factory() as db:
        saved = db.get(TransferRequest, row["request_id"])
        assert saved.status == "completed" and saved.amount_cents == 100
        assert saved.risk_json and saved.policy_json


def test_legacy_completed_key_replays_before_risk_or_balance(client):
    with client.test_factory() as db:
        card = db.scalar(select(Card).where(Card.user_id == 1))
        card.balance = 0
        legacy = Transaction(user_id=1, card_id=card.id, type="transfer", title="Айдана",
            recipient=BODY["recipient"], amount=-100, message=BODY["message"], status="completed",
            request_key="1:legacy-done", balance_after=0)
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id
    row, _ = command(client, body={**BODY, "amount": "1"}, key="legacy-done")
    assert row["status"] == "completed" and row["transaction_id"] == legacy_id
    assert row["new_balance"] == 0 and row["request_id"] is None
    with client.test_factory() as db:
        assert list(db.scalars(select(TransferRequest))) == []
