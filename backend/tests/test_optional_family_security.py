"""Independent checks of voluntary protection and frozen family authority."""
from datetime import timedelta
import json
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Card, ProtectionChangeRequest, Transaction, TransferRequest, utcnow
from app.main import app


BODY = {"recipient": "+77010000043", "recipient_name": "Айдана", "amount": "1000000.25"}


def login(client, code="TEST0006"):
    assert client.post("/api/auth/login", json={"test_iin": code,
        "password": f"Aman-Test-{code[-4:]}!"}).status_code == 200


def post(client, body, key=None):
    return client.post("/api/transfers", json=body, headers={"Idempotency-Key": key or str(uuid4())})


def money(client):
    with client.test_factory() as db:
        return (list(db.execute(select(Card.id, Card.balance).order_by(Card.id))),
                list(db.execute(select(Transaction.id, Transaction.amount).order_by(Transaction.id))))


def enable(client):
    assert client.put("/api/protection-settings", json={"protection_active": True,
        "notifications_enabled": True, "confirmation_enabled": True}).status_code == 200


def make_due(client):
    assert client.post("/api/protection-change-requests", json={"action": "disable"}).status_code == 200
    with client.test_factory() as db:
        change = db.scalar(select(ProtectionChangeRequest).where(ProtectionChangeRequest.status == "pending"))
        change.effective_at = utcnow() - timedelta(seconds=1)
        db.commit()


def test_optout_preview_cannot_override_later_server_activation(client):
    login(client)
    preview = client.post("/api/transfers/risk-check", json={
        "recipient": BODY["recipient"], "amount": BODY["amount"]}).json()
    assert preview["requires_risk_acknowledgement"]
    enable(client)
    before = money(client)
    response = post(client, {**BODY, "risk_warning_acknowledged": True})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked_no_trusted"
    assert money(client) == before


def test_due_disable_is_durable_even_when_warning_command_is_denied(client):
    login(client)
    enable(client)
    make_due(client)
    before = money(client)
    key = str(uuid4())
    assert post(client, BODY, key).status_code == 428
    with client.test_factory() as db:
        change = db.scalar(select(ProtectionChangeRequest))
        assert change.status == "executed"
    assert money(client) == before
    result = post(client, {**BODY, "risk_warning_acknowledged": True}, key)
    assert result.status_code == 200 and result.json()["status"] == "completed"


def test_ack_cannot_bypass_quorum_or_reinterpret_legacy_staff_request(client):
    login(client)
    enable(client)
    invitation = client.post("/api/trusted-invitations", json={"trusted_iin": "TEST0003"}).json()
    with TestClient(app) as son:
        login(son, "TEST0003")
        assert son.post(f"/api/trusted-invitations/{invitation['id']}/accept").status_code == 200
        before = money(client)
        body = {**BODY, "risk_warning_acknowledged": True, "anti_scam": {"pressure": True}}
        created = post(client, body)
        assert created.status_code == 200 and created.json()["status"] == "pending_approval"
        request_id = created.json()["request_id"]
        assert client.post(f"/api/transfers/requests/{request_id}/approve").status_code == 403
        assert son.post(f"/api/transfers/requests/{request_id}/approve").json()["status"] == "bank_review"
        assert money(client) == before
    # A historical high-risk staff request must retain the legacy family rule.
    with client.test_factory() as db:
        row = db.get(TransferRequest, request_id)
        policy = json.loads(row.policy_json)
        policy.pop("requires_family")
        policy.pop("risk_warning_acknowledged")
        policy["policy_version"] = "family-v2"
        row.policy_json = json.dumps(policy)
        db.commit()
    make_due(client)
    login(client, "TEST0099")
    decision = client.post(f"/api/analyst/bank-events/{request_id}/approve",
                           json={"note": "Проверка исторического запроса"})
    assert decision.status_code == 409, decision.text
    assert money(client) == before
