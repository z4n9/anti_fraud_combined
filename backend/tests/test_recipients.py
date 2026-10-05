from uuid import uuid4
import pytest

pytestmark = pytest.mark.usefixtures("low_transfer_risk")
def test_recipient_lookup_and_authoritative_name(client):
    response = client.get('/api/recipients', params={'phone': '+7 (702) 000-00-44'})
    assert response.status_code == 200
    assert response.json() == {'name': 'Марат Омаров', 'phone': '+77020000044'}
    body = {'recipient': '+7 (702) 000-00-44', 'recipient_name': 'Айдана', 'amount': 1}
    result = client.post('/api/transfers', json=body, headers={'Idempotency-Key': 'recipient-test'})
    assert result.status_code == 200
    assert result.json()['recipient_name'] == 'Марат Омаров'
    assert client.get('/api/transactions').json()[0]['title'] == 'Марат Омаров'
    assert client.post('/api/transfers', json=body, headers={'Idempotency-Key': 'recipient-test'}).json() == result.json()

def test_unknown_recipient_does_not_debit(client):
    before = client.get('/api/cards').json()
    assert client.get('/api/recipients', params={'phone': '+77099999999'}).status_code == 404
    assert client.get('/api/recipients', params={'phone': 'oops'}).status_code == 422
    assert client.post('/api/transfers', json={'recipient': '+77099999999', 'recipient_name': 'Марат', 'amount': 1}, headers={'Idempotency-Key': str(uuid4())}).status_code == 404
    assert client.get('/api/cards').json() == before
    client.post('/api/auth/logout')
    assert client.get('/api/recipients', params={'phone': '+77020000044'}).status_code == 401
