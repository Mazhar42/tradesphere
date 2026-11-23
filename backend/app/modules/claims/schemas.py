from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field

from app.shared.money import Money, PositiveMoney
from app.shared.schemas import ReadSchema, WriteSchema

Notes = Annotated[str | None, Field(max_length=1000)]


class ClaimableRead(ReadSchema):
    id: int
    supplier_id: int
    supplier_name: str
    claim_type: str
    source_doc_type: str
    source_doc_id: int
    source_line_id: int | None
    business_date: date
    book_amount: Money
    claim_amount: Money
    status: str


class CompanyClaimCreate(WriteSchema):
    supplier_id: int
    claimable_item_ids: Annotated[list[int], Field(min_length=1, max_length=500)]
    business_date: date | None = None
    period_from: date | None = None
    period_to: date | None = None
    notes: Notes = None


class CompanyClaimSubmit(WriteSchema):
    company_ref: Annotated[str | None, Field(max_length=60)] = None


class SettlementCreate(WriteSchema):
    method: Literal["cash", "bank", "mfs", "supplier_credit"]
    amount: PositiveMoney
    payment_account_id: int | None = None  # required for cash, bank and mfs
    business_date: date | None = None
    reference: Annotated[str | None, Field(max_length=100)] = None
    notes: Notes = None


class RejectionLineIn(WriteSchema):
    claim_line_id: int
    amount: PositiveMoney


class RejectionCreate(WriteSchema):
    lines: Annotated[list[RejectionLineIn], Field(min_length=1, max_length=200)]
    business_date: date | None = None
    notes: Notes = None


class CommissionCreate(WriteSchema):
    customer_id: int
    amount: PositiveMoney
    method: Literal["cash", "credit_to_account"]
    payment_account_id: int | None = None  # required for cash
    is_company_funded: bool = False
    supplier_id: int | None = None  # required when the company funds it
    business_date: date | None = None
    scheme_ref: Annotated[str | None, Field(max_length=100)] = None
    notes: Notes = None


class CommissionRead(ReadSchema):
    id: int
    doc_no: str
    customer_id: int
    customer_name: str
    business_date: date
    amount: Money
    method: str
    payment_account_name: str | None
    is_company_funded: bool
    supplier_id: int | None
    scheme_ref: str | None
    claimable_item_id: int | None


class ClaimSettlementRead(ReadSchema):
    id: int
    doc_no: str
    business_date: date
    method: str
    amount: Money
    payment_account_name: str | None
    reference: str | None
    notes: str | None


class ClaimLineRead(ReadSchema):
    id: int
    claimable_item_id: int
    claim_type: str
    source_doc_type: str
    source_doc_id: int
    business_date: date
    book_amount: Money
    claimed_amount: Money
    settled_amount: Money
    rejected_amount: Money
    outstanding_amount: Money


class CompanyClaimListItem(ReadSchema):
    id: int
    doc_no: str | None
    supplier_id: int
    supplier_name: str
    business_date: date
    status: str
    total_claimed: Money
    total_settled: Money
    total_rejected: Money
    outstanding: Money


class CompanyClaimRead(ReadSchema):
    id: int
    doc_no: str | None
    supplier_id: int
    supplier_name: str
    business_date: date
    period_from: date | None
    period_to: date | None
    status: str
    company_ref: str | None
    notes: str | None
    total_claimed: Money
    total_settled: Money
    total_rejected: Money
    outstanding: Money
    submitted_at: datetime | None
    lines: list[ClaimLineRead]
    settlements: list[ClaimSettlementRead]
