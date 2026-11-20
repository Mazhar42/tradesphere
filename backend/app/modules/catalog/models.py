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

from app.shared.models import Base, CreatedByMixin, IdMixin, TimestampMixin
from app.shared.money import MONEY, QTY


class Category(IdMixin, TimestampMixin, Base):
    __tablename__ = "categories"

    name_en: Mapped[str] = mapped_column(String(100))
    name_bn: Mapped[str | None] = mapped_column(String(100))
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("categories.id"))
    is_active: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (Index("uq_categories_name_en", func.lower(name_en), unique=True),)


class Brand(IdMixin, TimestampMixin, Base):
    __tablename__ = "brands"

    name_en: Mapped[str] = mapped_column(String(100))
    name_bn: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (Index("uq_brands_name_en", func.lower(name_en), unique=True),)


class Uom(IdMixin, TimestampMixin, Base):
    """Unit of measure, e.g. PCS, CTN, BOX, KG."""

    __tablename__ = "uoms"

    code: Mapped[str] = mapped_column(String(10), unique=True)
    name_en: Mapped[str] = mapped_column(String(50))
    name_bn: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(default=True)


class Product(IdMixin, TimestampMixin, CreatedByMixin, Base):
    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String(50), unique=True)
    barcode: Mapped[str | None] = mapped_column(String(64))
    name_en: Mapped[str] = mapped_column(String(200))
    name_bn: Mapped[str | None] = mapped_column(String(200))
    category_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("categories.id"))
    brand_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("brands.id"))
    base_uom_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("uoms.id"))
    # Prices are per *base* unit.
    dp: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))  # dealer price (purchase)
    tp: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))  # trade price (sale)
    mrp: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))  # max retail price
    reorder_level: Mapped[Decimal] = mapped_column(QTY, default=Decimal("0"))  # base units
    track_batch: Mapped[bool] = mapped_column(default=True)
    track_expiry: Mapped[bool] = mapped_column(default=True)
    is_active: Mapped[bool] = mapped_column(default=True)

    category: Mapped[Category | None] = relationship(lazy="joined")
    brand: Mapped[Brand | None] = relationship(lazy="joined")
    base_uom: Mapped[Uom] = relationship(lazy="joined")
    uoms: Mapped[list["ProductUom"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ProductUom.factor_to_base.desc()",
    )

    __table_args__ = (
        Index(
            "uq_products_barcode",
            "barcode",
            unique=True,
            postgresql_where=text("barcode IS NOT NULL"),
        ),
        Index("ix_products_name_en", func.lower(name_en)),
        CheckConstraint("dp >= 0 AND tp >= 0 AND mrp >= 0", name="prices_non_negative"),
        CheckConstraint("mrp = 0 OR tp <= mrp", name="tp_not_above_mrp"),
        CheckConstraint("reorder_level >= 0", name="reorder_level_non_negative"),
    )


class ProductUom(IdMixin, Base):
    """Alternative unit for a product, e.g. 1 CTN = 24 base units."""

    __tablename__ = "product_uoms"

    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    uom_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("uoms.id"))
    factor_to_base: Mapped[Decimal] = mapped_column(QTY)
    is_purchase_default: Mapped[bool] = mapped_column(default=False)
    is_sales_default: Mapped[bool] = mapped_column(default=False)
    barcode: Mapped[str | None] = mapped_column(String(64))

    product: Mapped[Product] = relationship(back_populates="uoms")
    uom: Mapped[Uom] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("product_id", "uom_id"),
        CheckConstraint("factor_to_base > 1", name="factor_gt_one"),
    )
