from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.domain.models import LoginSession, User
from app import seed

def signin(client, code):
    response=client.post('/api/auth/login',json={'test_iin':code,'password':f'Aman-Test-{code[-4:]}!'})
    assert response.status_code==200,response.text
    return response.json()

def enable(client, value=True):
    result=client.put('/api/protection-settings',json={'protection_active':value,'notifications_enabled':True,'confirmation_enabled':True})
    assert result.status_code==200,result.text

def invite(client, code='TEST0003'):
    response=client.post('/api/trusted-invitations',json={'trusted_iin':code})
    assert response.status_code==200,response.text
    return response.json()

def ready(client):
    return client.get('/api/trusted-person').json()['family_protection_ready']

def test_grandmother_son_full_flow(client):
    grandma=signin(client,'TEST0006')
    enable(client)
    checked=client.post('/api/mock-egov/verify-relationship',json={'trusted_iin':'TEST0003'}).json()
    assert checked['verified'] and checked['relationship']=='сын'
    assert checked['trusted_person']['full_name']=='Марат Омаров'
    assert set(checked['trusted_person'])=={'test_iin','full_name','phone'}
    invitation=invite(client)
    assert invitation['owner_user_id']==grandma['id']
    assert invitation['status']=='pending'
    assert not ready(client)
    assert client.get('/api/trusted-invitations/incoming').json()==[]
    assert client.post(f"/api/trusted-invitations/{invitation['id']}/accept").status_code==403
    assert client.post(f"/api/trusted-invitations/{invitation['id']}/reject").status_code==403
    with TestClient(app) as son:
        account=signin(son,'TEST0003')
        incoming=son.get('/api/trusted-invitations/incoming').json()
        assert len(incoming)==1 and incoming[0]['reverse_relationship']=='мать'
        assert incoming[0]['recipient_user_id']==account['id']
        response=son.post(f"/api/trusted-invitations/{invitation['id']}/accept")
        assert response.status_code==200,response.text
        assert response.json()['accepted_by_user_id']==account['id']
        assert son.post(f"/api/trusted-invitations/{invitation['id']}/accept").status_code==200
        assert son.post(f"/api/trusted-invitations/{invitation['id']}/reject").status_code==409
        assert son.get('/api/cards').json()[0]['balance']==0
        assert son.get('/api/transactions').json()==[]
    assert ready(client)
    assert client.get('/api/cards').json()[0]['balance']==1200000
    seed.seed_data()
    assert ready(client)
    enable(client,False)
    assert ready(client)
    change = client.get('/api/protection-change-requests').json()[0]
    assert change['action'] == 'disable' and change['status'] == 'pending'
    enable(client,True)
    assert ready(client)
    assert client.get('/api/protection-change-requests').json()[0]['status'] == 'pending'
    cancelled = client.post(f"/api/protection-change-requests/{change['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'
    assert ready(client)


def test_daughter_reject_and_unrelated_account(client):
    signin(client,'TEST0006');enable(client)
    check=client.post('/api/mock-egov/verify-relationship',json={'trusted_iin':'TEST0007'}).json()
    assert check['relationship']=='дочь'
    invitation=invite(client,'TEST0007')
    with TestClient(app) as stranger:
        signin(stranger,'TEST0005')
        assert stranger.get('/api/trusted-invitations/incoming').json()==[]
        assert stranger.post(f"/api/trusted-invitations/{invitation['id']}/accept").status_code==403
        assert stranger.post(f"/api/trusted-invitations/{invitation['id']}/reject").status_code==403
    with TestClient(app) as daughter:
        signin(daughter,'TEST0007')
        assert daughter.post(f"/api/trusted-invitations/{invitation['id']}/reject").status_code==200
        assert daughter.post(f"/api/trusted-invitations/{invitation['id']}/reject").status_code==200
    assert not ready(client)
    assert client.get('/api/trusted-person').json()['invitation_status']=='rejected'


def test_relationship_failure_and_no_impersonation(client):
    signin(client,'TEST0006')
    for code,reason in [('TEST0005','relationship_not_found'),('TEST9999','citizen_not_found'),('TEST0006','relationship_not_found')]:
        assert client.post('/api/mock-egov/verify-relationship',json={'trusted_iin':code}).json()=={'verified':False,'reason':reason}
        assert client.post('/api/trusted-invitations',json={'trusted_iin':code}).status_code==400
    assert client.post('/api/mock-egov/verify-relationship',json={'client_iin':'TEST0001','trusted_iin':'TEST0002'}).status_code==403
    assert client.post('/api/mock-egov/verify-relationship',json={'trusted_iin':'123456789012'}).status_code==422
    for key,value in [('status','accepted'),('recipient_user_id',1),('owner_user_id',1),('relationship_verified',True)]:
        assert client.post('/api/trusted-invitations',json={'trusted_iin':'TEST0003',key:value}).status_code==422
    assert client.get('/api/trusted-invitations/current').json() is None
    assert client.get('/api/mock-egov/citizens').status_code==404


def test_replaced_invitation_cannot_be_accepted(client):
    signin(client,'TEST0006');enable(client)
    old=invite(client)
    assert invite(client)['id']==old['id']
    new=invite(client,'TEST0007')
    with TestClient(app) as son:
        signin(son,'TEST0003')
        assert son.get('/api/trusted-invitations/incoming').json()==[]
        assert son.post(f"/api/trusted-invitations/{old['id']}/accept").status_code==409
    with TestClient(app) as daughter:
        signin(daughter,'TEST0007')
        assert daughter.post(f"/api/trusted-invitations/{new['id']}/accept").status_code==200
    assert ready(client)


def test_concurrent_responses_and_duplicate_invites(client):
    signin(client,'TEST0006');enable(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        invites=list(pool.map(lambda _:invite(client),range(2)))
    assert invites[0]['id']==invites[1]['id']
    with TestClient(app) as son:
        signin(son,'TEST0003')
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda answer:son.post(f"/api/trusted-invitations/{invites[0]['id']}/{answer}"),['accept','reject']))
        assert sorted(r.status_code for r in responses)==[200,409]
    saved=client.get('/api/trusted-person').json()
    assert saved['family_protection_ready']==(saved['invitation_status']=='accepted')


def test_authentication_logout_expiry_and_no_demo_bypass(client):
    with TestClient(app) as anonymous:
        for path in ['/api/user','/api/cards','/api/transactions','/api/trusted-person','/api/trusted-invitations/incoming']:
            assert anonymous.get(path).status_code==401
        assert anonymous.post('/api/auth/login',json={'test_iin':'TEST0006','password':'wrong'}).status_code==401
        assert anonymous.post('/api/auth/login',json={'test_iin':'TEST9999','password':'Aman-Test-9999!'}).status_code==401
        assert anonymous.post('/api/trusted-invitations',json={'trusted_iin':'TEST0003'}).status_code==401
        login=anonymous.post('/api/auth/login',json={'test_iin':'TEST0006','password':'Aman-Test-0006!'})
        assert 'HttpOnly' in login.headers['set-cookie'] and 'SameSite=strict' in login.headers['set-cookie']
        cookie=anonymous.cookies.get('aman_session')
        assert anonymous.post('/api/auth/logout').status_code==200
        anonymous.cookies.set('aman_session',cookie)
        assert anonymous.get('/api/user').status_code==401
    for path in ['/demo-accept','/demo-reject']:
        assert client.post('/api/trusted-invitations/1'+path).status_code in [404,405]
    assert client.post('/api/auth/logout',headers={'Origin':'https://attacker.invalid'}).status_code==403
    assert client.get('/api/cards',headers={'X-Account-ID':'999'}).status_code==409


def test_financial_and_setting_isolation(client):
    grandma=signin(client,'TEST0006');enable(client)
    grandma_balance=client.get('/api/cards').json()[0]['balance']
    invitation=invite(client)
    with TestClient(app) as son:
        signin(son,'TEST0003')
        assert son.get(f"/api/cards?user_id={grandma['id']}").json()[0]['balance']==0
        response=son.post('/api/transfers',json={'recipient':'+77010000043','recipient_name':'Тест','amount':1}, headers={'Idempotency-Key': str(uuid4())})
        assert response.status_code==400
        assert not ready(son)
        son.put('/api/protection-settings',json={'protection_active':False,'notifications_enabled':False,'confirmation_enabled':False})
    assert client.get('/api/cards').json()[0]['balance']==grandma_balance
    assert client.get('/api/trusted-person').json()['protection_active'] is True


def test_password_hashes_and_session_hashes(client):
    with client.test_factory() as db:
        users=list(db.scalars(select(User)))
        assert len(users)==8
        assert sum(u.role == 'client' for u in users) == 7
        assert sum(u.role == 'analyst' for u in users) == 1
        assert all(u.password_hash and 'Aman-Test' not in u.password_hash for u in users)
        token=client.cookies.get('aman_session')
        sessions=list(db.scalars(select(LoginSession)))
        assert sessions and all(s.token_hash!=token for s in sessions)
