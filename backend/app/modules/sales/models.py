from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.accounting.models import PaymentAccount
from app.modules.catalog.models import Product, Uom
from app.modules.inventory.models import Batch
from app.modules.parties.models import Customer, Supplier
from app.shared.models import Base, CreatedByMixin, IdMixin, TenantScoped, TimestampMixin
from app.shared.money import MONEY, QTY, UNIT_COST

INVOICE_STATUSES = ("draft", "posted", "reversed")
RECEIPT_STATUSES = ("posted", "reversed")


class SalesInvoice(TenantScoped, IdMixin, TimestampMixin, CreatedByMixin, Base):
    """A sale to a customer. Draft until posted; posted invoices are corrected by reversal."""

    __tablename__ = "sales_invoices"

    doc_no: Mapped[str | None] = mapped_column(String(30))  # assigned when posted
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"), index=True)
    business_date: Mapped[date]
    status: Mapped[str] = mapped_column(String(10), default="draft")
    notes: Mapped[str | None] = mapped_column(Text)
    gross_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    dealer_discount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    company_discount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    net_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    paid_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))  # cash taken at sale
    payment_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payment_accounts.id")
    )
    posted_at: Mapped[datetime | None]
    reversed_at: Mapped[datetime | None]

    customer: Mapped[Customer] = relationship(lazy="joined")
    payment_account: Mapped[PaymentAccount | None] = relationship(lazy="joined")
    items: Mapped[list["SalesInvoiceItem"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SalesInvoiceItem.id",
    )

    __table_args__ = (
        Index(
            "uq_sales_invoices_doc_no",
            "tenant_id",
            "doc_no",
            unique=True,
            postgresql_where=text("doc_no IS NOT NULL"),
        ),
        Index("ix_sales_invoices_business_date", "tenant_id", "business_date", "id"),
        CheckConstraint("status IN ('draft','posted','reversed')", name="status_valid"),
        CheckConstraint(
            "gross_amount >= 0 AND dealer_discount >= 0 AND company_discount >= 0 "
            "AND net_amount >= 0 AND paid_amount >= 0",
            name="amounts_non_negative",
        ),
        CheckConstraint("paid_amount <= net_amount", name="paid_within_net"),
        CheckConstraint(
            "paid_amount = 0 OR payment_account_id IS NOT NULL", name="paid_needs_account"
        ),
    )


class SalesInvoiceItem(TenantScoped, IdMixin, Base):
    """One product line. Quantities are base units; prices keep the unit they were entered in."""

    __tablename__ = "sales_invoice_items"

    invoice_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sales_invoices.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"))
    uom_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("uoms.id"))  # entry unit
    factor: Mapped[Decimal] = mapped_column(QTY)  # base units per entry unit
    qty_entry: Mapped[Decimal] = mapped_column(QTY)  # as entered (entry unit)
    free_qty_entry: Mapped[Decimal] = mapped_column(QTY, default=Decimal(0))
    qty_base: Mapped[Decimal] = mapped_column(QTY)
    free_qty_base: Mapped[Decimal] = mapped_column(QTY, default=Decimal(0))
    free_is_company_scheme: Mapped[bool] = mapped_column(default=False)
    unit_price: Mapped[Decimal] = mapped_column(UNIT_COST)  # per entry unit
    base_price: Mapped[Decimal] = mapped_column(UNIT_COST)  # per base unit
    gross_amount: Mapped[Decimal] = mapped_column(MONEY)
    dealer_discount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    company_discount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    company_supplier_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("suppliers.id"))
    line_net: Mapped[Decimal] = mapped_column(MONEY)
    unit_cost: Mapped[Decimal] = mapped_column(
        UNIT_COST, default=Decimal(0)
    )  # per base, at posting
    cogs: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))  # cost of units sold
    free_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))  # cost of free units

    invoice: Mapped[SalesInvoice] = relationship(back_populates="items")
    product: Mapped[Product] = relationship(lazy="joined")
    uom: Mapped[Uom] = relationship(lazy="joined")
    company_supplier: Mapped[Supplier | None] = relationship(lazy="joined")
    batches: Mapped[list["SaleItemBatch"]] = relationship(
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SaleItemBatch.id",
    )

    __table_args__ = (
        CheckConstraint("qty_base > 0 AND free_qty_base >= 0", name="qty_valid"),
        CheckConstraint(
            "unit_price >= 0 AND gross_amount >= 0 AND dealer_discount >= 0 "
            "AND company_discount >= 0 AND line_net >= 0",
            name="amounts_valid",
        ),
        CheckConstraint(
            "dealer_discount + company_discount <= gross_amount", name="discounts_within_gross"
        ),
        CheckConstraint(
            "company_discount = 0 OR company_supplier_id IS NOT NULL",
            name="company_discount_needs_supplier",
        ),
    )


class SaleItemBatch(TenantScoped, IdMixin, Base):
    """FEFO allocation result: which batch a sold or free unit came from."""

    __tablename__ = "sale_item_batches"

    invoice_item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sales_invoice_items.id", ondelete="CASCADE"), index=True
    )
    batch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("batches.id"))
    qty_base: Mapped[Decimal] = mapped_column(QTY)
    is_free: Mapped[bool] = mapped_column(default=False)

    batch: Mapped[Batch | None] = relationship(lazy="joined")

    __table_args__ = (CheckConstraint("qty_base > 0", name="qty_positive"),)


class CustomerReceipt(TenantScoped, IdMixin, TimestampMixin, CreatedByMixin, Base):
    """Money received from a customer, allocated to invoices (the rest is customer credit)."""

    __tablename__ = "customer_receipts"

    doc_no: Mapped[str] = mapped_column(String(30))
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"), index=True)
    business_date: Mapped[date]
    amount: Mapped[Decimal] = mapped_column(MONEY)
    payment_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("payment_accounts.id"))
    reference: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="posted")
    # Set when the receipt was taken as cash at the time of a sale (reversed with that sale).
    sales_invoice_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sales_invoices.id"), index=True
    )
    posted_at: Mapped[datetime | None]
    reversed_at: Mapped[datetime | None]

    customer: Mapped[Customer] = relationship(lazy="joined")
    payment_account: Mapped[PaymentAccount] = relationship(lazy="joined")
    allocations: Mapped[list["ReceiptAllocation"]] = relationship(
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ReceiptAllocation.id",
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "doc_no", name="uq_customer_receipts_doc_no"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("status IN ('posted','reversed')", name="status_valid"),
    )


class ReceiptAllocation(TenantScoped, IdMixin, Base):
    __tablename__ = "receipt_allocations"

    receipt_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("customer_receipts.id", ondelete="CASCADE"), index=True
    )
    invoice_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_invoices.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(MONEY)

    invoice: Mapped[SalesInvoice] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("receipt_id", "invoice_id"),
        CheckConstraint("amount > 0", name="amount_positive"),
    )


class DailySalesSummary(TenantScoped, Base):
    """Projection: sales per business date and product. Updated in the posting transaction."""

    __tablename__ = "daily_sales_summary"

    business_date: Mapped[date] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"), primary_key=True)
    qty_base: Mapped[Decimal] = mapped_column(QTY, default=Decimal(0))
    free_qty_base: Mapped[Decimal] = mapped_column(QTY, default=Decimal(0))
    gross_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    dealer_discount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    company_discount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    net_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    returns_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))  # gross of returns
    cogs: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))


RETURN_STATUSES = ("draft", "posted")
RETURN_CONDITIONS = ("good", "damaged", "expired")
CONDITION_LOCATION = {"good": "SELLABLE", "damaged": "DAMAGED", "expired": "EXPIRED"}


class SalesReturn(TenantScoped, IdMixin, TimestampMixin, CreatedByMixin, Base):
    """Goods a customer brings back against one invoice (refunded in cash or credited)."""

    __tablename__ = "sales_returns"

    doc_no: Mapped[str | None] = mapped_column(String(30))  # assigned when posted
    invoice_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_invoices.id"), index=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    business_date: Mapped[date]
    status: Mapped[str] = mapped_column(String(10), default="draft")
    refund_method: Mapped[str] = mapped_column(String(10))  # cash | credit
    payment_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payment_accounts.id")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    gross_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    dealer_discount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    company_discount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    net_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    cogs: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    posted_at: Mapped[datetime | None]
    reversed_at: Mapped[datetime | None]

    invoice: Mapped[SalesInvoice] = relationship(lazy="joined")
    payment_account: Mapped[PaymentAccount | None] = relationship(lazy="joined")
    items: Mapped[list["SalesReturnItem"]] = relationship(
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SalesReturnItem.id",
    )

    __table_args__ = (
        Index(
            "uq_sales_returns_doc_no",
            "tenant_id",
            "doc_no",
            unique=True,
            postgresql_where=text("doc_no IS NOT NULL"),
        ),
        CheckConstraint("status IN ('draft','posted','reversed')", name="status_valid"),
        CheckConstraint("refund_method IN ('cash','credit')", name="refund_method_valid"),
        CheckConstraint(
            "refund_method = 'credit' OR payment_account_id IS NOT NULL",
            name="cash_refund_needs_account",
        ),
    )


class SalesReturnItem(TenantScoped, IdMixin, Base):
    """One returned quantity: taken from a specific batch sold on a specific invoice line."""

    __tablename__ = "sales_return_items"

    return_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sales_returns.id", ondelete="CASCADE"), index=True
    )
    invoice_item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sales_invoice_items.id"), index=True
    )
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"))
    batch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("batches.id"))
    condition: Mapped[str] = mapped_column(String(10))  # good | damaged | expired
    qty_base: Mapped[Decimal] = mapped_column(QTY)
    unit_cost: Mapped[Decimal] = mapped_column(UNIT_COST, default=Decimal(0))  # original line cost
    gross_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    dealer_discount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    company_discount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    net_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    cogs: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))

    invoice_item: Mapped[SalesInvoiceItem] = relationship(lazy="joined")
    product: Mapped[Product] = relationship(lazy="joined")
    batch: Mapped[Batch | None] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("qty_base > 0", name="qty_positive"),
        CheckConstraint("condition IN ('good','damaged','expired')", name="condition_valid"),
        CheckConstraint(
            "gross_amount >= 0 AND dealer_discount >= 0 AND company_discount >= 0 "
            "AND net_amount >= 0 AND cogs >= 0",
            name="amounts_non_negative",
        ),
    )
