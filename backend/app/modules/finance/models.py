from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.accounting.models import Account, PaymentAccount
from app.shared.models import Base, CreatedByMixin, IdMixin, TimestampMixin
from app.shared.money import MONEY


class Expense(IdMixin, TimestampMixin, CreatedByMixin, Base):
    """Money spent on running the business, paid from a payment account (posted at once)."""

    __tablename__ = "expenses"

    doc_no: Mapped[str] = mapped_column(String(30), unique=True)
    business_date: Mapped[date]
    expense_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    payment_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("payment_accounts.id"))
    payee: Mapped[str | None] = mapped_column(String(150))
    notes: Mapped[str | None] = mapped_column(Text)

    expense_account: Mapped[Account] = relationship(
        foreign_keys=[expense_account_id], lazy="joined"
    )
    payment_account: Mapped[PaymentAccount] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_expenses_business_date", "business_date"),
    )


class FundTransfer(IdMixin, TimestampMixin, CreatedByMixin, Base):
    """Moves money between payment accounts (cash to bank, wallet to cash).

    Charges are an expense.
    """

    __tablename__ = "fund_transfers"

    doc_no: Mapped[str] = mapped_column(String(30), unique=True)
    business_date: Mapped[date]
    from_payment_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("payment_accounts.id")
    )
    to_payment_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("payment_accounts.id")
    )
    amount: Mapped[Decimal] = mapped_column(MONEY)
    charges: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    reference: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)

    from_account: Mapped[PaymentAccount] = relationship(
        foreign_keys=[from_payment_account_id], lazy="joined"
    )
    to_account: Mapped[PaymentAccount] = relationship(
        foreign_keys=[to_payment_account_id], lazy="joined"
    )

    __table_args__ = (
        CheckConstraint("amount > 0 AND charges >= 0", name="amounts_valid"),
        CheckConstraint("from_payment_account_id <> to_payment_account_id", name="accounts_differ"),
    )


OWNER_KINDS = ("capital", "drawing")


class OwnerTransaction(IdMixin, TimestampMixin, CreatedByMixin, Base):
    """Owner puts money in (capital) or takes it out (drawing)."""

    __tablename__ = "owner_transactions"

    doc_no: Mapped[str] = mapped_column(String(30), unique=True)
    business_date: Mapped[date]
    kind: Mapped[str] = mapped_column(String(10))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    payment_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("payment_accounts.id"))
    notes: Mapped[str | None] = mapped_column(Text)

    payment_account: Mapped[PaymentAccount] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("kind IN ('capital','drawing')", name="kind_valid"),
    )
