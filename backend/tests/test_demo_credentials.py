"""Credential upgrades must not reset the existing demonstration bank."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app import seed
from app.core.database import Base, configure_sqlite, get_db
from app.core.demo_credentials import DEMO_PASSWORDS, demo_password
from app.core.security import hash_password, password_matches
from app.domain.models import Card, Transaction, TrustedInvitation, TrustedPerson, User
from app.main import app


@pytest.fixture
def demo_store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'demo.db').as_posix()}",
                           connect_args={"check_same_thread": False})
    event.listen(engine, "connect", configure_sqlite)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(seed, "engine", engine)
    monkeypatch.setattr(seed, "SessionLocal", factory)
    monkeypatch.setattr(app.state, "analyst_runtime_root", tmp_path / "analyst", raising=False)

    def db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = db
    seed.seed_data()
    try:
        yield factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _snapshot(factory, *, omit_passwords=False):
    with factory() as session:
        result = {}
        for table in Base.metadata.tables.values():
            columns = [c for c in table.columns
                       if not (omit_passwords and table.name == "users" and c.name == "password_hash")]
            result[table.name] = [tuple(row) for row in session.execute(
                select(*columns).order_by(*table.primary_key.columns))]
        return result


def test_fresh_demo_accounts_use_hashed_name_passwords_and_reject_old_passwords(demo_store):
    with demo_store() as session:
        for code, password in DEMO_PASSWORDS.items():
            account = session.scalar(select(User).where(User.test_iin == code))
            assert account is not None
            assert password_matches(password, account.password_hash)
            salt, digest = account.password_hash.split(":")
            assert len(salt) == 32 and len(digest) == 128
            assert account.password_hash != password
    with TestClient(app) as client:
        for code, password in DEMO_PASSWORDS.items():
            assert client.post("/api/auth/login", json={
                "test_iin": code, "password": password}).status_code == 200
            assert client.post("/api/auth/login", json={
                "test_iin": code, "password": f"Aman-Test-{code[-4:]}!"}).status_code == 401


def test_startup_upgrades_only_old_demo_credentials_and_preserves_bank_state(demo_store):
    with demo_store.begin() as session:
        accounts = {u.test_iin: u for u in session.scalars(select(User))}
        for code in DEMO_PASSWORDS:
            accounts[code].password_hash = hash_password(f"Aman-Test-{code[-4:]}!")
        card = session.scalar(select(Card).where(Card.user_id == accounts["TEST0006"].id))
        card.balance = 73_123_999
        person = session.scalar(select(TrustedPerson).where(TrustedPerson.user_id == card.user_id))
        person.protection_active = True
        person.notifications_enabled = False
        session.add(TrustedInvitation(owner_user_id=card.user_id,
            recipient_user_id=accounts["TEST0003"].id, accepted_by_user_id=accounts["TEST0003"].id,
            trusted_person_name="Марат Омаров", trusted_person_test_iin="TEST0003", relationship="сын",
            status="accepted", active=True, relationship_verified=True))
        session.scalar(select(Transaction).order_by(Transaction.id)).message = "Сохранённая история"
        session.add(User(name="Другой клиент", phone="+77012341234", test_iin="TEST1234",
                         password_hash=hash_password("Aman-Test-1234!")))
    before = _snapshot(demo_store, omit_passwords=True)
    with TestClient(app) as client:
        assert _snapshot(demo_store, omit_passwords=True) == before
        for code, password in DEMO_PASSWORDS.items():
            assert client.post("/api/auth/login", json={
                "test_iin": code, "password": password}).status_code == 200
            assert client.post("/api/auth/login", json={
                "test_iin": code, "password": f"Aman-Test-{code[-4:]}!"}).status_code == 401
        assert client.post("/api/auth/login", json={
            "test_iin": "TEST1234", "password": "Aman-Test-1234!"}).status_code == 200


def test_restart_preserves_custom_passwords_hashes_and_existing_roles(demo_store):
    with demo_store.begin() as session:
        account = session.scalar(select(User).where(User.test_iin == "TEST0003"))
        account.password_hash = hash_password("MyCustomDemoPassword!")
        analyst = session.scalar(select(User).where(User.test_iin == "TEST0099"))
        analyst.role = "client"  # Seeding must not promote an existing account.
    before = _snapshot(demo_store)
    with demo_store() as session:
        before_hashes = {u.test_iin: u.password_hash for u in session.scalars(select(User))}
    with TestClient(app) as client:
        assert _snapshot(demo_store) == before
        assert client.post("/api/auth/login", json={
            "test_iin": "TEST0003", "password": "MyCustomDemoPassword!"}).status_code == 200
        assert client.post("/api/auth/login", json={
            "test_iin": "TEST0003", "password": demo_password("TEST0003")}).status_code == 401
    seed.seed_data()
    with demo_store() as session:
        after_hashes = {u.test_iin: u.password_hash for u in session.scalars(select(User))}
    assert after_hashes == before_hashes


def test_missing_hash_is_initialized_only_for_known_demo_accounts(demo_store):
    with demo_store.begin() as session:
        session.scalar(select(User).where(User.test_iin == "TEST0007")).password_hash = None
        session.scalar(select(User).where(User.test_iin == "TEST0099")).password_hash = None
        session.add(User(name="Без пароля", phone="+77077771234", test_iin="TEST1234"))
    seed.seed_data()
    with demo_store() as session:
        for code in ("TEST0007", "TEST0099"):
            account = session.scalar(select(User).where(User.test_iin == code))
            assert password_matches(demo_password(code), account.password_hash)
        assert session.scalar(select(User).where(User.test_iin == "TEST1234")).password_hash is None
    with pytest.raises(KeyError):
        demo_password("TEST1234")
