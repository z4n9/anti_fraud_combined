from datetime import date, datetime, timezone
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

def utcnow():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    test_iin: Mapped[str | None] = mapped_column(String(8), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="client", server_default="client")

class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (CheckConstraint("balance >= 0"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    card_name: Mapped[str] = mapped_column(String(100))
    last_four: Mapped[str] = mapped_column(String(4))
    balance: Mapped[int] = mapped_column(Integer)  # tiyn; API uses KZT
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    expiry: Mapped[str] = mapped_column(String(5))
    status: Mapped[str] = mapped_column(String(20), default="active")

class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("type IN ('income', 'expense', 'transfer')"),
        CheckConstraint("status IN ('completed', 'pending', 'failed')"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"))
    type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(100))
    amount: Mapped[int] = mapped_column(Integer)  # signed tiyn
    recipient: Mapped[str | None] = mapped_column(String(100), nullable=True)
    message: Mapped[str] = mapped_column(String(140), default="")
    status: Mapped[str] = mapped_column(String(20), default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    request_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    balance_after: Mapped[int | None] = mapped_column(Integer, nullable=True)

class TrustedPerson(Base):
    __tablename__ = "trusted_people"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(30))
    relationship: Mapped[str] = mapped_column(String(50))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    protection_active: Mapped[bool] = mapped_column(Boolean, default=False)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    confirmation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    relationship_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    invitation_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    trusted_person_test_iin: Mapped[str | None] = mapped_column(String(8), nullable=True)
    current_invitation_id: Mapped[int | None] = mapped_column(ForeignKey("trusted_invitations.id"), nullable=True)

class MockCitizen(Base):
    """Mock eGov's private fixtures, not a bank customer directory."""
    __tablename__ = "mock_citizens"
    id: Mapped[int] = mapped_column(primary_key=True)
    test_iin: Mapped[str] = mapped_column(String(8), unique=True)
    full_name: Mapped[str] = mapped_column(String(100))
    birth_date: Mapped[date] = mapped_column(Date)
    phone: Mapped[str] = mapped_column(String(30))

class MockRelationship(Base):
    __tablename__ = "mock_relationships"
    __table_args__ = (UniqueConstraint("person_1_id", "person_2_id"), CheckConstraint("person_1_id != person_2_id"))
    id: Mapped[int] = mapped_column(primary_key=True)
    person_1_id: Mapped[int] = mapped_column(ForeignKey("mock_citizens.id"))
    person_2_id: Mapped[int] = mapped_column(ForeignKey("mock_citizens.id"))
    relationship_from_1_to_2: Mapped[str] = mapped_column(String(50))
    relationship_from_2_to_1: Mapped[str] = mapped_column(String(50))

class TrustedInvitation(Base):
    __tablename__ = "trusted_invitations"
    __table_args__ = (CheckConstraint("status IN ('pending', 'accepted', 'rejected')"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    trusted_person_name: Mapped[str] = mapped_column(String(100))
    trusted_person_test_iin: Mapped[str] = mapped_column(String(8))
    relationship: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    recipient_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    accepted_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

class LoginSession(Base):
    __tablename__ = "login_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TransferRequest(Base):
    """Frozen transfer command, including blocked and completed decisions."""
    __tablename__ = "transfer_requests"
    __table_args__ = (
        CheckConstraint("amount_cents > 0"),
        CheckConstraint("status IN ('pending_approval','completed','blocked_no_trusted','rejected','cancelled','expired')"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    request_key: Mapped[str] = mapped_column(String(120), unique=True)
    sender_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    recipient_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    sender_card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"))
    recipient_card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"))
    trusted_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    invitation_id: Mapped[int | None] = mapped_column(ForeignKey("trusted_invitations.id"), nullable=True)
    recipient: Mapped[str] = mapped_column(String(100))
    recipient_name: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(String(140), default="")
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), index=True)
    risk_json: Mapped[str] = mapped_column(Text)
    policy_json: Mapped[str] = mapped_column(Text)
    decided_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProtectionChangeRequest(Base):
    """A delayed, account-owned change of an existing protection relationship."""
    __tablename__ = "protection_change_requests"
    __table_args__ = (
        CheckConstraint("action IN ('disable','remove')"),
        CheckConstraint("status IN ('pending','cancelled','executed')"),
        Index("uq_protection_pending_action", "user_id", "action", unique=True,
              sqlite_where=text("status = 'pending'")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    invitation_id: Mapped[int | None] = mapped_column(ForeignKey("trusted_invitations.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
