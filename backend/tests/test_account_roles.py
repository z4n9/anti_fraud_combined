"""Seeding must not restore permissions or passwords revoked by an operator."""
from sqlalchemy import select
from app.domain.models import Card, User
from app import seed


def test_seed_never_promotes_existing_analyst_fixture_or_resets_password(client):
    with client.test_factory() as db:
        analyst = db.scalar(select(User).where(User.test_iin == "TEST0099"))
        assert analyst.role == "analyst"
        analyst.role = "client"
        analyst.password_hash = "operator-controlled-password"
        db.commit()
        original_cards = list(db.execute(select(Card.id, Card.balance).order_by(Card.id)))
    seed.seed_data()
    with client.test_factory() as db:
        analyst = db.scalar(select(User).where(User.test_iin == "TEST0099"))
        assert analyst.role == "client"
        assert analyst.password_hash == "operator-controlled-password"
        assert list(db.execute(select(Card.id, Card.balance).order_by(Card.id))) == original_cards
        assert db.scalar(select(Card.id).where(Card.user_id == analyst.id)) is None
