"""Actual authenticated transaction-only analyst workflow, without ML artifacts."""
import csv
import io
import json
import time

from sqlalchemy import select

from app.domain.models import Card, Transaction

BASE = "/api/analyst"


def login_analyst(client):
    response = client.post("/api/auth/login", json={"test_iin": "TEST0099", "password": "Aman-Test-0099!"})
    assert response.status_code == 200, response.text


def source_rows():
    rows = [{"transaction_id": f"tx-{index}", "client_id": "client-a", "sender_account_id": "account-a",
             "recipient_account_id": "account-b", "transaction_timestamp": f"2026-10-05T12:00:{index:02d}Z",
             "transaction_amount": 100, "currency": "KZT", "direction": "outbound"} for index in range(6)]
    rows.append({**rows[-1], "transaction_id": "tx-large", "recipient_account_id": "account-new",
                 "transaction_timestamp": "2026-10-05T12:01:00Z", "transaction_amount": 100000})
    return rows


def csv_source():
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(source_rows()[0]))
    writer.writeheader()
    writer.writerows(source_rows())
    return stream.getvalue().encode()


def wait_status(client, analysis_id, states):
    for _ in range(500):
        response = client.get(f"{BASE}/analyses/{analysis_id}/status")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in states:
            return body
        time.sleep(0.01)
    raise AssertionError("Analyst workflow did not finish")


def upload(client, filename, content, *, auto=False):
    response = client.post(f"{BASE}/analyses", params={"auto_run": auto},
                           files={"file": (filename, content)})
    assert response.status_code == 202, response.text
    assert response.json()["status_url"].startswith(BASE)
    return response.json()["analysis_id"]


def test_transaction_plan_run_review_exports_and_delete_without_models(client):
    with client.test_factory() as db:
        balances = list(db.execute(select(Card.id, Card.balance)).all())
        ledger = list(db.execute(select(Transaction.id, Transaction.amount)).all())
    login_analyst(client)
    model = client.get(f"{BASE}/model").json()
    assert model["ready"] and model["engine_mode"] == "rule_based_fallback"
    assert not model["client_model_available"]
    analysis_id = upload(client, "transfers.csv", csv_source())
    assert wait_status(client, analysis_id, {"planned", "failed"})["status"] == "planned"
    plan = client.get(f"{BASE}/analyses/{analysis_id}/plan").json()
    assert any(p["profile"] == "transaction_anomaly" and p["state"] == "planned" for p in plan["profiles"])
    assert client.get(f"{BASE}/analyses/{analysis_id}/inventory").status_code == 200
    response = client.post(f"{BASE}/analyses/{analysis_id}/run")
    assert response.status_code == 202, response.text
    finished = wait_status(client, analysis_id, {"completed", "failed"})
    assert finished["status"] == "completed", finished
    transactions = client.get(f"{BASE}/analyses/{analysis_id}/transactions")
    assert transactions.status_code == 200, transactions.text
    assert transactions.json()["total"] == 7
    item = next(row for row in transactions.json()["items"] if row["transaction_id"] == "tx-large")
    assert item["engine_mode"] == "rule_based_fallback"
    assert item["score_type"] == "risk_signal_not_fraud_probability"
    assert item["risk_signal_score"] >= 0.72
    assert "large_amount" in {factor["code"] for factor in json.loads(item["rule_explanation"])}
    review = client.patch(f"{BASE}/analyses/{analysis_id}/investigations/tx-large",
                          json={"status": "confirmed", "comment": "Проверено аналитиком"})
    assert review.status_code == 200, review.text
    assert client.get(f"{BASE}/analyses/{analysis_id}/feedback").status_code == 200
    for kind in ("full", "review", "transactions", "relationships", "mapping_quality", "technical_plan"):
        response = client.get(f"{BASE}/analyses/{analysis_id}/exports/{kind}")
        assert response.status_code == 200, response.text
        assert "attachment" in response.headers["content-disposition"]
    for suffix in ("summary", "results", "distribution", "report.csv", "clients", "relationships"):
        response = client.get(f"{BASE}/analyses/{analysis_id}/{suffix}")
        assert response.status_code == 200, response.text
    assert client.delete(f"{BASE}/analyses/{analysis_id}").status_code == 204
    assert client.get(f"{BASE}/analyses/{analysis_id}/status").status_code == 404
    with client.test_factory() as db:
        assert list(db.execute(select(Card.id, Card.balance)).all()) == balances
        assert list(db.execute(select(Transaction.id, Transaction.amount)).all()) == ledger


def test_automatic_json_transaction_analysis_without_models(client):
    login_analyst(client)
    analysis_id = upload(client, "transfers.json", json.dumps(source_rows()).encode(), auto=True)
    finished = wait_status(client, analysis_id, {"completed", "failed"})
    assert finished["status"] == "completed", finished
    assert client.get(f"{BASE}/analyses/{analysis_id}/transactions").json()["total"] == 7


def test_client_only_plan_explicitly_blocks_without_model(client):
    login_analyst(client)
    analysis_id = upload(client, "clients.csv", b"record_id,age,gender,monthly_income\nclient-a,40,M,500000\n")
    assert wait_status(client, analysis_id, {"planned", "failed"})["status"] == "planned"
    plan = client.get(f"{BASE}/analyses/{analysis_id}/plan").json()
    profile = next(p for p in plan["profiles"] if p["profile"] == "client_risk")
    assert profile["state"] == "blocked"
    assert any("модель отсутствует" in reason for reason in profile["blocking_reasons"])
