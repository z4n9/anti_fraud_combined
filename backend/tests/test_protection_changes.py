"""Delayed protection lifecycle on isolated fixture databases."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.models import Card, ProtectionChangeRequest, Transaction, TrustedPerson, User
from app.main import app
from app.services import protection_changes

BASE = '/api/protection-change-requests'
NOW = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)


def login(client, code):
    assert client.post('/api/auth/login', json={'test_iin': code, 'password': f'Aman-Test-{code[-4:]}!'}).status_code == 200


@pytest.fixture
def protected_family(client):
    login(client, 'TEST0006')
    assert client.put('/api/protection-settings', json={'protection_active': True,
        'notifications_enabled': True, 'confirmation_enabled': True}).status_code == 200
    invite = client.post('/api/trusted-invitations', json={'trusted_iin': 'TEST0003'}).json()
    with TestClient(app) as son:
        login(son, 'TEST0003')
        assert son.post(f"/api/trusted-invitations/{invite['id']}/accept").status_code == 200
        yield client, son


def snapshot(client):
    with client.test_factory() as db:
        return (list(db.execute(select(Card.id, Card.balance)).all()),
                list(db.execute(select(Transaction.id, Transaction.amount)).all()))


@pytest.mark.parametrize('action', ['disable', 'remove'])
def test_exact_deadline_preserves_before_and_applies_at_24_hours(protected_family, monkeypatch, action):
    owner, _ = protected_family
    monkeypatch.setattr(protection_changes, 'utcnow', lambda: NOW)
    before = snapshot(owner)
    created = owner.post(BASE, json={'action': action})
    assert created.status_code == 200, created.text
    row = created.json()
    assert row['status'] == 'pending' and row['effective_at'] == (NOW + timedelta(hours=24)).isoformat()
    assert owner.post(BASE, json={'action': action}).json() == row
    monkeypatch.setattr(protection_changes, 'utcnow', lambda: NOW + timedelta(hours=24, microseconds=-1))
    person = owner.get('/api/trusted-person').json()
    assert person['family_protection_ready'] and person['invitation_status'] == 'accepted'
    monkeypatch.setattr(protection_changes, 'utcnow', lambda: NOW + timedelta(hours=24))
    person = owner.get('/api/trusted-person').json()
    assert person['protection_active'] is False
    if action == 'remove':
        assert person['current_invitation_id'] is None and not person['relationship_verified']
        assert owner.get('/api/trusted-invitations/current').json() is None
    else:
        assert person['invitation_status'] == 'accepted'
    saved = owner.get(BASE).json()[0]
    assert saved['status'] == 'executed' and saved['decided_at'] == (NOW + timedelta(hours=24)).isoformat()
    assert not saved['can_cancel']
    assert owner.post(f"{BASE}/{row['id']}/cancel").status_code == 409
    assert snapshot(owner) == before


def test_cancelled_change_survives_deadline_and_new_request_has_new_deadline(protected_family, monkeypatch):
    owner, _ = protected_family
    monkeypatch.setattr(protection_changes, 'utcnow', lambda: NOW)
    first = owner.post(BASE, json={'action': 'disable'}).json()
    cancelled = owner.post(f"{BASE}/{first['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'
    assert owner.post(f"{BASE}/{first['id']}/cancel").json() == cancelled.json()
    monkeypatch.setattr(protection_changes, 'utcnow', lambda: NOW + timedelta(hours=25))
    assert owner.get('/api/trusted-person').json()['protection_active']
    second = owner.post(BASE, json={'action': 'disable'}).json()
    assert second['id'] != first['id']
    assert second['effective_at'] == (NOW + timedelta(hours=49)).isoformat()


def test_settings_disable_schedules_but_confirmation_cannot_be_disabled(protected_family):
    owner, _ = protected_family
    response = owner.put('/api/protection-settings', json={'protection_active': False,
        'notifications_enabled': False, 'confirmation_enabled': True})
    assert response.status_code == 200
    assert response.json()['protection_active'] and response.json()['confirmation_enabled']
    assert response.json()['notifications_enabled'] is False
    assert owner.get(BASE).json()[0]['action'] == 'disable'
    response = owner.put('/api/protection-settings', json={'protection_active': True,
        'notifications_enabled': True, 'confirmation_enabled': False})
    assert response.status_code == 409
    assert owner.get('/api/trusted-person').json()['confirmation_enabled']


def test_additive_relative_does_not_remove_accepted_consent_even_if_disabled(protected_family, monkeypatch):
    owner, _ = protected_family
    monkeypatch.setattr(protection_changes, 'utcnow', lambda: NOW)
    owner.post(BASE, json={'action': 'disable'})
    monkeypatch.setattr(protection_changes, 'utcnow', lambda: NOW + timedelta(hours=24))
    assert not owner.get('/api/trusted-person').json()['protection_active']
    old_id = owner.get('/api/trusted-person').json()['current_invitation_id']
    assert owner.post('/api/trusted-invitations', json={'trusted_iin': 'TEST0007'}).status_code == 200
    assert owner.get('/api/trusted-person').json()['current_invitation_id'] == old_id
    remove = owner.post(BASE, json={'action': 'remove', 'invitation_id': old_id}).json()
    assert remove['status'] == 'pending'
    monkeypatch.setattr(protection_changes, 'utcnow', lambda: NOW + timedelta(hours=48))
    response = owner.post('/api/trusted-invitations', json={'trusted_iin': 'TEST0007'})
    assert response.status_code == 200, response.text
    assert response.json()['trusted_person_test_iin'] == 'TEST0007'
    assert any(row['status'] == 'executed' and row['action'] == 'remove' for row in owner.get(BASE).json())


def test_due_change_applies_on_risk_preview_without_ledger_mutation(protected_family, monkeypatch):
    owner, _ = protected_family
    monkeypatch.setattr(protection_changes, 'utcnow', lambda: NOW)
    owner.post(BASE, json={'action': 'disable'})
    before = snapshot(owner)
    monkeypatch.setattr(protection_changes, 'utcnow', lambda: NOW + timedelta(hours=24))
    response = owner.post('/api/transfers/risk-check', json={'recipient': '+77010000043', 'amount': '1'})
    assert response.status_code == 200, response.text
    with owner.test_factory() as db:
        change = db.scalar(select(ProtectionChangeRequest))
        person = db.scalar(select(TrustedPerson).where(TrustedPerson.user_id == change.user_id))
        assert change.status == 'executed' and not person.protection_active
    assert snapshot(owner) == before
