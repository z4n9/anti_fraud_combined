from decimal import Decimal
from typing import Annotated, Literal
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator

class InputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

class UserOut(BaseModel):
    id: int
    name: str
    phone: str
    test_iin: str | None

class CardOut(BaseModel):
    id: int
    card_name: str
    last_four: str
    balance: float
    currency: str
    expiry: str
    status: str

class TransactionOut(BaseModel):
    id: int
    type: Literal["income", "expense", "transfer"]
    title: str
    amount: float
    recipient: str | None
    message: str
    status: Literal["completed", "pending", "failed"]
    created_at: str

class TransferIn(InputModel):
    recipient: Annotated[str, Field(min_length=1, max_length=100)]
    recipient_name: Annotated[str, Field(min_length=1, max_length=100)]
    amount: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]
    message: Annotated[str, Field(max_length=140)] = ""
    anti_scam: "AntiScamIn | None" = None

class TransferOut(BaseModel):
    success: bool = True
    transaction_id: int | None = None
    recipient_name: str
    status: str
    amount: float
    new_balance: float
    request_id: int | None = None
    risk: dict | None = None
    needs_approval: bool = False

class TrustedPersonIn(InputModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    phone: str
    relationship: Annotated[str, Field(min_length=1, max_length=50)]

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value):
        compact = re.sub(r"[\s()\-]", "", value)
        if not re.fullmatch(r"\+7\d{10}", compact):
            raise ValueError("Введите тестовый номер в формате +7 и 10 цифр")
        return compact

class ProtectionSettingsIn(InputModel):
    protection_active: bool
    notifications_enabled: bool
    confirmation_enabled: bool

class TrustedPersonOut(BaseModel):
    id: int
    name: str
    phone: str
    relationship: str
    verified: bool
    protection_active: bool
    notifications_enabled: bool
    confirmation_enabled: bool

    relationship_verified: bool
    invitation_status: Literal["pending", "accepted", "rejected"] | None
    trusted_person_test_iin: str | None
    current_invitation_id: int | None
    family_protection_ready: bool

TestIIN = Annotated[str, Field(pattern=r"^TEST[0-9]{4}$")]

class VerifyRelationshipIn(InputModel):
    client_iin: TestIIN | None = None
    trusted_iin: TestIIN

class MockTrustedOut(BaseModel):
    test_iin: str
    full_name: str
    phone: str

class RelationshipOut(BaseModel):
    verified: bool
    relationship: str | None = None
    trusted_person: MockTrustedOut | None = None
    reason: Literal["citizen_not_found", "relationship_not_found"] | None = None

class InvitationIn(InputModel):
    trusted_iin: TestIIN

class InvitationOut(BaseModel):
    id: int
    owner_user_id: int
    owner_name: str
    trusted_person_name: str
    trusted_person_test_iin: str
    relationship: str
    reverse_relationship: str
    status: Literal["pending", "accepted", "rejected"]
    created_at: str
    updated_at: str

    recipient_user_id: int | None
    accepted_by_user_id: int | None
    active: bool = True
    relationship_verified: bool = False

class LoginIn(InputModel):
    test_iin: TestIIN
    password: Annotated[str, Field(min_length=1, max_length=128)]


class AntiScamIn(InputModel):
    pressure: Annotated[bool, Field(strict=True)] | None = None
    secrecy: Annotated[bool, Field(strict=True)] | None = None
    stranger: Annotated[bool, Field(strict=True)] | None = None


TransferIn.model_rebuild()
