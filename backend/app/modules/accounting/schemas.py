from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from app.shared.money import (
    Money,
    NonNegativeUnitCost,
    PositiveMoney,
    PositiveQty,
)
from app.shared.schemas import ReadSchema, WriteSchema

# --- Chart of accounts / payment accounts --------------------------------------------


class AccountRead(ReadSchema):
    id: int
    code: str
    name_en: str
    name_bn: str | None
    type: str
    parent_id: int | None
    is_system: bool
    is_postable: bool
    party_type: str | None
    is_active: bool


class PaymentAccountRead(ReadSchema):
    id: int
    name: str
    kind: str
    gl_account_code: str
    bank_name: str | None
    account_no: str | None
    is_system: bool
    is_active: bool
    balance: Money


class PaymentAccountCreate(WriteSchema):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    kind: Literal["cash", "bank", "mfs"]
    gl_account_code: Literal["1000", "1010", "1020", "1030"] | None = None
    bank_name: Annotated[str | None, Field(max_length=100)] = None
    account_no: Annotated[str | None, Field(max_length=50)] = None


class PaymentAccountUpdate(WriteSchema):
    name: Annotated[str | None, Field(min_length=1, max_length=100)] = None
    bank_name: Annotated[str | None, Field(max_length=100)] = None
    account_no: Annotated[str | None, Field(max_length=50)] = None
    is_active: bool | None = None


# --- Journal / trial balance ----------------------------------------------------------


class JournalLineRead(ReadSchema):
    id: int
    account_code: str
    account_name: str
    debit: Money
    credit: Money
    party_type: str | None
    party_id: int | None
    payment_account_id: int | None
    memo: str | None


class JournalEntryRead(ReadSchema):
    id: int
    entry_no: str
    business_date: date
    source_doc_type: str
    source_doc_id: int | None
    source_doc_no: str | None
    memo: str
    reversal_of_id: int | None
    created_at: datetime
    lines: list[JournalLineRead]


class TrialBalanceRow(ReadSchema):
    account_code: str
    account_name: str
    account_type: str
    debit: Money
    credit: Money


class TrialBalanceRead(ReadSchema):
    as_of: date | None
    rows: list[TrialBalanceRow]
    total_debit: Money
    total_credit: Money
    balanced: bool


class LedgerEntryRead(ReadSchema):
    line_id: int
    business_date: date
    entry_no: str
    memo: str
    source_doc_type: str
    source_doc_id: int | None
    source_doc_no: str | None
    debit: Money
    credit: Money
    balance: Money


# --- Opening balances ---------------------------------------------------------------


class OpeningStockLine(WriteSchema):
    product_id: int
    batch_no: Annotated[str | None, Field(max_length=60)] = None
    mfg_date: date | None = None
    expiry_date: date | None = None
    location: Literal["SELLABLE", "DAMAGED", "EXPIRED"] = "SELLABLE"
    qty_base: PositiveQty
    unit_cost: NonNegativeUnitCost


class OpeningCustomerDue(WriteSchema):
    customer_id: int
    amount: PositiveMoney


class OpeningSupplierBalance(WriteSchema):
    supplier_id: int
    # advance: the company holds our money; payable: we owe the company
    direction: Literal["advance", "payable"]
    amount: PositiveMoney


class OpeningClaim(WriteSchema):
    supplier_id: int
    amount: PositiveMoney


class OpeningCash(WriteSchema):
    payment_account_id: int
    amount: PositiveMoney


class OpeningBalancesCreate(WriteSchema):
    business_date: date
    stock: list[OpeningStockLine] = Field(default_factory=list)
    customer_dues: list[OpeningCustomerDue] = Field(default_factory=list)
    supplier_balances: list[OpeningSupplierBalance] = Field(default_factory=list)
    claims_receivable: list[OpeningClaim] = Field(default_factory=list)
    cash: list[OpeningCash] = Field(default_factory=list)
    notes: Annotated[str | None, Field(max_length=500)] = None

    @model_validator(mode="after")
    def _not_empty(self) -> "OpeningBalancesCreate":
        if not (
            self.stock
            or self.customer_dues
            or self.supplier_balances
            or self.claims_receivable
            or self.cash
        ):
            raise ValueError("Enter at least one opening balance.")
        return self


class OpeningBalanceRead(ReadSchema):
    id: int
    doc_no: str
    business_date: date
    status: str
    summary: dict[str, Any]
    notes: str | None
    created_at: datetime
