from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field

from app.shared.money import Money, NonNegativeMoney, PositiveMoney
from app.shared.schemas import ReadSchema, WriteSchema

Notes = Annotated[str | None, Field(max_length=1000)]


class ExpenseCreate(WriteSchema):
    expense_account_id: int
    amount: PositiveMoney
    payment_account_id: int
    business_date: date | None = None
    payee: Annotated[str | None, Field(max_length=150)] = None
    notes: Notes = None


class ExpenseRead(ReadSchema):
    id: int
    doc_no: str
    business_date: date
    expense_account_code: str
    expense_account_name: str
    amount: Money
    payment_account_name: str
    payee: str | None
    notes: str | None


class FundTransferCreate(WriteSchema):
    from_payment_account_id: int
    to_payment_account_id: int
    amount: PositiveMoney
    charges: NonNegativeMoney = Decimal("0")
    business_date: date | None = None
    reference: Annotated[str | None, Field(max_length=100)] = None
    notes: Notes = None


class FundTransferRead(ReadSchema):
    id: int
    doc_no: str
    business_date: date
    from_account: str
    to_account: str
    amount: Money
    charges: Money
    reference: str | None
    notes: str | None


class OwnerTransactionCreate(WriteSchema):
    kind: Literal["capital", "drawing"]
    amount: PositiveMoney
    payment_account_id: int
    business_date: date | None = None
    notes: Notes = None


class OwnerTransactionRead(ReadSchema):
    id: int
    doc_no: str
    business_date: date
    kind: str
    amount: Money
    payment_account_name: str
    notes: str | None
