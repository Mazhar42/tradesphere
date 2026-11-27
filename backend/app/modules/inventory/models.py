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
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.catalog.models import Product
from app.shared.models import Base, CreatedByMixin, IdMixin, TenantScoped, TimestampMixin
from app.shared.money import MONEY, QTY, UNIT_COST


class Location(TenantScoped, IdMixin, TimestampMixin, Base):
    """Logical stock bucket: SELLABLE, DAMAGED, EXPIRED (extensible to warehouses)."""

    __tablename__ = "locations"

    code: Mapped[str] = mapped_column(String(20), unique=True)
    name_en: Mapped[str] = mapped_column(String(100))
    name_bn: Mapped[str | None] = mapped_column(String(100))
    is_sellable: Mapped[bool] = mapped_column(default=False)
    is_system: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)


class Batch(TenantScoped, IdMixin, TimestampMixin, Base):
    """Manufacturer lot of a product, with optional manufacturing and expiry dates."""

    __tablename__ = "batches"

    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"))
    batch_no: Mapped[str] = mapped_column(String(60))
    mfg_date: Mapped[date | None]
    expiry_date: Mapped[date | None]

    __table_args__ = (
        UniqueConstraint("product_id", "batch_no"),
        Index("ix_batches_expiry_date", "expiry_date"),
    )


class InventoryMovement(TenantScoped, IdMixin, Base):
    """Append-only stock ledger. Every stock change is one signed row here.

    `qty_base` is in base units (positive = in, negative = out); `value` carries the
    same sign. The table is the source of truth for stock; `stock_balances` and
    `product_costs` are projections kept in sync inside the same transaction.
    """

    __tablename__ = "inventory_movements"

    business_date: Mapped[date]
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"))
    batch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("batches.id"))
    location_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("locations.id"))
    qty_base: Mapped[Decimal] = mapped_column(QTY)
    unit_cost: Mapped[Decimal] = mapped_column(UNIT_COST)
    value: Mapped[Decimal] = mapped_column(MONEY)
    movement_type: Mapped[str] = mapped_column(String(20))
    source_doc_type: Mapped[str] = mapped_column(String(20))
    source_doc_id: Mapped[int] = mapped_column(BigInteger)
    source_doc_no: Mapped[str | None] = mapped_column(String(30))
    source_line_id: Mapped[int | None] = mapped_column(BigInteger)
    reversal_of_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("inventory_movements.id")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))

    batch: Mapped[Batch | None] = relationship(lazy="joined")
    location: Mapped[Location] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_inventory_movements_product_id_id", "product_id", "id"),
        Index("ix_inventory_movements_source", "source_doc_type", "source_doc_id"),
        Index("ix_inventory_movements_business_date", "business_date"),
        Index(
            "uq_inventory_movements_reversal_of",
            "reversal_of_id",
            unique=True,
            postgresql_where=text("reversal_of_id IS NOT NULL"),
        ),
        CheckConstraint(
            "(qty_base > 0 AND value >= 0) OR (qty_base < 0 AND value <= 0)", name="sign_consistent"
        ),
    )


class StockBalance(TenantScoped, IdMixin, Base):
    """Current quantity per product / batch / location (projection of the movements)."""

    __tablename__ = "stock_balances"

    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"))
    batch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("batches.id"))
    location_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("locations.id"))
    qty_base: Mapped[Decimal] = mapped_column(QTY, default=Decimal(0))

    batch: Mapped[Batch | None] = relationship(lazy="joined")
    location: Mapped[Location] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "batch_id",
            "location_id",
            name="uq_stock_balances_key",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("qty_base >= 0", name="qty_non_negative"),
        Index("ix_stock_balances_product_id", "product_id"),
    )


class ProductCost(TenantScoped, Base):
    """Moving weighted-average cost per product (across all locations)."""

    __tablename__ = "product_costs"

    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"), primary_key=True)
    qty_on_hand: Mapped[Decimal] = mapped_column(QTY, server_default="0")
    total_value: Mapped[Decimal] = mapped_column(MONEY, server_default="0")
    avg_cost: Mapped[Decimal] = mapped_column(UNIT_COST, server_default="0")

    __table_args__ = (
        CheckConstraint("qty_on_hand >= 0", name="qty_non_negative"),
        CheckConstraint("total_value >= 0", name="value_non_negative"),
    )


ADJUSTMENT_REASONS = ("count", "damage", "expiry", "found", "other")


class StockAdjustment(TenantScoped, IdMixin, TimestampMixin, CreatedByMixin, Base):
    """Corrects stock outside purchasing and sales. Damage and expiry reasons are write-offs."""

    __tablename__ = "stock_adjustments"

    doc_no: Mapped[str | None] = mapped_column(String(30))  # assigned when posted
    business_date: Mapped[date]
    reason: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), default="draft")
    notes: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None]

    items: Mapped[list["StockAdjustmentItem"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="StockAdjustmentItem.id"
    )

    __table_args__ = (
        Index(
            "uq_stock_adjustments_doc_no",
            "doc_no",
            unique=True,
            postgresql_where=text("doc_no IS NOT NULL"),
        ),
        CheckConstraint(
            "reason IN ('count','damage','expiry','found','other')", name="reason_valid"
        ),
        CheckConstraint("status IN ('draft','posted')", name="status_valid"),
    )


class StockAdjustmentItem(TenantScoped, IdMixin, Base):
    __tablename__ = "stock_adjustment_items"

    adjustment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("stock_adjustments.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"))
    batch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("batches.id"))
    location_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("locations.id"))
    qty_delta: Mapped[Decimal] = mapped_column(QTY)  # signed base units
    value: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))  # signed, set when posted

    product: Mapped["Product"] = relationship(lazy="joined")
    batch: Mapped[Batch | None] = relationship(lazy="joined")
    location: Mapped[Location] = relationship(lazy="joined")

    __table_args__ = (CheckConstraint("qty_delta <> 0", name="qty_non_zero"),)


class StockTransfer(TenantScoped, IdMixin, TimestampMixin, CreatedByMixin, Base):
    """Moves stock between locations (for example SELLABLE to EXPIRED). No journal entry."""

    __tablename__ = "stock_transfers"

    doc_no: Mapped[str | None] = mapped_column(String(30))
    business_date: Mapped[date]
    from_location_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("locations.id"))
    to_location_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("locations.id"))
    status: Mapped[str] = mapped_column(String(10), default="draft")
    notes: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None]

    from_location: Mapped[Location] = relationship(foreign_keys=[from_location_id], lazy="joined")
    to_location: Mapped[Location] = relationship(foreign_keys=[to_location_id], lazy="joined")
    items: Mapped[list["StockTransferItem"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="StockTransferItem.id"
    )

    __table_args__ = (
        Index(
            "uq_stock_transfers_doc_no",
            "doc_no",
            unique=True,
            postgresql_where=text("doc_no IS NOT NULL"),
        ),
        CheckConstraint("from_location_id <> to_location_id", name="locations_differ"),
        CheckConstraint("status IN ('draft','posted')", name="status_valid"),
    )


class StockTransferItem(TenantScoped, IdMixin, Base):
    __tablename__ = "stock_transfer_items"

    transfer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("stock_transfers.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"))
    batch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("batches.id"))
    qty_base: Mapped[Decimal] = mapped_column(QTY)
    value: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))  # set when posted

    product: Mapped["Product"] = relationship(lazy="joined")
    batch: Mapped[Batch | None] = relationship(lazy="joined")

    __table_args__ = (CheckConstraint("qty_base > 0", name="qty_positive"),)
