"""Independent authorization and uploaded-source isolation checks."""
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.domain.models import Card, Transaction, User
from app.main import app

BASE = "/api/analyst"
CSV = ("transaction_id,client_id,sender_account_id,recipient_account_id,transaction_timestamp,transaction_amount,currency,direction\n"
       "T1,C1,A1,A2,2026-01-01T10:00:00Z,100,KZT,outbound\n"
       "T2,C1,A1,A2,2026-01-01T10:01:00Z,200,KZT,outbound\n").encode()


def login(client, code="TEST0099", password="Aman-Test-0099!"):
    response = client.post("/api/auth/login", json={"test_iin": code, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def snapshot(client):
    with client.test_factory() as db:
        return (list(db.execute(select(Card.id, Card.user_id, Card.balance).order_by(Card.id)).all()),
                list(db.execute(select(Transaction.id, Transaction.user_id, Transaction.amount)
                                .order_by(Transaction.id)).all()))


def test_analyst_namespace_requires_server_side_role(client):
    paths = ["/me", "/health", "/model", "/models/versions", "/models/audit",
             f"/analyses/{uuid4()}/status"]
    for path in paths:
        assert client.get(BASE + path, headers={"X-Role": "analyst"}).status_code == 403
    assert client.post(BASE + "/analyses", files={"file": ("transactions.csv", CSV)}).status_code == 403
    assert client.post("/api/auth/login", json={"test_iin": "TEST0001", "password": "Aman-Test-0001!",
                                               "role": "analyst"}).status_code == 422
    client.post("/api/auth/logout")
    for path in paths:
        assert client.get(BASE + path).status_code == 401
    assert client.post(BASE + "/analyses", files={"file": ("transactions.csv", CSV)}).status_code == 401
    login(client)
    response = client.get(BASE + "/me")
    assert response.status_code == 200, response.text
    assert response.json()["role"] == "analyst"


def test_analyst_role_does_not_grant_bank_access(client):
    before = snapshot(client)
    login(client)
    for path in ("/api/user", "/api/cards", "/api/transactions", "/api/trusted-person",
                 "/api/transfers/requests", "/api/transfers/pending-requests"):
        assert client.get(path).status_code == 403
    body = {"recipient": "+77010000043", "recipient_name": "Тест", "amount": "1"}
    assert client.post("/api/transfers", json=body,
                       headers={"Idempotency-Key": str(uuid4())}).status_code == 403
    assert client.post("/api/transfers/risk-check", json={"recipient": body["recipient"], "amount": "1"}).status_code == 403
    assert snapshot(client) == before


def test_second_analyst_cannot_read_mutate_export_or_delete_anothers_analysis(client):
    before = snapshot(client)
    login(client)
    response = client.post(BASE + "/analyses?auto_run=false", files={"file": ("transactions.csv", CSV)})
    assert response.status_code == 202, response.text
    analysis_id = response.json()["analysis_id"]
    path = f"{BASE}/analyses/{analysis_id}"
    assert client.get(path + "/status").status_code == 200
    with client.test_factory() as db:
        db.add(User(name="Другой аналитик", phone="+77999999998", test_iin="TEST0098",
                    password_hash=hash_password("Second-Analyst-Test!"), role="analyst"))
        db.commit()
    login(client, "TEST0098", "Second-Analyst-Test!")
    for suffix in ("status", "inventory", "plan", "summary", "results", "clients", "transactions",
                   "relationships", "investigations/T1", "feedback", "exports/technical-plan",
                   "distribution", "report.csv"):
        response = client.get(path + "/" + suffix)
        assert response.status_code == 404, (suffix, response.text)
    for method, suffix, body in (("patch", "plan", {}), ("post", "run", None),
                                 ("post", "cancel", None),
                                 ("patch", "investigations/T1", {"status": "confirmed", "comment": "forged"})):
        response = getattr(client, method)(path + "/" + suffix, **({"json": body} if body is not None else {}))
        assert response.status_code == 404, (suffix, response.text)
    assert client.delete(path).status_code == 404
    login(client)
    assert client.get(path + "/status").status_code == 200
    assert snapshot(client) == before


def test_uploads_and_registry_paths_are_confined_without_bank_writes(client):
    before = snapshot(client)
    login(client)
    response = client.post(BASE + "/analyses?auto_run=false",
                           files={"file": ("../../escaped.csv", CSV)})
    assert response.status_code == 202, response.text
    analysis_id = response.json()["analysis_id"]
    job = app.state.analysis_manager.get_job(analysis_id)
    root = Path(app.state.analyst_runtime_root).resolve()
    assert job.upload_path.resolve().is_relative_to(root)
    assert job.filename == "escaped.csv"
    for filename, content, expected in (("fake.sqlite", b"not a SQLite file", 422),
                                        ("model.joblib", b"\x80\x04malicious serialized model", 415),
                                        ("empty.csv", b"", 422)):
        response = client.post(BASE + "/analyses", files={"file": (filename, content)})
        assert response.status_code == expected, (filename, response.text)
    response = client.post(BASE + "/models/candidates",
                           json={"profile": "transaction_anomaly", "version": "../escape", "evaluation": {}})
    assert response.status_code == 422, response.text
    assert snapshot(client) == before


def test_uploaded_sqlite_connection_cannot_execute_writes(tmp_path):
    from app.risk_ledger.services.database_adapters import SqliteSourceAdapter

    source = tmp_path / "uploaded.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE evidence (value INTEGER)")
        connection.execute("INSERT INTO evidence VALUES (42)")
    before = source.read_bytes()
    connection = SqliteSourceAdapter._connect(source)
    try:
        assert connection.execute("SELECT value FROM evidence").fetchone() == (42,)
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("UPDATE evidence SET value=0")
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE injected (value INTEGER)")
    finally:
        connection.close()
    assert source.read_bytes() == before


def test_corrupted_saved_analysis_does_not_break_bank_startup(tmp_path):
    from app.risk_ledger.runtime import _restore_owned_results
    from app.risk_ledger.services.session_store import SessionStore

    store = SessionStore(tmp_path / "analyses")
    folder = store.root / str(uuid4())
    folder.mkdir()
    source = folder / "results.sqlite3"
    source.write_bytes(b"corrupted saved result database")

    def cannot_restore(*args, **kwargs):
        pytest.fail("Corrupted result must not become an accessible analysis")

    _restore_owned_results(SimpleNamespace(store=store, add_job=cannot_restore))
    assert source.read_bytes() == b"corrupted saved result database"


def test_invalid_saved_contract_cannot_leave_partially_restored_job(tmp_path):
    from app.risk_ledger.runtime import _restore_owned_results

    folder = tmp_path / str(uuid4())
    folder.mkdir()
    (folder / "results.sqlite3").write_bytes(b"existing result")
    store = SimpleNamespace(root=tmp_path, get_metadata=lambda _: {
        "owner_user_id": 1, "mapping": {"invalid_contract": True},
    })

    def cannot_restore(*args, **kwargs):
        pytest.fail("All persisted contracts must validate before publishing a restored job")

    _restore_owned_results(SimpleNamespace(store=store, add_job=cannot_restore))
