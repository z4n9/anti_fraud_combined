"""Portable HTTP acceptance check. Writes only when explicitly requested for a demo."""
import argparse
from decimal import Decimal
from http.cookiejar import CookieJar
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4


class Client:
    def __init__(self, base, code=None):
        self.base, self.account_id = base.rstrip('/'), None
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        if code:
            self.account_id = self.call('/api/auth/login', 'POST', {
                'test_iin': code, 'password': f'Aman-Test-{code[-4:]}!'})['id']

    def raw(self, path, method='GET', body=None, *, content_type='application/json', key=None):
        payload = json.dumps(body).encode() if isinstance(body, dict) else body
        headers = {'Origin': self.base, 'Content-Type': content_type}
        if self.account_id is not None:
            headers['X-Account-ID'] = str(self.account_id)
        if key:
            headers['Idempotency-Key'] = key
        request = Request(self.base + path, data=payload, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            return error.code, error.read(), error.headers

    def call(self, path, method='GET', body=None, *, expected=200, key=None):
        status, data, _ = self.raw(path, method, body, key=key)
        assert status == expected, (path, status, data.decode(errors='replace'))
        return json.loads(data) if data else None


def money(client):
    return int(Decimal(str(client.call('/api/cards')[0]['balance'])) * 100)


def wait(client, analysis_id, states):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        result = client.call(f'/api/analyst/analyses/{analysis_id}/status')
        if result['status'] in states:
            return result
        time.sleep(0.1)
    raise AssertionError('Analysis did not complete within 60 seconds')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--confirm-demo-writes', action='store_true')
    parser.add_argument('--state-out', type=Path)
    parser.add_argument('--verify-state', type=Path)
    args = parser.parse_args()
    if not args.confirm_demo_writes and not args.verify_state:
        parser.error('Use --confirm-demo-writes on a disposable demo, or --verify-state after restart.')
    base = args.base_url
    guest = Client(base)
    assert guest.call('/api/readiness')['status'] == 'ok'
    owner, son, daughter, receiver, employee = [Client(base, code) for code in
        ('TEST0006', 'TEST0003', 'TEST0007', 'TEST0002', 'TEST0099')]
    bank = '/api/analyst/bank-events'
    if args.verify_state:
        saved = json.loads(args.verify_state.read_text(encoding='utf-8'))
        assert [money(owner), money(receiver)] == saved['balances']
        event = employee.call(f"{bank}/{saved['request_id']}")
        assert event['status'] == 'completed' and len(event['bank_decisions']) == 1
        assert len(event['participants']) == 2
        analysis = employee.call(f"/api/analyst/analyses/{saved['analysis_id']}/status")
        assert analysis['status'] == 'completed'
        assert employee.call(f"/api/analyst/analyses/{saved['analysis_id']}/transactions")['total'] == 7
        print('PASS: balances, frozen participants, decision audit and uploaded analysis survived recreation.')
        return
    guest.call(bank, expected=401)
    owner.call(bank, expected=403)
    employee.call('/api/cards', expected=403)
    owner.call('/api/protection-settings', 'PUT', {'protection_active': True,
        'notifications_enabled': True, 'confirmation_enabled': True})
    for relative, code in ((son, 'TEST0003'), (daughter, 'TEST0007')):
        existing = owner.call('/api/trusted-invitations')
        if any(item['active'] and item['status'] == 'accepted' and
               item['trusted_person_test_iin'] == code for item in existing):
            continue
        invitation = owner.call('/api/trusted-invitations', 'POST', {'trusted_iin': code})
        relative.call(f"/api/trusted-invitations/{invitation['id']}/accept", 'POST')
    before = [money(owner), money(receiver)]
    cents = int((Decimal(before[0]) * Decimal('0.85')).to_integral_value())
    assert cents >= 100000, 'Use a fresh rehearsal volume with sufficient demo funds.'
    history = [len(person.call('/api/transactions')) for person in (owner, receiver)]
    body = {'recipient': '+77010000043', 'recipient_name': 'Айдана',
        'amount': str(Decimal(cents) / 100), 'message': 'Contest MVP acceptance',
        'anti_scam': {'pressure': False, 'secrecy': False, 'stranger': False}}
    key = str(uuid4())
    created = owner.call('/api/transfers', 'POST', body, key=key)
    assert created['status'] == 'pending_approval'
    request_id = created['request_id']
    employee.call(f'{bank}/{request_id}/approve', 'POST', {'note': 'Before family votes'}, expected=409)
    assert son.call(f'/api/transfers/requests/{request_id}/approve', 'POST')['status'] == 'pending_approval'
    assert [money(owner), money(receiver)] == before
    assert daughter.call(f'/api/transfers/requests/{request_id}/reject', 'POST')['status'] == 'bank_review'
    assert [money(owner), money(receiver)] == before
    assert [len(person.call('/api/transactions')) for person in (owner, receiver)] == history
    note = {'note': 'Reviewed family conflict during contest MVP acceptance'}
    event = employee.call(f'{bank}/{request_id}/approve', 'POST', note)
    assert event['status'] == 'completed' and len(event['bank_decisions']) == 1
    after = [before[0] - cents, before[1] + cents]
    assert [money(owner), money(receiver)] == after
    employee.call(f'{bank}/{request_id}/approve', 'POST', note)
    owner.call('/api/transfers', 'POST', body, key=key)
    assert [money(owner), money(receiver)] == after
    assert [len(person.call('/api/transactions')) for person in (owner, receiver)] == [n + 1 for n in history]
    scam = owner.call('/api/transfers', 'POST', {'recipient': '+77010000043', 'recipient_name': 'Айдана',
        'amount': '1.00', 'anti_scam': {'pressure': True}}, key=str(uuid4()))
    assert scam['status'] == 'bank_review'
    employee.call(f"{bank}/{scam['request_id']}/reject", 'POST', {'note': 'Pressure reported; declined'})
    assert [money(owner), money(receiver)] == after
    source = (Path(__file__).resolve().parents[1] / 'examples/transactions_demo.csv').read_bytes()
    boundary = 'mvp-' + uuid4().hex
    multipart = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="demo.csv"\r\n'
        'Content-Type: text/csv\r\n\r\n').encode() + source + f'\r\n--{boundary}--\r\n'.encode()
    status, raw, _ = employee.raw('/api/analyst/analyses?auto_run=false', 'POST', multipart,
        content_type=f'multipart/form-data; boundary={boundary}')
    assert status == 202, raw.decode(errors='replace')
    analysis_id = json.loads(raw)['analysis_id']
    assert wait(employee, analysis_id, {'planned', 'failed'})['status'] == 'planned'
    employee.call(f'/api/analyst/analyses/{analysis_id}/run', 'POST', expected=202)
    assert wait(employee, analysis_id, {'completed', 'failed'})['status'] == 'completed'
    rows = employee.call(f'/api/analyst/analyses/{analysis_id}/transactions')
    assert rows['total'] == 7
    assert next(item for item in rows['items'] if item['transaction_id'] == 'demo-007')['risk_signal_score'] >= 0.72
    employee.call(f'/api/analyst/analyses/{analysis_id}/investigations/demo-007', 'PATCH',
        {'status': 'in_review', 'comment': 'Contest acceptance review'})
    assert employee.raw(f'/api/analyst/analyses/{analysis_id}/exports/transactions')[0] == 200
    assert [money(owner), money(receiver)] == after
    for path in ('/', '/analyst/', '/analyst/bank-events', '/docs'):
        assert guest.raw(path)[0] == 200, path
    for path in ('/aman_bank.db', '/data/aman_bank.db', '/runtime/risk_ledger', '/deploy/.env', '/backend/app/main.py'):
        assert guest.raw(path)[0] == 404, path
    if args.state_out:
        args.state_out.parent.mkdir(parents=True, exist_ok=True)
        args.state_out.write_text(json.dumps({'balances': after, 'request_id': request_id,
            'analysis_id': analysis_id}), encoding='utf-8')
    print('PASS: two family consents, conflict hold, one bank transfer/audit, replay, AntiScam, '
          'upload/analysis/review/export, readiness, role boundaries and private files.')


if __name__ == '__main__':
    main()
