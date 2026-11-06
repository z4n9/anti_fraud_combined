"""Independent bank boundaries around the optional embedded SynSEPA model."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.demo_credentials import demo_password
from app.domain.models import Card, Transaction, TransferRequest, utcnow
from app.main import app


BASE = "/api/transfers"
BODY = {"recipient": "+77010000043", "recipient_name": "Айдана", "amount": "100",
        "message": "Independent model security"}
SECRET = "PRIVATE_MODEL_FEATURE_MARKER"


def audit(decision="REVIEW", *, status="available"):
    return {"status": status, "model_id": "synsepa_s0_clean_kzt", "model_version": "1.1-kzt-units",
            "currency": "KZT", "model_currency": "KZT", "unit_version": "kzt-native-v1",
            "score": 0.01, "ml_decision": "ALLOW", "rule_decision": decision, "decision": decision,
            "out_of_distribution": True, "ood_reasons": ["amount_outside_training_range"], "features": {SECRET: 123},
            "amount_kzt": 100, "thresholds": {"review": .3, "high_risk": .7},
            "checksums": {"model": SECRET}}


def inject(monkeypatch, result=None):
    import app.services.fraud_detector as detector
    calls = []

    def evaluate(history, **kwargs):
        calls.append((deepcopy(history), deepcopy(kwargs)))
        return deepcopy(result if result is not None else audit())

    monkeypatch.setattr(detector, "evaluate_synsepa", evaluate)
    return calls


def login(actor, code):
    response = actor.post("/api/auth/login", json={"test_iin": code, "password": demo_password(code)})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def ml_family(client):
    login(client, "TEST0006")
    response = client.put("/api/protection-settings", json={"protection_active": True,
        "notifications_enabled": True, "confirmation_enabled": True})
    assert response.status_code == 200, response.text
    son, daughter, employee, stranger = [TestClient(app) for _ in range(4)]
    actors = (son, daughter, employee, stranger)
    try:
        for actor, code in zip(actors, ("TEST0003", "TEST0007", "TEST0099", "TEST0005")):
            login(actor, code)
        for actor, code in ((son, "TEST0003"), (daughter, "TEST0007")):
            invited = client.post("/api/trusted-invitations", json={"trusted_iin": code})
            assert invited.status_code == 200, invited.text
            accepted = actor.post(f"/api/trusted-invitations/{invited.json()['id']}/accept")
            assert accepted.status_code == 200, accepted.text
        yield client, son, daughter, employee, stranger
    finally:
        for actor in actors:
            actor.close()


def snapshot(owner):
    with owner.test_factory() as db:
        return (list(db.execute(select(Card.id, Card.balance).order_by(Card.id))),
                list(db.execute(select(Transaction.id, Transaction.amount).order_by(Transaction.id))),
                list(db.execute(select(TransferRequest.id, TransferRequest.status).order_by(TransferRequest.id))))


def create(owner, body=None, key=None):
    return owner.post(BASE, json=body or BODY, headers={"Idempotency-Key": key or str(uuid4())})


def vote(actor, request_id, action):
    return actor.post(f"{BASE}/requests/{request_id}/{action}")


def assert_client_redacted(response):
    assert SECRET not in response.text
    for forbidden in ('"ml_score"', '"features"', '"checksums"', '"ml_evaluation"',
                      '"demo_kzt_per_eur"', '"thresholds"'):
        assert forbidden not in response.text, response.text
    if isinstance(response.json(), dict):
        assert response.json().get("ml_assessment") is None


def test_ml_review_raises_risk_without_moving_money_and_audit_stays_private(ml_family, monkeypatch):
    owner, son, _, employee, stranger = ml_family
    calls = inject(monkeypatch)
    before = snapshot(owner)
    preview = owner.post(f"{BASE}/risk-check", json={"recipient": BODY["recipient"], "amount": BODY["amount"]})
    assert preview.status_code == 200 and preview.json()["overall_level"] == "high", preview.text
    assert snapshot(owner) == before
    assert_client_redacted(preview)
    created = create(owner)
    assert created.status_code == 200 and created.json()["status"] == "pending_approval", created.text
    request_id = created.json()["request_id"]
    assert snapshot(owner)[:2] == before[:2]
    assert len(calls) == 2
    for response in (created, owner.get(f"{BASE}/requests"), son.get(f"{BASE}/pending-requests")):
        assert response.status_code == 200
        assert_client_redacted(response)
    assert owner.get(f"/api/analyst/bank-events/{request_id}").status_code == 403
    assert stranger.get(f"/api/analyst/bank-events/{request_id}").status_code == 403
    with TestClient(app) as guest:
        assert guest.get(f"/api/analyst/bank-events/{request_id}").status_code == 401
    staff = employee.get(f"/api/analyst/bank-events/{request_id}")
    assert staff.status_code == 200 and staff.json()["ml_assessment"] is not None, staff.text
    assessment = staff.json()["ml_assessment"]
    assert assessment["decision"] == "REVIEW" and assessment["score"] == .01
    assert '"features"' not in staff.text and SECRET not in staff.text
    with owner.test_factory() as db:
        stored = json.loads(db.get(TransferRequest, request_id).policy_json)["ml_evaluation"]
        assert stored["decision"] == "REVIEW" and stored["features"][SECRET] == 123


@pytest.mark.parametrize("score,amount,level,status,checked,triggered", [
    (0.01, "1000000.25", "low", "completed", False, False),
    (0.4, "959999.99", "medium", "completed", True, False),
    (0.4, "960000", "high", "pending_approval", True, True),
    (0.9, "1000000.25", "critical", "pending_approval", False, False),
])
def test_model_gates_balance_check_in_preview_and_command(
        ml_family, monkeypatch, score, amount, level, status, checked, triggered):
    from app.ml import service

    owner, _, _, _, _ = ml_family
    monkeypatch.setenv("AMAN_ML_MODE", "required")
    model, _, _, _ = service._bundle(service.get_ml_status()["checksums"])
    monkeypatch.setattr(model, "predict", lambda features: score)
    before = snapshot(owner)
    preview = owner.post(f"{BASE}/risk-check", json={"recipient": BODY["recipient"], "amount": amount})
    assert preview.status_code == 200 and snapshot(owner) == before
    result = create(owner, {**BODY, "amount": amount})
    assert result.status_code == 200 and result.json()["status"] == status, result.text
    for risk in (preview.json(), result.json()["risk"]):
        assert risk["overall_level"] == level
        assert ("balance_share" in {factor["code"] for factor in risk["transaction_risk"]["factors"]}) is triggered
    with owner.test_factory() as db:
        stored = json.loads(db.get(TransferRequest, result.json()["request_id"]).policy_json)["ml_evaluation"]
        assert stored["balance_rule"]["evaluated"] is checked
        assert stored["balance_rule"]["triggered"] is triggered
    if status == "pending_approval":
        assert snapshot(owner)[:2] == before[:2]
    else:
        assert len(snapshot(owner)[1]) == len(before[1]) + 2


def test_source_high_risk_maps_to_critical_but_still_needs_one_valid_relative(ml_family, monkeypatch):
    owner, son, _, employee, _ = ml_family
    inject(monkeypatch, audit("HIGH_RISK"))
    before = snapshot(owner)
    result = create(owner)
    assert result.status_code == 200 and result.json()["risk"]["overall_level"] == "critical", result.text
    request_id = result.json()["request_id"]
    assert result.json()["status"] == "pending_approval"
    assert employee.post(f"/api/analyst/bank-events/{request_id}/approve", json={"note": "Без семейного решения"}).status_code == 409
    assert snapshot(owner)[:2] == before[:2]
    assert vote(owner, request_id, "approve").status_code == 403
    assert vote(son, request_id, "approve").json()["status"] == "completed"
    assert len(snapshot(owner)[1]) == len(before[1]) + 2


@pytest.mark.parametrize("preview", [True, False])
def test_required_model_failure_is_generic_503_without_any_bank_mutation(ml_family, monkeypatch, preview):
    from app.ml import MLUnavailable
    import app.services.fraud_detector as detector
    owner, _, _, _, _ = ml_family
    monkeypatch.setenv("AMAN_ML_MODE", "required")

    def broken(*args, **kwargs):
        raise MLUnavailable("C:/private/model-path/" + SECRET)

    monkeypatch.setattr(detector, "evaluate_synsepa", broken)
    before = snapshot(owner)
    result = (owner.post(f"{BASE}/risk-check", json={"recipient": BODY["recipient"], "amount": BODY["amount"]})
              if preview else create(owner))
    assert result.status_code == 503, result.text
    assert SECRET not in result.text and "private/model-path" not in result.text
    assert snapshot(owner) == before


def test_client_cannot_supply_ml_decision_model_features_or_server_timestamp(ml_family, monkeypatch):
    owner, _, _, _, _ = ml_family
    calls = inject(monkeypatch)
    before = snapshot(owner)
    for injected in ({"ml": {"decision": "ALLOW"}}, {"ml_assessment": {"score": 0}},
                     {"model_id": "unsafe-model"}, {"features": {"amount": 0}},
                     {"evaluated_at": "2099-01-01T00:00:00Z"}, {"sender_id": "another-user"}):
        for preview in (True, False):
            body = {"recipient": BODY["recipient"], "amount": BODY["amount"]} if preview else BODY
            result = (owner.post(f"{BASE}/risk-check", json={**body, **injected}) if preview
                      else create(owner, {**body, **injected}))
            assert result.status_code == 422, result.text
    assert not calls
    assert snapshot(owner) == before


def test_idempotency_and_stored_family_decision_do_not_rescore_or_depend_on_later_model_failure(ml_family, monkeypatch):
    owner, son, daughter, _, _ = ml_family
    calls = inject(monkeypatch)
    key = str(uuid4())
    result = create(owner, key=key)
    assert result.status_code == 200 and result.json()["status"] == "pending_approval", result.text
    request_id = result.json()["request_id"]
    assert create(owner, key=key).json()["request_id"] == request_id
    assert len(calls) == 1
    with owner.test_factory() as db:
        original_audit = json.loads(db.get(TransferRequest, request_id).policy_json)["ml_evaluation"]
    import app.services.fraud_detector as detector

    def forbidden(*args, **kwargs):
        pytest.fail("A stored transfer must not call ML again")

    monkeypatch.setattr(detector, "evaluate_synsepa", forbidden)
    monkeypatch.setenv("AMAN_ML_MODE", "required")
    completed = vote(son, request_id, "approve")
    assert completed.status_code == 200 and completed.json()["status"] == "completed", completed.text
    after = snapshot(owner)
    assert vote(daughter, request_id, "reject").status_code == 409
    replay = create(owner, key=key)
    assert replay.status_code == 200 and replay.json()["status"] == "completed", replay.text
    assert create(owner, {**BODY, "amount": "101"}, key).status_code == 409
    assert snapshot(owner) == after
    with owner.test_factory() as db:
        assert json.loads(db.get(TransferRequest, request_id).policy_json)["ml_evaluation"] == original_audit


def test_ml_pending_first_response_race_still_has_one_terminal_decision(ml_family, monkeypatch):
    owner, son, daughter, _, _ = ml_family
    calls = inject(monkeypatch)
    before = snapshot(owner)
    result = create(owner)
    assert result.status_code == 200 and result.json()["status"] == "pending_approval", result.text
    request_id = result.json()["request_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(vote, actor, request_id, action)
                   for actor, action in ((son, "approve"), (daughter, "reject"))]
        responses = [future.result() for future in futures]
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    winner = next(r.json() for r in responses if r.status_code == 200)
    after = snapshot(owner)
    assert winner["status"] in {"completed", "rejected"}
    assert len(after[1]) - len(before[1]) == (2 if winner["status"] == "completed" else 0)
    assert sum(balance for _, balance in before[0]) == sum(balance for _, balance in after[0])
    assert len(calls) == 1


def test_ml_history_excludes_future_and_equal_timestamp_requests(ml_family, monkeypatch):
    from app.services.ml_history import build_ml_history
    owner, _, _, _, _ = ml_family
    inject(monkeypatch)
    request_ids = [create(owner).json()["request_id"] for _ in range(3)]
    when = utcnow()
    with owner.test_factory() as db:
        for request_id, offset in zip(request_ids, (-1, 0, 1)):
            db.get(TransferRequest, request_id).created_at = when + timedelta(minutes=offset)
        db.commit()
        history, _ = build_ml_history(db, [], when)
        assert len(history) == 1, history


def test_completed_ml_request_and_paired_ledger_are_one_history_operation(ml_family, monkeypatch):
    from app.services.ml_history import build_ml_history
    owner, son, _, _, _ = ml_family
    inject(monkeypatch)
    result = create(owner)
    request_id = result.json()["request_id"]
    assert vote(son, request_id, "approve").json()["status"] == "completed"
    with owner.test_factory() as db:
        row = db.get(TransferRequest, request_id)
        debit = db.get(Transaction, row.transaction_id)
        history, _ = build_ml_history(db, [(debit, row.recipient_card_id)], utcnow() + timedelta(seconds=1))
        assert len(history) == 1, history


def test_native_kzt_accepts_one_tiyn_without_old_five_tenge_floor(monkeypatch):
    from app.ml import evaluate_synsepa
    from app.ml.history import kzt_amount
    monkeypatch.setenv("AMAN_ML_MODE", "required")
    assert kzt_amount(1) == .01
    assert kzt_amount(99) == .99
    assert kzt_amount(101) == 1.01
    when = datetime(2026, 11, 4, 10, tzinfo=timezone.utc)
    history = [{"id": "prior-two-tiyn", "sender_id": "card:1", "receiver_id": "card:2",
                "amount_cents": 2, "timestamp": when - timedelta(minutes=1), "status": "COMPLETED"}]
    result = evaluate_synsepa(history, sender_id="card:1", receiver_id="card:2",
                              amount_cents=1, evaluated_at=when)
    assert result["status"] == "available"
    assert result["features"]["amount"] == .01
    assert result["features"]["sender_avg_amount_30d"] == .02
    assert result["features"]["amount_sum_1h"] == .02
    assert result["amount_kzt"] == .01
    assert result["currency"] == result["model_currency"] == "KZT"
    assert result["unit_version"] == "kzt-native-v1"
    assert not ({"amount_eur", "kzt_per_eur", "demo_kzt_per_eur"} & result.keys())
    assert history[0]["amount_cents"] == 2


def test_obsolete_exchange_rate_environment_cannot_change_native_model_forecast(monkeypatch):
    from app.ml import evaluate_synsepa, get_ml_status
    monkeypatch.setenv("AMAN_ML_MODE", "required")
    when = datetime(2026, 11, 4, 10, tzinfo=timezone.utc)
    history = [{"id": f"prior-{i}", "sender_id": "card:1", "receiver_id": "card:2",
                "amount_cents": 120_000 + i, "timestamp": when - timedelta(days=i + 1),
                "status": "COMPLETED"} for i in range(4)]
    snapshots = []
    for obsolete_rate in ("500", "not-a-number", "NaN", "0", "999999999"):
        monkeypatch.setenv("AMAN_ML_KZT_PER_EUR", obsolete_rate)
        status = get_ml_status()
        assert status["status"] == "available" and status["currency"] == "KZT"
        assert not ({"kzt_per_eur", "demo_kzt_per_eur"} & status.keys())
        assessed = evaluate_synsepa(history, sender_id="card:1", receiver_id="card:3",
                                    amount_cents=123_456, evaluated_at=when)
        snapshots.append({key: assessed[key] for key in (
            "score", "decision", "ml_decision", "rule_decision", "features", "out_of_distribution")})
    assert all(snapshot == snapshots[0] for snapshot in snapshots)


def test_native_one_tiyn_transfer_changes_both_balances_by_one_tiyn_only(ml_family, monkeypatch):
    owner, son, _, employee, _ = ml_family
    monkeypatch.setenv("AMAN_ML_MODE", "required")
    before = snapshot(owner)
    result = create(owner, {**BODY, "amount": "0.01", "risk_warning_acknowledged": True})
    assert result.status_code == 200, result.text
    request_id = result.json()["request_id"]
    if result.json()["status"] == "pending_approval":
        assert snapshot(owner)[:2] == before[:2]
        approved = vote(son, request_id, "approve")
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "completed", approved.text
    else:
        assert result.json()["status"] == "completed", result.text
    after = snapshot(owner)
    differences = [new - old for (_, old), (_, new) in zip(before[0], after[0]) if new != old]
    assert sorted(differences) == [-1, 1]
    assert len(after[1]) == len(before[1]) + 2
    with owner.test_factory() as db:
        row = db.get(TransferRequest, request_id)
        native = json.loads(row.policy_json)["ml_evaluation"]
        assert row.amount_cents == 1
        assert native["amount_kzt"] == native["features"]["amount"] == .01
        assert "amount_eur" not in native
    staff = employee.get(f"/api/analyst/bank-events/{request_id}")
    assert staff.status_code == 200 and staff.json()["ml_assessment"]["currency"] == "KZT"
    assert "demo_kzt_per_eur" not in staff.json()["ml_assessment"]


def test_historical_eur_audit_remains_archived_and_does_not_become_native_kzt(ml_family, monkeypatch):
    owner, _, _, employee, _ = ml_family
    calls = inject(monkeypatch)
    request_id = create(owner).json()["request_id"]
    old = {"status": "available", "model_id": "synsepa_s0_clean", "model_version": "1.0",
           "score": .123, "decision": "REVIEW", "ml_decision": "ALLOW", "rule_decision": "REVIEW",
           "demo_kzt_per_eur": 500, "amount_eur": .2, "features": {SECRET: 999}}
    with owner.test_factory() as db:
        row = db.get(TransferRequest, request_id)
        policy = json.loads(row.policy_json)
        policy["ml_evaluation"] = old
        row.policy_json = json.dumps(policy)
        risk = json.loads(row.risk_json)
        risk["ml"] = {"status": "available", "model_id": "synsepa_s0_clean", "model_version": "1.0",
                      "decision": "REVIEW", "out_of_distribution": False}
        row.risk_json = json.dumps(risk)
        db.commit()
    before = snapshot(owner)
    staff = employee.get(f"/api/analyst/bank-events/{request_id}")
    assert staff.status_code == 200, staff.text
    archived = staff.json()["ml_assessment"]
    assert archived["model_id"] == "synsepa_s0_clean" and archived["demo_kzt_per_eur"] == 500
    assert archived.get("unit_version") != "kzt-native-v1"
    assert archived.get("currency") != "KZT" and archived.get("model_currency") != "KZT"
    own = owner.get(f"{BASE}/requests")
    assert own.status_code == 200
    assert_client_redacted(own)
    recorded = next(row for row in own.json() if row["id"] == request_id)
    assert recorded["risk"]["ml"].get("unit_version") != "kzt-native-v1"
    assert snapshot(owner) == before
    assert len(calls) == 1
    with owner.test_factory() as db:
        assert json.loads(db.get(TransferRequest, request_id).policy_json)["ml_evaluation"] == old
