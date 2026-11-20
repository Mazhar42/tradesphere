from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import EmailStr, Field, StringConstraints

from app.shared.money import NonNegativeMoney
from app.shared.schemas import ReadSchema, WriteSchema

Code = Annotated[
    str, StringConstraints(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_\-]+$", to_upper=True)
]
Name = Annotated[str, Field(min_length=1, max_length=150)]
Phone = Annotated[str | None, Field(max_length=30)]


class CustomerRead(ReadSchema):
    id: int
    code: str
    name: str
    shop_name: str | None
    phone: str | None
    address: str | None
    area: str | None
    credit_limit: NonNegativeMoney
    payment_terms_days: int
    is_walk_in: bool
    is_active: bool
    notes: str | None
    created_at: datetime


class CustomerCreate(WriteSchema):
    code: Code | None = None  # auto-generated (CUS-00001) when omitted
    name: Name
    shop_name: Annotated[str | None, Field(max_length=150)] = None
    phone: Phone = None
    address: Annotated[str | None, Field(max_length=500)] = None
    area: Annotated[str | None, Field(max_length=100)] = None
    credit_limit: NonNegativeMoney = Decimal("0")
    payment_terms_days: Annotated[int, Field(ge=0, le=365)] = 0
    notes: Annotated[str | None, Field(max_length=1000)] = None


class CustomerUpdate(WriteSchema):
    name: Name | None = None
    shop_name: Annotated[str | None, Field(max_length=150)] = None
    phone: Phone = None
    address: Annotated[str | None, Field(max_length=500)] = None
    area: Annotated[str | None, Field(max_length=100)] = None
    credit_limit: NonNegativeMoney | None = None
    payment_terms_days: Annotated[int | None, Field(ge=0, le=365)] = None
    is_active: bool | None = None
    notes: Annotated[str | None, Field(max_length=1000)] = None


class SupplierRead(ReadSchema):
    id: int
    code: str
    name: str
    contact_person: str | None
    phone: str | None
    email: str | None
    address: str | None
    is_active: bool
    notes: str | None
    created_at: datetime


class SupplierCreate(WriteSchema):
    code: Code | None = None  # auto-generated (SUP-001) when omitted
    name: Name
    contact_person: Annotated[str | None, Field(max_length=120)] = None
    phone: Phone = None
    email: EmailStr | None = None
    address: Annotated[str | None, Field(max_length=500)] = None
    notes: Annotated[str | None, Field(max_length=1000)] = None


class SupplierUpdate(WriteSchema):
    name: Name | None = None
    contact_person: Annotated[str | None, Field(max_length=120)] = None
    phone: Phone = None
    email: EmailStr | None = None
    address: Annotated[str | None, Field(max_length=500)] = None
    is_active: bool | None = None
    notes: Annotated[str | None, Field(max_length=1000)] = None
