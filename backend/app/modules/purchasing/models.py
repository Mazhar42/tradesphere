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
from app.modules.inventory.models import Batch, Location
from app.modules.parties.models import Supplier
from app.shared.models import Base, CreatedByMixin, IdMixin, TenantScoped, TimestampMixin
from app.shared.money import MONEY, QTY, UNIT_COST

PO_STATUSES = ("draft", "confirmed", "partially_received", "received", "closed_short", "cancelled")
OPEN_PO_STATUSES = ("confirmed", "partially_received")
PAYABLE_PO_STATUSES = ("confirmed", "partially_received", "received", "closed_short")


class PurchaseOrder(TenantScoped, IdMixin, TimestampMixin, CreatedByMixin, Base):
    __tablename__ = "purchase_orders"

    doc_no: Mapped[str | None] = mapped_column(String(30))  # assigned when confirmed
    supplier_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("suppliers.id"))
    business_date: Mapped[date]
    expected_date: Mapped[date | None]
    status: Mapped[str] = mapped_column(String(20), default="draft")
    notes: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]

    supplier: Mapped[Supplier] = relationship(lazy="joined")
    items: Mapped[list["PurchaseOrderItem"]] = relationship(
        back_populates="po",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PurchaseOrderItem.id",
    )

    __table_args__ = (
        Index(
            "uq_purchase_orders_doc_no",
            "tenant_id",
            "doc_no",
            unique=True,
            postgresql_where=text("doc_no IS NOT NULL"),
        ),
        Index("ix_purchase_orders_supplier_id_status", "tenant_id", "supplier_id", "status"),
        CheckConstraint(
            "status IN ('draft','confirmed','partially_received','received',"
            "'closed_short','cancelled')",
            name="status_valid",
        ),
    )


class PurchaseOrderItem(TenantScoped, IdMixin, Base):
    """One product line. Quantities are base units; prices keep the unit it was entered in."""

    __tablename__ = "purchase_order_items"

    po_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"))
    uom_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("uoms.id"))  # entry unit
    factor: Mapped[Decimal] = mapped_column(QTY)  # base units per entry unit
    ordered_qty_base: Mapped[Decimal] = mapped_column(QTY)
    free_qty_base: Mapped[Decimal] = mapped_column(QTY, default=Decimal(0))
    unit_price: Mapped[Decimal] = mapped_column(UNIT_COST)  # per entry unit
    unit_cost: Mapped[Decimal] = mapped_column(UNIT_COST)  # per base unit
    line_value: Mapped[Decimal] = mapped_column(MONEY)
    received_qty_base: Mapped[Decimal] = mapped_column(QTY, default=Decimal(0))
    received_free_qty_base: Mapped[Decimal] = mapped_column(QTY, default=Decimal(0))
    cancelled_qty_base: Mapped[Decimal] = mapped_column(QTY, default=Decimal(0))
    received_value: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    cancelled_value: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))

    po: Mapped[PurchaseOrder] = relationship(back_populates="items")
    product: Mapped[Product] = relationship(lazy="joined")
    uom: Mapped[Uom] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("ordered_qty_base > 0 AND free_qty_base >= 0", name="qty_valid"),
        CheckConstraint(
            "received_qty_base >= 0 AND cancelled_qty_base >= 0 "
            "AND received_qty_base + cancelled_qty_base <= ordered_qty_base",
            name="received_within_ordered",
        ),
        CheckConstraint(
            "received_value >= 0 AND cancelled_value >= 0 "
            "AND received_value + cancelled_value <= line_value",
            name="value_within_line",
        ),
    )

    @property
    def remaining_qty_base(self) -> Decimal:
        return self.ordered_qty_base - self.received_qty_base - self.cancelled_qty_base

    @property
    def remaining_value(self) -> Decimal:
        return self.line_value - self.received_value - self.cancelled_value


class GoodsReceipt(TenantScoped, IdMixin, TimestampMixin, CreatedByMixin, Base):
    """One shipment received against a purchase order."""

    __tablename__ = "goods_receipts"

    doc_no: Mapped[str | None] = mapped_column(String(30))  # assigned when posted
    po_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("purchase_orders.id"), index=True)
    supplier_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("suppliers.id"))
    business_date: Mapped[date]
    challan_no: Mapped[str | None] = mapped_column(String(50))
    vehicle_no: Mapped[str | None] = mapped_column(String(50))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="draft")
    total_value: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    posted_at: Mapped[datetime | None]
    reversed_at: Mapped[datetime | None]

    po: Mapped[PurchaseOrder] = relationship(lazy="joined")
    supplier: Mapped[Supplier] = relationship(lazy="joined")
    items: Mapped[list["GoodsReceiptItem"]] = relationship(
        back_populates="grn",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="GoodsReceiptItem.id",
    )

    __table_args__ = (
        Index(
            "uq_goods_receipts_doc_no",
            "tenant_id",
            "doc_no",
            unique=True,
            postgresql_where=text("doc_no IS NOT NULL"),
        ),
        CheckConstraint("status IN ('draft','posted','reversed')", name="status_valid"),
    )


class GoodsReceiptItem(TenantScoped, IdMixin, Base):
    __tablename__ = "goods_receipt_items"

    grn_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("goods_receipts.id", ondelete="CASCADE"), index=True
    )
    po_item_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("purchase_order_items.id"))
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"))
    batch_no: Mapped[str | None] = mapped_column(String(60))
    mfg_date: Mapped[date | None]
    expiry_date: Mapped[date | None]
    batch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("batches.id"))
    received_qty_base: Mapped[Decimal] = mapped_column(QTY)
    free_qty_base: Mapped[Decimal] = mapped_column(QTY, default=Decimal(0))
    damaged_qty_base: Mapped[Decimal] = mapped_column(QTY, default=Decimal(0))
    value: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))

    grn: Mapped[GoodsReceipt] = relationship(back_populates="items")
    product: Mapped[Product] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint(
            "received_qty_base >= 0 AND free_qty_base >= 0 AND damaged_qty_base >= 0 "
            "AND damaged_qty_base <= received_qty_base AND received_qty_base + free_qty_base > 0",
            name="qty_valid",
        ),
    )


class SupplierPayment(TenantScoped, IdMixin, TimestampMixin, CreatedByMixin, Base):
    """Money paid to the company. Goods are not implied: it sits as an advance until applied."""

    __tablename__ = "supplier_payments"

    doc_no: Mapped[str] = mapped_column(String(30))
    supplier_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("suppliers.id"))
    business_date: Mapped[date]
    amount: Mapped[Decimal] = mapped_column(MONEY)
    payment_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("payment_accounts.id"))
    reference: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="posted")
    reversed_at: Mapped[datetime | None]

    supplier: Mapped[Supplier] = relationship(lazy="joined")
    payment_account: Mapped[PaymentAccount] = relationship(lazy="joined")
    allocations: Mapped[list["PaymentPoAllocation"]] = relationship(
        back_populates="payment",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PaymentPoAllocation.id",
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "doc_no", name="uq_supplier_payments_doc_no"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("status IN ('posted','reversed')", name="status_valid"),
        Index(
            "ix_supplier_payments_supplier_id_business_date",
            "tenant_id",
            "supplier_id",
            "business_date",
        ),
    )


class PaymentPoAllocation(TenantScoped, IdMixin, Base):
    """Links part of a payment to a purchase order, so each PO shows what was paid for it."""

    __tablename__ = "payment_po_allocations"

    payment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("supplier_payments.id", ondelete="CASCADE"), index=True
    )
    po_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("purchase_orders.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(MONEY)

    payment: Mapped[SupplierPayment] = relationship(back_populates="allocations")
    po: Mapped[PurchaseOrder] = relationship(lazy="joined")

    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)


class PurchaseReturn(TenantScoped, IdMixin, TimestampMixin, CreatedByMixin, Base):
    """Goods sent back to the company. The agreed value is credited to the supplier account or
    claimed from the company, per line."""

    __tablename__ = "purchase_returns"

    doc_no: Mapped[str | None] = mapped_column(String(30))  # assigned when posted
    supplier_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("suppliers.id"), index=True)
    grn_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("goods_receipts.id"))
    business_date: Mapped[date]
    status: Mapped[str] = mapped_column(String(10), default="draft")
    notes: Mapped[str | None] = mapped_column(Text)
    total_value: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))  # agreed credit
    total_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))  # book cost removed
    posted_at: Mapped[datetime | None]

    supplier: Mapped[Supplier] = relationship(lazy="joined")
    items: Mapped[list["PurchaseReturnItem"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="PurchaseReturnItem.id"
    )

    __table_args__ = (
        Index(
            "uq_purchase_returns_doc_no",
            "tenant_id",
            "doc_no",
            unique=True,
            postgresql_where=text("doc_no IS NOT NULL"),
        ),
        CheckConstraint("status IN ('draft','posted')", name="status_valid"),
    )


class PurchaseReturnItem(TenantScoped, IdMixin, Base):
    __tablename__ = "purchase_return_items"

    return_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("purchase_returns.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"))
    batch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("batches.id"))
    location_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("locations.id"))
    qty_base: Mapped[Decimal] = mapped_column(QTY)
    unit_value: Mapped[Decimal] = mapped_column(UNIT_COST)  # agreed credit per base unit
    settlement: Mapped[str] = mapped_column(String(20))  # supplier_credit | claim
    value: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))  # set when posted

    product: Mapped[Product] = relationship(lazy="joined")
    batch: Mapped[Batch | None] = relationship(lazy="joined")
    location: Mapped[Location] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("qty_base > 0 AND unit_value >= 0", name="qty_value_valid"),
        CheckConstraint("settlement IN ('supplier_credit','claim')", name="settlement_valid"),
    )
