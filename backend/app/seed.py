"""Incremental fixtures; existing balances, transactions and consent are preserved."""
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import select
from app.core.database import Base, SessionLocal, engine
from app.migrations import migrate
from app.core.security import hash_password
from app.domain.models import Card, MockCitizen, MockRelationship, Transaction, TrustedPerson, TrustedInvitation, User

CITIZENS = [
    ("TEST0001", "Алихан Омаров", "2005-01-01", "+77000000001"),
    ("TEST0002", "Айдана Омарова", "2000-05-12", "+77010000043"),
    ("TEST0003", "Марат Омаров", "1974-02-10", "+77020000044"),
    ("TEST0004", "Гульмира Омарова", "1977-09-15", "+77030000055"),
    ("TEST0006", "Амина Омарова", "1950-03-08", "+77050000077"),
    ("TEST0007", "Сауле Омарова", "1980-06-17", "+77060000088"),
    ("TEST0005", "Данияр Иманов", "1999-08-20", "+77040000066"),
]

def seed_data():
    # create_all adds missing tables; migrate adds columns to pre-existing tables.
    Base.metadata.create_all(engine)
    migrate(engine)
    with SessionLocal.begin() as session:
        user = session.get(User, 1)
        if user is None:
            user = User(id=1, name="Алихан", phone="+77000000025", test_iin="TEST0001")
            session.add(user)
            session.flush()
            session.add(Card(id=1, user_id=1, card_name="AMAN Gold", last_four="4582",
                             balance=285000000, currency="KZT", expiry="09/29", status="active"))
            session.flush()
            session.add(TrustedPerson(user_id=1, name="", phone="", relationship="", verified=False,
                                     protection_active=False, notifications_enabled=True, confirmation_enabled=True))
            now = datetime.now(timezone.utc)
            examples = [("expense", "Magnum", -24500, None, 1),
                        ("transfer", "Айдана О.", -50000, "+77010000043", 2),
                        ("income", "Зарплата", 450000, None, 24),
                        ("expense", "Магазин", -8900, None, 26)]
            for kind, title, amount, recipient, hours in examples:
                session.add(Transaction(user_id=1, card_id=1, type=kind, title=title,
                                        amount=amount * 100, recipient=recipient, status="completed",
                                        created_at=now - timedelta(hours=hours)))
        elif user.test_iin is None:
            user.test_iin = "TEST0001"
        citizens = {}
        for test_iin, name, birthday, phone in CITIZENS:
            citizen = session.scalar(select(MockCitizen).where(MockCitizen.test_iin == test_iin))
            if citizen is None:
                citizen = MockCitizen(test_iin=test_iin, full_name=name, birth_date=date.fromisoformat(birthday), phone=phone)
                session.add(citizen)
                session.flush()
            citizens[test_iin] = citizen
        owner_id = citizens["TEST0001"].id
        for trusted_iin, forward, backward in [("TEST0002", "сестра", "брат"), ("TEST0003", "отец", "сын"), ("TEST0004", "мать", "сын")]:
            trusted_id = citizens[trusted_iin].id
            exists = session.scalar(select(MockRelationship).where(MockRelationship.person_1_id == owner_id,
                                                                   MockRelationship.person_2_id == trusted_id))
            if exists is None:
                session.add(MockRelationship(person_1_id=owner_id, person_2_id=trusted_id,
                                             relationship_from_1_to_2=forward, relationship_from_2_to_1=backward))

        grandma_id = citizens["TEST0006"].id
        for code, relationship in [("TEST0003", "сын"), ("TEST0007", "дочь")]:
            child_id = citizens[code].id
            exists = session.scalar(select(MockRelationship).where(MockRelationship.person_1_id == grandma_id, MockRelationship.person_2_id == child_id))
            if exists is None:
                session.add(MockRelationship(person_1_id=grandma_id, person_2_id=child_id,
                                             relationship_from_1_to_2=relationship, relationship_from_2_to_1="мать"))
        accounts = {}
        for code, citizen in citizens.items():
            account = session.scalar(select(User).where(User.test_iin == code))
            if account is None:
                account = User(name=citizen.full_name, phone=citizen.phone, test_iin=code)
                session.add(account)
                session.flush()
                session.add(Card(user_id=account.id, card_name="AMAN Gold", last_four=f"{account.id:04d}", balance=120000000 if code=="TEST0006" else 0, currency="KZT", expiry="09/29", status="active"))
                session.add(TrustedPerson(user_id=account.id, name="", phone="", relationship="", verified=False, protection_active=False))
            if not account.password_hash:
                # Public local fixture credentials, never a real user's password.
                account.password_hash = hash_password(f"Aman-Test-{code[-4:]}!")
            accounts[code] = account
        for invitation in session.scalars(select(TrustedInvitation).where(TrustedInvitation.recipient_user_id.is_(None))):
            recipient = accounts.get(invitation.trusted_person_test_iin)
            if recipient:
                invitation.recipient_user_id = recipient.id

if __name__ == "__main__":
    seed_data()
    print("Миграция и тестовые данные готовы. Баланс и история сохранены.")
