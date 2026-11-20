from decimal import Decimal

from sqlalchemy import CheckConstraint, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, CreatedByMixin, IdMixin, TimestampMixin
from app.shared.money import MONEY


class Customer(IdMixin, TimestampMixin, CreatedByMixin, Base):
    """A retailer / shop that buys from the dealer."""

    __tablename__ = "customers"

    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    shop_name: Mapped[str | None] = mapped_column(String(150))
    phone: Mapped[str | None] = mapped_column(String(30))
    address: Mapped[str | None] = mapped_column(Text)
    area: Mapped[str | None] = mapped_column(String(100))  # route / market area
    credit_limit: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))  # 0 = no credit
    payment_terms_days: Mapped[int] = mapped_column(default=0)
    is_walk_in: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_customers_name", func.lower(name)),
        Index("ix_customers_phone", "phone"),
        # Exactly one system walk-in (cash) customer.
        Index(
            "uq_customers_is_walk_in",
            "is_walk_in",
            unique=True,
            postgresql_where=text("is_walk_in"),
        ),
        CheckConstraint("credit_limit >= 0", name="credit_limit_non_negative"),
        CheckConstraint("payment_terms_days >= 0", name="payment_terms_non_negative"),
    )


class Supplier(IdMixin, TimestampMixin, CreatedByMixin, Base):
    """The company (manufacturer / importer) the dealer buys from."""

    __tablename__ = "suppliers"

    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    contact_person: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(150))
    address: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_suppliers_name", func.lower(name)),)
