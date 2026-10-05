from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from app import seed
from app.core import database
from app.core.database import configure_sqlite, get_db
from app.main import app

@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
                           connect_args={"check_same_thread": False, "timeout": 15})
    event.listen(engine, "connect", configure_sqlite)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(seed, "engine", engine)
    monkeypatch.setattr(seed, "SessionLocal", factory)
    monkeypatch.setattr(database, "engine", engine)
    def db():
        with factory() as session:
            yield session
    app.dependency_overrides[get_db] = db
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json={"test_iin":"TEST0001", "password":"Aman-Test-0001!"}).status_code == 200
        c.test_factory = factory
        yield c
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def low_transfer_risk(monkeypatch):
    """Isolate legacy monetary tests from policy while retaining bank validation.

    Explicitly selected only by pre-integration money test modules. New risk and
    approval tests always exercise the production detector unless opted in.
    """
    from app.domain.risk_schemas import RiskComponent
    from app.services.fraud_detector import FraudDetector
    original = FraudDetector.evaluate_transfer

    def evaluate(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        return result.model_copy(update={"overall_level": "low",
            "recipient_risk": RiskComponent(score=0, level="low", factors=[]),
            "transaction_risk": RiskComponent(score=0, level="low", factors=[])})

    monkeypatch.setattr(FraudDetector, "evaluate_transfer", evaluate)

