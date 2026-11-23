from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.models import Base, IdMixin, TimestampMixin
from app.shared.money import MONEY, QTY, UNIT_COST


class Location(IdMixin, TimestampMixin, Base):
    """Logical stock bucket: SELLABLE, DAMAGED, EXPIRED (extensible to warehouses)."""

    __tablename__ = "locations"

    code: Mapped[str] = mapped_column(String(20), unique=True)
    name_en: Mapped[str] = mapped_column(String(100))
    name_bn: Mapped[str | None] = mapped_column(String(100))
    is_sellable: Mapped[bool] = mapped_column(default=False)
    is_system: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)


class Batch(IdMixin, TimestampMixin, Base):
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


class InventoryMovement(IdMixin, Base):
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


class StockBalance(IdMixin, Base):
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


class ProductCost(Base):
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
