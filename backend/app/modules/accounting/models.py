from datetime import date, datetime
from decimal import Decimal
from typing import Any

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.models import Base, CreatedByMixin, IdMixin, TenantScoped, TimestampMixin
from app.shared.money import MONEY

ACCOUNT_TYPES = ("asset", "liability", "equity", "income", "expense")


class Account(TenantScoped, IdMixin, TimestampMixin, Base):
    """Chart of accounts."""

    __tablename__ = "accounts"

    code: Mapped[str] = mapped_column(String(10))
    name_en: Mapped[str] = mapped_column(String(120))
    name_bn: Mapped[str | None] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(10))
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("accounts.id"))
    is_system: Mapped[bool] = mapped_column(default=False)
    is_postable: Mapped[bool] = mapped_column(default=True)
    party_type: Mapped[str | None] = mapped_column(String(10))  # customer / supplier sub-ledger
    is_active: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_accounts_code"),
        CheckConstraint(
            "type IN ('asset','liability','equity','income','expense')", name="type_valid"
        ),
        CheckConstraint(
            "party_type IS NULL OR party_type IN ('customer','supplier')", name="party_type_valid"
        ),
    )


class PaymentAccount(TenantScoped, IdMixin, TimestampMixin, Base):
    """A place money is kept: the cash drawer, a bank account, bKash, Nagad..."""

    __tablename__ = "payment_accounts"

    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(10))  # cash | bank | mfs
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"))
    bank_name: Mapped[str | None] = mapped_column(String(100))
    account_no: Mapped[str | None] = mapped_column(String(50))
    is_system: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)

    account: Mapped[Account] = relationship(lazy="joined")

    __table_args__ = (
        Index("uq_payment_accounts_name", "tenant_id", func.lower(name), unique=True),
        CheckConstraint("kind IN ('cash','bank','mfs')", name="kind_valid"),
    )


class JournalEntry(TenantScoped, IdMixin, Base):
    """Append-only header of a balanced double-entry posting."""

    __tablename__ = "journal_entries"

    entry_no: Mapped[str] = mapped_column(String(30))
    business_date: Mapped[date]
    source_doc_type: Mapped[str] = mapped_column(String(20))
    source_doc_id: Mapped[int | None] = mapped_column(BigInteger)
    source_doc_no: Mapped[str | None] = mapped_column(String(30))
    memo: Mapped[str] = mapped_column(String(300))
    reversal_of_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("journal_entries.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))

    lines: Mapped[list["JournalLine"]] = relationship(
        lazy="selectin", order_by="JournalLine.id", back_populates="entry"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "entry_no", name="uq_journal_entries_entry_no"),
        Index("ix_journal_entries_business_date", "tenant_id", "business_date"),
        Index("ix_journal_entries_source", "tenant_id", "source_doc_type", "source_doc_id"),
        Index(
            "uq_journal_entries_reversal_of",
            "reversal_of_id",
            unique=True,
            postgresql_where=text("reversal_of_id IS NOT NULL"),
        ),
    )


class JournalLine(TenantScoped, IdMixin, Base):
    __tablename__ = "journal_lines"

    entry_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("journal_entries.id"), index=True)
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.id"))
    debit: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    credit: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    party_type: Mapped[str | None] = mapped_column(String(10))
    party_id: Mapped[int | None] = mapped_column(BigInteger)
    payment_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payment_accounts.id")
    )
    memo: Mapped[str | None] = mapped_column(String(200))

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint(
            "(debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)", name="one_side_only"
        ),
        Index("ix_journal_lines_party", "tenant_id", "account_id", "party_type", "party_id"),
        Index("ix_journal_lines_payment_account_id", "tenant_id", "payment_account_id"),
    )


class OpeningBalance(TenantScoped, IdMixin, TimestampMixin, CreatedByMixin, Base):
    """The one-time go-live data load (opening stock, dues, advances, claims, cash)."""

    __tablename__ = "opening_balances"

    doc_no: Mapped[str] = mapped_column(String(30))
    business_date: Mapped[date]
    status: Mapped[str] = mapped_column(String(10), default="posted")
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(String(500))
    reversed_at: Mapped[datetime | None]

    __table_args__ = (
        UniqueConstraint("tenant_id", "doc_no", name="uq_opening_balances_doc_no"),
        CheckConstraint("status IN ('posted','reversed')", name="status_valid"),
        # Only one opening-balance load may be active at a time.
        Index(
            "uq_opening_balances_active",
            "tenant_id",
            unique=True,
            postgresql_where=text("status = 'posted'"),
        ),
    )
