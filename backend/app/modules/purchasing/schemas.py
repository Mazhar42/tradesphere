from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import Field, model_validator

from app.shared.money import (
    Money,
    NonNegativeMoney,
    NonNegativeQty,
    NonNegativeUnitCost,
    PositiveMoney,
    PositiveQty,
    Qty,
    UnitCost,
)
from app.shared.schemas import ReadSchema, WriteSchema

Notes = Annotated[str | None, Field(max_length=1000)]

# --- Purchase orders -------------------------------------------------------------------------


class POLineIn(WriteSchema):
    product_id: int
    uom_id: int | None = None  # entry unit (e.g. carton); None = the product's base unit
    qty: PositiveQty  # in the entry unit
    free_qty: NonNegativeQty = Decimal(0)  # free goods expected from the company (entry unit)
    unit_price: NonNegativeUnitCost  # price per entry unit


class POCreate(WriteSchema):
    supplier_id: int
    business_date: date | None = None
    expected_date: date | None = None
    notes: Notes = None
    items: Annotated[list[POLineIn], Field(min_length=1, max_length=200)]


class POUpdate(WriteSchema):
    supplier_id: int
    business_date: date | None = None
    expected_date: date | None = None
    notes: Notes = None
    items: Annotated[list[POLineIn], Field(min_length=1, max_length=200)]


class POItemRead(ReadSchema):
    id: int
    product_id: int
    sku: str
    name_en: str
    name_bn: str | None
    base_uom_code: str
    track_batch: bool
    track_expiry: bool
    uom_id: int
    uom_code: str
    factor: Qty
    ordered_qty_base: Qty
    free_qty_base: Qty
    unit_price: UnitCost
    unit_cost: UnitCost
    line_value: Money
    received_qty_base: Qty
    received_free_qty_base: Qty
    cancelled_qty_base: Qty
    remaining_qty_base: Qty
    received_value: Money
    cancelled_value: Money
    remaining_value: Money


class POSummary(ReadSchema):
    ordered_value: Money
    received_value: Money
    cancelled_value: Money
    remaining_value: Money  # goods still to be delivered
    paid_value: Money  # payments allocated to this PO
    balance_due: Money  # received but not yet paid
    unapplied_advance: Money  # paid but goods not yet delivered


class POListItem(ReadSchema):
    id: int
    doc_no: str | None
    supplier_id: int
    supplier_name: str
    business_date: date
    expected_date: date | None
    status: str
    item_count: int
    summary: POSummary


class ReceiptRef(ReadSchema):
    id: int
    doc_no: str | None
    business_date: date
    status: str
    total_value: Money
    challan_no: str | None


class PaymentRef(ReadSchema):
    payment_id: int
    doc_no: str
    business_date: date
    status: str
    amount: Money  # amount allocated to this PO


class PODetail(ReadSchema):
    id: int
    doc_no: str | None
    supplier_id: int
    supplier_name: str
    business_date: date
    expected_date: date | None
    status: str
    notes: str | None
    created_at: datetime
    confirmed_at: datetime | None
    closed_at: datetime | None
    items: list[POItemRead]
    summary: POSummary
    receipts: list[ReceiptRef]
    payments: list[PaymentRef]


# --- Goods receipts -------------------------------------------------------------------------


class GrnLineIn(WriteSchema):
    po_item_id: int
    received_qty_base: NonNegativeQty = Decimal(0)
    free_qty_base: NonNegativeQty = Decimal(0)
    damaged_qty_base: NonNegativeQty = Decimal(0)  # part of received that arrived damaged
    batch_no: Annotated[str | None, Field(max_length=60)] = None
    mfg_date: date | None = None
    expiry_date: date | None = None

    @model_validator(mode="after")
    def _check(self) -> "GrnLineIn":
        if self.received_qty_base + self.free_qty_base <= 0:
            raise ValueError("Enter a received or free quantity.")
        if self.damaged_qty_base > self.received_qty_base:
            raise ValueError("Damaged quantity cannot exceed the received quantity.")
        return self


class GrnCreate(WriteSchema):
    po_id: int
    business_date: date | None = None
    challan_no: Annotated[str | None, Field(max_length=50)] = None
    vehicle_no: Annotated[str | None, Field(max_length=50)] = None
    notes: Notes = None
    items: Annotated[list[GrnLineIn], Field(min_length=1, max_length=200)]


class GrnItemRead(ReadSchema):
    id: int
    po_item_id: int
    product_id: int
    sku: str
    name_en: str
    name_bn: str | None
    batch_no: str | None
    mfg_date: date | None
    expiry_date: date | None
    received_qty_base: Qty
    free_qty_base: Qty
    damaged_qty_base: Qty
    value: Money


class GrnRead(ReadSchema):
    id: int
    doc_no: str | None
    po_id: int
    po_doc_no: str | None
    supplier_id: int
    supplier_name: str
    business_date: date
    challan_no: str | None
    vehicle_no: str | None
    notes: str | None
    status: str
    total_value: Money
    posted_at: datetime | None
    created_at: datetime
    items: list[GrnItemRead]


class GrnListItem(ReadSchema):
    id: int
    doc_no: str | None
    po_id: int
    po_doc_no: str | None
    supplier_id: int
    supplier_name: str
    business_date: date
    challan_no: str | None
    status: str
    total_value: Money
    item_count: int


# --- Supplier payments ----------------------------------------------------------------------


class AllocationIn(WriteSchema):
    po_id: int
    amount: PositiveMoney


class SupplierPaymentCreate(WriteSchema):
    supplier_id: int
    business_date: date | None = None
    amount: PositiveMoney
    payment_account_id: int
    reference: Annotated[str | None, Field(max_length=100)] = None
    notes: Notes = None
    allocations: Annotated[list[AllocationIn], Field(max_length=100)] = Field(default_factory=list)


class AllocateRequest(WriteSchema):
    allocations: Annotated[list[AllocationIn], Field(min_length=1, max_length=100)]


class AllocationRead(ReadSchema):
    id: int
    po_id: int
    po_doc_no: str | None
    amount: Money


class SupplierPaymentRead(ReadSchema):
    id: int
    doc_no: str
    supplier_id: int
    supplier_name: str
    business_date: date
    amount: Money
    payment_account_id: int
    payment_account_name: str
    reference: str | None
    notes: str | None
    status: str
    allocated_amount: NonNegativeMoney
    unallocated_amount: NonNegativeMoney
    allocations: list[AllocationRead]
    created_at: datetime


# --- Supplier account -----------------------------------------------------------------------


class SupplierSummary(ReadSchema):
    supplier_id: int
    ledger_balance: Money  # debit - credit on the supplier account: >0 advance, <0 we owe
    position: str  # advance | payable | settled
    advance: NonNegativeMoney
    payable: NonNegativeMoney
    outstanding_po_value: NonNegativeMoney  # goods ordered but not yet delivered
    open_po_count: int
