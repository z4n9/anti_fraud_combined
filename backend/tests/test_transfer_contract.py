import pytest

BODY = {"recipient": "+77010000043", "recipient_name": "Айдана", "amount": "10.01"}


@pytest.mark.parametrize("headers", [{}, {"Idempotency-Key": ""}, {"Idempotency-Key": " "}, {"Idempotency-Key": "a" * 101}])
def test_transfer_requires_valid_idempotency_key(client, headers):
    cards = client.get("/api/cards").json()
    history = client.get("/api/transactions").json()
    response = client.post("/api/transfers", json=BODY, headers=headers)
    assert response.status_code == 422
    assert "idempotency-key" in response.json()["detail"].lower()
    assert client.get("/api/cards").json() == cards
    assert client.get("/api/transactions").json() == history


def test_health_reports_bank_and_enforced_rule_analysis(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "bank": "ready",
                               "analysis": {"status": "ready", "engine_mode": "rule_based_fallback",
                                            "model_available": False,
                                            "profiles": ["transaction_rules", "transaction_graph"],
                                            "advisory_only": False,
                                            "transfer_enforcement": True}}
    assert response.headers["cache-control"] == "no-store"
