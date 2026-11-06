"""Server-owned pre-transfer balance, private snapshots and frozen consent."""
from copy import deepcopy
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app.core.demo_credentials import demo_password
from app.domain.models import Card, Transaction, TransferRequest, User
from app.main import app


RECIPIENT = '+77010000043'
PRIVATE_KEYS = ('balance_context', 'balance_before_cents', 'projected_balance_after_cents',
                'amount_to_balance_ratio', 'sender_balance_cents', 'balance_rule')


def login(actor, code):
    response = actor.post('/api/auth/login', json={
        'test_iin': code, 'password': demo_password(code)})
    assert response.status_code == 200, response.text


def set_balance(client, code, cents):
    with client.test_factory() as db:
        user = db.scalar(select(User).where(User.test_iin == code))
        card = db.scalar(select(Card).where(Card.user_id == user.id,
            Card.currency == 'KZT', Card.status == 'active').order_by(Card.id))
        card.balance = cents
        db.commit()
        return card.id


def snapshot(client):
    with client.test_factory() as db:
        return (list(db.execute(select(Card.id, Card.balance).order_by(Card.id))),
                list(db.execute(select(Transaction.id, Transaction.amount).order_by(Transaction.id))))


def private_audit(client, request_id):
    with client.test_factory() as db:
        return json.loads(db.get(TransferRequest, request_id).policy_json)['ml_evaluation']


def assert_private(response):
    assert response.status_code == 200, response.text
    for key in PRIVATE_KEYS:
        assert f'"{key}"' not in response.text, response.text


@pytest.mark.parametrize('mode', ['required', 'off', 'auto-missing'])
def test_balance_is_read_from_bank_again_for_command_and_saved_before_debit(client, monkeypatch, mode):
    import app.services.fraud_detector as detector
    monkeypatch.setenv('AMAN_ML_MODE', 'auto' if mode == 'auto-missing' else mode)
    if mode == 'auto-missing':
        monkeypatch.setenv('AMAN_ML_ASSET_DIR', 'missing-balance-test-assets')
    real_evaluate, calls = detector.evaluate_synsepa, []

    def evaluate(history, **kwargs):
        result = real_evaluate(history, **kwargs)
        calls.append((deepcopy(kwargs), deepcopy(result)))
        return result

    monkeypatch.setattr(detector, 'evaluate_synsepa', evaluate)
    card_id = set_balance(client, 'TEST0001', 200_000)
    before = snapshot(client)
    preview = client.post('/api/transfers/risk-check', json={'recipient': RECIPIENT, 'amount': '1000'})
    assert_private(preview)
    assert snapshot(client) == before
    assert calls[0][0]['sender_balance_cents'] == 200_000
    assert calls[0][1]['balance_context']['amount_to_balance_ratio'] == .5
    with client.test_factory() as db:
        assert list(db.scalars(select(TransferRequest))) == []

    # A command must use the current balance, not the earlier preview's value.
    set_balance(client, 'TEST0001', 125_000)
    response = client.post('/api/transfers', json={'recipient': RECIPIENT,
        'recipient_name': 'Айдана', 'amount': '1000', 'risk_warning_acknowledged': True},
        headers={'Idempotency-Key': str(uuid4())})
    assert_private(response)
    assert response.json()['status'] == 'completed', response.text
    assert len(calls) == 2 and calls[1][0]['sender_balance_cents'] == 125_000
    audit = private_audit(client, response.json()['request_id'])
    context = audit['balance_context']
    assert context['balance_before_cents'] == 125_000
    assert context['amount_cents'] == 100_000
    assert context['projected_balance_after_cents'] == 25_000
    assert context['amount_to_balance_ratio'] == .8
    assert audit['status'] == {'required': 'available', 'off': 'disabled', 'auto-missing': 'unavailable'}[mode]
    if mode == 'required':
        assert len(audit['features']) == 21
        assert not any('balance' in name for name in audit['features'])
    assert dict(snapshot(client)[0])[card_id] == 25_000


def test_family_and_replay_keep_original_private_balance_snapshot(client, monkeypatch):
    from app.ml import service

    monkeypatch.setenv('AMAN_ML_MODE', 'required')
    model, _, _, _ = service._bundle(service.get_ml_status()['checksums'])
    monkeypatch.setattr(model, 'predict', lambda features: 0.4)
    login(client, 'TEST0006')
    assert client.put('/api/protection-settings', json={'protection_active': True,
        'confirmation_enabled': True, 'notifications_enabled': True}).status_code == 200
    invite = client.post('/api/trusted-invitations', json={'trusted_iin': 'TEST0003'})
    assert invite.status_code == 200, invite.text
    with TestClient(app) as relative, TestClient(app) as analyst:
        login(relative, 'TEST0003')
        login(analyst, 'TEST0099')
        assert relative.post(f"/api/trusted-invitations/{invite.json()['id']}/accept").status_code == 200
        card_id = set_balance(client, 'TEST0006', 100_000)
        before = snapshot(client)
        body = {'recipient': RECIPIENT, 'recipient_name': 'Айдана', 'amount': '800'}
        key = str(uuid4())
        response = client.post('/api/transfers', json=body, headers={'Idempotency-Key': key})
        assert_private(response)
        assert response.json()['status'] == 'pending_approval', response.text
        request_id = response.json()['request_id']
        original = private_audit(client, request_id)
        assert original['balance_context']['balance_before_cents'] == 100_000
        assert original['balance_context']['projected_balance_after_cents'] == 20_000
        assert snapshot(client) == before

        def no_rescoring(*args, **kwargs):
            raise AssertionError('A frozen transfer must not be rescored')

        monkeypatch.setattr('app.services.fraud_detector.evaluate_synsepa', no_rescoring)
        set_balance(client, 'TEST0006', 90_000)
        for result in (client.get('/api/transfers/requests'),
                       relative.get('/api/transfers/pending-requests'),
                       analyst.get(f'/api/analyst/bank-events/{request_id}'),
                       client.post('/api/transfers', json=body, headers={'Idempotency-Key': key})):
            assert_private(result)
        approved = relative.post(f'/api/transfers/requests/{request_id}/approve')
        assert_private(approved)
        assert approved.json()['status'] == 'completed'
        posted = snapshot(client)
        assert dict(posted[0])[card_id] == 10_000
        replay = client.post('/api/transfers', json=body, headers={'Idempotency-Key': key})
        assert_private(replay)
        assert replay.json()['status'] == 'completed' and snapshot(client) == posted
        assert private_audit(client, request_id) == original


def test_client_cannot_supply_balance_context(client, monkeypatch):
    def should_not_run(*args, **kwargs):
        raise AssertionError('Untrusted balance must be rejected before scoring')

    monkeypatch.setattr('app.services.fraud_detector.evaluate_synsepa', should_not_run)
    before = snapshot(client)
    for field in ('sender_balance_cents', 'sender_balance', 'balance_context',
                  'amount_to_balance_ratio', 'projected_balance_after_cents'):
        data = {'recipient': RECIPIENT, 'amount': '100', field: 999_999_999}
        preview = client.post('/api/transfers/risk-check', json=data)
        command = client.post('/api/transfers', json={**data, 'recipient_name': 'Айдана'},
            headers={'Idempotency-Key': str(uuid4())})
        assert preview.status_code == command.status_code == 422
    assert snapshot(client) == before
