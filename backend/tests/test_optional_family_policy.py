"""Voluntary protection changes policy, never monetary integrity or AntiScam."""
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.domain.models import Card, Transaction, TransferRequest, TrustedPerson

BODY = {"recipient": "+77010000043", "recipient_name": "Айдана", "amount": "1000000.25"}


def login(client, code="TEST0006"):
    assert client.post("/api/auth/login", json={"test_iin": code,
        "password": f"Aman-Test-{code[-4:]}!"}).status_code == 200


def post(client, body=None, key=None):
    return client.post("/api/transfers", json=body or BODY, headers={"Idempotency-Key": key or str(uuid4())})


def snapshot(client):
    with client.test_factory() as db:
        return (list(db.execute(select(Card.id, Card.balance).order_by(Card.id))),
                list(db.execute(select(Transaction.id, Transaction.amount).order_by(Transaction.id))),
                list(db.execute(select(TransferRequest.id, TransferRequest.status).order_by(TransferRequest.id))))


@pytest.mark.parametrize("missing_person", [False, True])
def test_preview_and_unacknowledged_high_are_warning_only_without_writes(client, missing_person):
    login(client)
    if missing_person:
        with client.test_factory() as db:
            person = db.scalar(select(TrustedPerson).where(TrustedPerson.user_id == db.scalar(
                select(Card.user_id).where(Card.balance == 120000000))))
            db.delete(person)
            db.commit()
    before = snapshot(client)
    preview = client.post("/api/transfers/risk-check", json={"recipient": BODY["recipient"], "amount": BODY["amount"]})
    assert preview.status_code == 200, preview.text
    assert preview.json()["overall_level"] in {"high", "critical"}
    assert preview.json()["family_protection_active"] is False
    assert preview.json()["requires_risk_acknowledgement"] is True
    response = post(client)
    assert response.status_code == 428 and isinstance(response.json()["detail"], str)
    assert snapshot(client) == before


def test_confirmed_optional_high_exact_money_replay_and_changed_ack_conflict(client):
    login(client)
    key = str(uuid4())
    # A denied warning doesn't consume the command key.
    assert post(client, key=key).status_code == 428
    body = {**BODY, "risk_warning_acknowledged": True}
    response = post(client, body, key)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    after = snapshot(client)
    assert post(client, body, key).json() == response.json()
    assert post(client, BODY, key).status_code == 409
    assert snapshot(client) == after
    with client.test_factory() as db:
        row = db.get(TransferRequest, response.json()["request_id"])
        debit = db.get(Transaction, row.transaction_id)
        credit = db.scalar(select(Transaction).where(Transaction.request_key == f"credit:{debit.id}"))
        assert debit.amount == -100000025 and credit.amount == 100000025
        policy = json.loads(row.policy_json)
        assert policy["policy_version"] == "family-v3"
        assert policy["requires_family"] is False and policy["risk_warning_acknowledged"] is True


def test_confirmed_overspend_rolls_back_request_and_balances(client):
    login(client)
    before = snapshot(client)
    response = post(client, {**BODY, "amount": "1300000.25", "risk_warning_acknowledged": True})
    assert response.status_code == 400, response.text
    assert snapshot(client) == before


def test_optional_high_antiscam_requires_bank_then_can_be_executed(client):
    login(client)
    before = snapshot(client)
    response = post(client, {**BODY, "risk_warning_acknowledged": True, "anti_scam": {"pressure": True}})
    assert response.status_code == 200 and response.json()["status"] == "bank_review"
    assert snapshot(client)[:2] == before[:2]
    login(client, "TEST0099")
    decision = client.post(f"/api/analyst/bank-events/{response.json()['request_id']}/approve",
                           json={"note": "Проверено сотрудником банка"})
    assert decision.status_code == 200 and decision.json()["status"] == "completed"
    assert len(snapshot(client)[1]) == len(before[1]) + 2


def test_active_protection_without_consent_still_blocks_even_with_ack(client):
    login(client)
    assert client.put("/api/protection-settings", json={"protection_active": True,
        "confirmation_enabled": True, "notifications_enabled": True}).status_code == 200
    before = snapshot(client)
    preview = client.post("/api/transfers/risk-check", json={"recipient": BODY["recipient"], "amount": BODY["amount"]}).json()
    assert preview["family_protection_active"] is True and preview["requires_risk_acknowledgement"] is False
    response = post(client, {**BODY, "risk_warning_acknowledged": True})
    assert response.status_code == 200 and response.json()["status"] == "blocked_no_trusted"
    assert snapshot(client)[:2] == before[:2]


@pytest.mark.parametrize("value", ["true", 1, None])
def test_warning_acknowledgement_is_strict_boolean(client, value):
    login(client)
    before = snapshot(client)
    assert post(client, {**BODY, "risk_warning_acknowledged": value}).status_code == 422
    assert snapshot(client) == before
