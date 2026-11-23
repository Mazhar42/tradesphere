from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field

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

# --- Invoices --------------------------------------------------------------------------------


class InvoiceLineIn(WriteSchema):
    product_id: int
    uom_id: int | None = None  # entry unit (e.g. carton); None = the product's base unit
    qty: PositiveQty  # in the entry unit
    free_qty: NonNegativeQty = Decimal(0)  # free goods given with the line (entry unit)
    # Price per entry unit. Omitted = the product's trade price (TP). Any other price needs the
    # sales:price_override permission.
    unit_price: NonNegativeUnitCost | None = None
    dealer_discount: NonNegativeMoney = Decimal(0)  # funded by the dealer
    company_discount: NonNegativeMoney = Decimal(0)  # reimbursed by the company
    company_supplier_id: int | None = None  # the company that funds company_discount / free goods
    free_is_company_scheme: bool = False  # free goods reimbursed by the company


class InvoiceWrite(WriteSchema):
    customer_id: int
    business_date: date | None = None  # default: today (Asia/Dhaka)
    notes: Notes = None
    paid_amount: NonNegativeMoney = Decimal(0)  # cash collected at the counter
    payment_account_id: int | None = None  # required when paid_amount > 0
    items: Annotated[list[InvoiceLineIn], Field(min_length=1, max_length=200)]


class BatchAllocRead(ReadSchema):
    batch_id: int | None
    batch_no: str | None
    expiry_date: date | None
    qty_base: Qty
    is_free: bool


class InvoiceItemRead(ReadSchema):
    id: int
    product_id: int
    sku: str
    name_en: str
    name_bn: str | None
    base_uom_code: str
    uom_id: int
    uom_code: str
    factor: Qty
    qty_entry: Qty
    free_qty_entry: Qty
    qty_base: Qty
    free_qty_base: Qty
    free_is_company_scheme: bool
    unit_price: UnitCost
    base_price: UnitCost
    gross_amount: Money
    dealer_discount: Money
    company_discount: Money
    company_supplier_id: int | None
    company_supplier_name: str | None
    line_net: Money
    unit_cost: UnitCost
    cogs: Money
    free_cost: Money
    batches: list[BatchAllocRead]


class InvoiceListItem(ReadSchema):
    id: int
    doc_no: str | None
    customer_id: int
    customer_code: str
    customer_name: str
    business_date: date
    status: str
    net_amount: Money
    received_amount: Money
    due_amount: Money
    payment_status: str  # paid | partial | unpaid | draft | reversed


class InvoiceRead(ReadSchema):
    id: int
    doc_no: str | None
    customer_id: int
    customer_code: str
    customer_name: str
    customer_shop_name: str | None
    business_date: date
    status: str
    notes: str | None
    gross_amount: Money
    dealer_discount: Money
    company_discount: Money
    net_amount: Money
    paid_amount: Money
    payment_account_id: int | None
    payment_account_name: str | None
    received_amount: Money
    due_amount: Money
    returned_amount: Money  # net value of posted sales returns against this invoice
    payment_status: str
    posted_at: datetime | None
    reversed_at: datetime | None
    items: list[InvoiceItemRead]
    receipts: list["ReceiptListItem"]


# --- Receipts --------------------------------------------------------------------------------


class PaymentOption(ReadSchema):
    """An account money can be received into. No balances: cashiers do not see them."""

    id: int
    name: str
    kind: str


class AllocationIn(WriteSchema):
    invoice_id: int
    amount: PositiveMoney


class ReceiptCreate(WriteSchema):
    customer_id: int
    amount: PositiveMoney
    payment_account_id: int
    business_date: date | None = None
    reference: Annotated[str | None, Field(max_length=100)] = None
    notes: Notes = None
    # Explicit allocation. When empty, the amount is allocated automatically to the oldest open
    # invoices first (FIFO); whatever is left stays as customer credit.
    allocations: Annotated[list[AllocationIn], Field(max_length=200, default_factory=list)]


class ReceiptAllocationRead(ReadSchema):
    invoice_id: int
    invoice_doc_no: str | None
    amount: Money


class ReceiptListItem(ReadSchema):
    id: int
    doc_no: str
    customer_id: int
    customer_name: str
    business_date: date
    amount: Money
    payment_account_name: str
    reference: str | None
    status: str
    sales_invoice_id: int | None
    allocated: Money


class ReceiptRead(ReadSchema):
    id: int
    doc_no: str
    customer_id: int
    customer_name: str
    business_date: date
    amount: Money
    payment_account_id: int
    payment_account_name: str
    reference: str | None
    notes: str | None
    status: str
    sales_invoice_id: int | None
    allocated: Money
    unallocated: Money
    allocations: list[ReceiptAllocationRead]
    posted_at: datetime | None
    reversed_at: datetime | None


# --- Customers and reports -------------------------------------------------------------------


class CustomerSummary(ReadSchema):
    customer_id: int
    code: str
    name: str
    credit_limit: Money
    balance: Money  # what the customer owes (negative = credit held with the dealer)
    available_credit: Money
    open_invoices: int


class AgingRow(ReadSchema):
    customer_id: int
    code: str
    name: str
    current: Money  # 0-30 days since the invoice date
    days_31_60: Money
    days_61_90: Money
    days_over_90: Money
    invoiced_due: Money
    on_account: Money  # balance not tied to an invoice (opening dues, unallocated receipts)
    total: Money


class AgingReport(ReadSchema):
    as_of: date
    rows: list[AgingRow]
    totals: AgingRow


class SalesSummaryRow(ReadSchema):
    period: str
    qty_base: Qty
    gross_amount: Money
    returns_amount: Money
    dealer_discount: Money
    company_discount: Money
    net_amount: Money
    # Cost and margin are null for users without reports:profit.
    cogs: Money | None = None
    # (gross - dealer discounts) - cogs; company-funded discounts excluded
    gross_profit: Money | None = None


InvoiceRead.model_rebuild()


# --- Sales returns ---------------------------------------------------------------------------


class ReturnLineIn(WriteSchema):
    invoice_item_id: int  # the invoice line the goods were sold on
    batch_id: int | None = None  # the batch they were sold from (required for tracked products)
    condition: Literal["good", "damaged", "expired"] = "good"
    qty_base: PositiveQty


class SalesReturnCreate(WriteSchema):
    invoice_id: int
    business_date: date | None = None
    refund_method: Literal["cash", "credit"]
    payment_account_id: int | None = None  # required for a cash refund
    notes: Notes = None
    items: Annotated[list[ReturnLineIn], Field(min_length=1, max_length=200)]


class ReturnableRow(ReadSchema):
    """What can still be returned from one batch of one invoice line."""

    invoice_item_id: int
    product_id: int
    sku: str
    name_en: str
    name_bn: str | None
    base_uom_code: str
    batch_id: int | None
    batch_no: str | None
    expiry_date: date | None
    sold_qty_base: Qty
    returned_qty_base: Qty
    returnable_qty_base: Qty
    unit_cost: UnitCost
    base_price: UnitCost


class ReturnItemRead(ReadSchema):
    id: int
    invoice_item_id: int
    product_id: int
    sku: str
    name_en: str
    name_bn: str | None
    base_uom_code: str
    batch_id: int | None
    batch_no: str | None
    condition: str
    qty_base: Qty
    unit_cost: UnitCost
    gross_amount: Money
    dealer_discount: Money
    company_discount: Money
    net_amount: Money
    cogs: Money


class SalesReturnListItem(ReadSchema):
    id: int
    doc_no: str | None
    invoice_id: int
    invoice_doc_no: str | None
    customer_id: int
    customer_name: str
    business_date: date
    status: str
    refund_method: str
    net_amount: Money


class SalesReturnRead(ReadSchema):
    id: int
    doc_no: str | None
    invoice_id: int
    invoice_doc_no: str | None
    customer_id: int
    customer_name: str
    business_date: date
    status: str
    refund_method: str
    payment_account_id: int | None
    payment_account_name: str | None
    notes: str | None
    gross_amount: Money
    dealer_discount: Money
    company_discount: Money
    net_amount: Money
    cogs: Money
    posted_at: datetime | None
    items: list[ReturnItemRead]
