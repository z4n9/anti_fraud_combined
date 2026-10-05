"""Independent checks of the integration's access and serving boundaries."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Card, Transaction, User
from app.main import app


BODY = {"recipient": "+77010000043", "recipient_name": "Получатель", "amount": "1.23"}


def test_private_project_files_are_not_public(client):
    for path in (
        "/aman_bank.db", "/data/aman_bank.db", "/backend/app/main.py",
        "/requirements.txt", "/AGENTS.md", "/.git/config",
        "/docs/project-context.md", "/%2e%2e/%2e%2e/requirements.txt",
        "/%2e%2e%5c%2e%2e%5crequirements.txt",
    ):
        assert client.get(path).status_code == 404, path
    assert client.get("/api/cards").headers["cache-control"] == "no-store"


def test_idempotency_key_is_isolated_by_authenticated_user(client):
    headers = {"Idempotency-Key": "shared-browser-key"}
    first = client.post("/api/transfers", json=BODY, headers=headers)
    assert first.status_code == 200, first.text
    with TestClient(app) as grandma:
        login = grandma.post("/api/auth/login", json={
            "test_iin": "TEST0006", "password": "Aman-Test-0006!",
        })
        assert login.status_code == 200
        second = grandma.post("/api/transfers", json=BODY, headers=headers)
        assert second.status_code == 200, second.text
        assert second.json()["transaction_id"] != first.json()["transaction_id"]
        assert grandma.post("/api/transfers", json=BODY, headers=headers).json() == second.json()
    assert client.post("/api/transfers", json=BODY, headers=headers).json() == first.json()
    with client.test_factory() as db:
        outgoing = list(db.scalars(select(Transaction).where(Transaction.request_key.like("%:shared-browser-key"))))
        assert len(outgoing) == 2
        assert len({entry.user_id for entry in outgoing}) == 2
        recipient = db.scalar(select(User).where(User.test_iin == "TEST0002"))
        assert db.scalar(select(Card.balance).where(Card.user_id == recipient.id)) == 246


def test_stale_account_header_cannot_mutate_balance(client):
    before_cards = client.get("/api/cards").json()
    before_history = client.get("/api/transactions").json()
    result = client.post("/api/transfers", json=BODY, headers={
        "Idempotency-Key": "stale-account", "X-Account-ID": "999",
    })
    assert result.status_code == 409
    assert client.get("/api/cards").json() == before_cards
    assert client.get("/api/transactions").json() == before_history


def test_anonymous_transfer_and_cross_site_transfer_do_not_debit(client):
    before = client.get("/api/cards").json()
    with TestClient(app) as anonymous:
        result = anonymous.post("/api/transfers", json=BODY, headers={"Idempotency-Key": "anonymous"})
        assert result.status_code == 401
    result = client.post("/api/transfers", json=BODY, headers={
        "Idempotency-Key": "cross-site", "Sec-Fetch-Site": "cross-site",
    })
    assert result.status_code == 403
    assert client.get("/api/cards").json() == before
