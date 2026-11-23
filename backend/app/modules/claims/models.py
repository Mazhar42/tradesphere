from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, CreatedByMixin, IdMixin, TimestampMixin
from app.shared.money import MONEY

CLAIM_TYPES = (
    "cash_commission",
    "trade_discount",
    "free_goods",
    "damage",
    "expiry",
    "price_drop",
    "other",
)
CLAIMABLE_STATUSES = ("open", "in_claim", "settled", "rejected", "cancelled")


class ClaimableItem(IdMixin, TimestampMixin, CreatedByMixin, Base):
    """Money the company owes the dealer for one event (created automatically by postings).

    Items are grouped into company claims in a later phase. Until then they are only created
    and, when their source document is reversed, cancelled.
    """

    __tablename__ = "claimable_items"

    supplier_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("suppliers.id"), index=True)
    claim_type: Mapped[str] = mapped_column(String(20))
    source_doc_type: Mapped[str] = mapped_column(String(30))
    source_doc_id: Mapped[int] = mapped_column(BigInteger)
    source_line_id: Mapped[int | None] = mapped_column(BigInteger)
    business_date: Mapped[date]
    book_amount: Mapped[Decimal] = mapped_column(MONEY)  # what the dealer's books carry
    claim_amount: Mapped[Decimal] = mapped_column(MONEY)  # what the dealer asks the company for
    status: Mapped[str] = mapped_column(String(20), default="open")

    __table_args__ = (
        Index("ix_claimable_items_source", "source_doc_type", "source_doc_id"),
        Index("ix_claimable_items_supplier_status", "supplier_id", "status"),
        CheckConstraint(
            "claim_type IN ('cash_commission','trade_discount','free_goods','damage',"
            "'expiry','price_drop','other')",
            name="claim_type_valid",
        ),
        CheckConstraint(
            "status IN ('open','in_claim','settled','rejected','cancelled')",
            name="status_valid",
        ),
        CheckConstraint("book_amount >= 0 AND claim_amount >= 0", name="amounts_non_negative"),
    )
