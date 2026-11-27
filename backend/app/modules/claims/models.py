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
from app.modules.parties.models import Customer, Supplier
from app.shared.models import Base, CreatedByMixin, IdMixin, TenantScoped, TimestampMixin
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
COMPANY_CLAIM_STATUSES = ("draft", "submitted", "partially_settled", "settled", "rejected")
SETTLEMENT_METHODS = ("cash", "bank", "mfs", "supplier_credit", "rejection")
PAYMENT_METHODS_WITH_ACCOUNT = ("cash", "bank", "mfs")


class ClaimableItem(TenantScoped, IdMixin, TimestampMixin, CreatedByMixin, Base):
    """Money the company owes the dealer for one event (created automatically by postings).

    Open items are gathered into a company claim. Once a claim is submitted its items are
    `in_claim`; settlement or rejection of their line closes them.
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


class CompanyClaim(TenantScoped, IdMixin, TimestampMixin, CreatedByMixin, Base):
    """A batch of claimable items sent to one company. Settled in cash, as supplier credit,
    or rejected line by line."""

    __tablename__ = "company_claims"

    doc_no: Mapped[str | None] = mapped_column(String(30))  # assigned when submitted
    supplier_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("suppliers.id"), index=True)
    business_date: Mapped[date]
    period_from: Mapped[date | None]
    period_to: Mapped[date | None]
    status: Mapped[str] = mapped_column(String(20), default="draft")
    company_ref: Mapped[str | None] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(Text)
    total_claimed: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    total_settled: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    total_rejected: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    submitted_at: Mapped[datetime | None]

    supplier: Mapped[Supplier] = relationship(lazy="joined")
    lines: Mapped[list["CompanyClaimLine"]] = relationship(
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="CompanyClaimLine.id",
    )

    __table_args__ = (
        Index(
            "uq_company_claims_doc_no",
            "tenant_id",
            "doc_no",
            unique=True,
            postgresql_where=text("doc_no IS NOT NULL"),
        ),
        CheckConstraint(
            "status IN ('draft','submitted','partially_settled','settled','rejected')",
            name="status_valid",
        ),
        CheckConstraint(
            "total_claimed >= 0 AND total_settled >= 0 AND total_rejected >= 0",
            name="totals_non_negative",
        ),
    )


class CompanyClaimLine(TenantScoped, IdMixin, Base):
    """One claimable item inside a claim, with what the company settled or rejected of it."""

    __tablename__ = "company_claim_lines"

    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("company_claims.id", ondelete="CASCADE"), index=True
    )
    claimable_item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claimable_items.id"), index=True
    )
    book_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    claimed_amount: Mapped[Decimal] = mapped_column(MONEY)
    settled_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    rejected_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    # How much of book_amount has been cleared from the claims receivable so far.
    book_cleared: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))

    claimable_item: Mapped[ClaimableItem] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint(
            "claimed_amount >= 0 AND settled_amount >= 0 AND rejected_amount >= 0",
            name="amounts_non_negative",
        ),
        CheckConstraint(
            "settled_amount + rejected_amount <= claimed_amount", name="resolved_within_claimed"
        ),
        CheckConstraint(
            "book_cleared >= 0 AND book_cleared <= book_amount", name="book_cleared_within_book"
        ),
    )


class ClaimSettlement(TenantScoped, IdMixin, TimestampMixin, CreatedByMixin, Base):
    """One money receipt, supplier-credit offset, or rejection against a claim."""

    __tablename__ = "claim_settlements"

    doc_no: Mapped[str] = mapped_column(String(30))
    claim_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("company_claims.id"), index=True)
    business_date: Mapped[date]
    method: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    payment_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payment_accounts.id")
    )
    reference: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)

    payment_account: Mapped[PaymentAccount | None] = relationship(lazy="joined")
    lines: Mapped[list["ClaimSettlementLine"]] = relationship(
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ClaimSettlementLine.id",
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "doc_no", name="uq_claim_settlements_doc_no"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "method IN ('cash','bank','mfs','supplier_credit','rejection')",
            name="method_valid",
        ),
        CheckConstraint(
            "method NOT IN ('cash','bank','mfs') OR payment_account_id IS NOT NULL",
            name="money_needs_account",
        ),
    )


class ClaimSettlementLine(TenantScoped, IdMixin, Base):
    """How one settlement was split across the claim's lines (the audit trail of the split)."""

    __tablename__ = "claim_settlement_lines"

    settlement_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claim_settlements.id", ondelete="CASCADE"), index=True
    )
    claim_line_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("company_claim_lines.id"), index=True
    )
    settled_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    rejected_amount: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    to_book: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))  # cleared from receivable
    to_income: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))  # settled above book
    write_off: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))  # book left unclaimed

    __table_args__ = (
        CheckConstraint(
            "settled_amount >= 0 AND rejected_amount >= 0 AND to_book >= 0 "
            "AND to_income >= 0 AND write_off >= 0",
            name="amounts_non_negative",
        ),
    )


class CommissionPayment(TenantScoped, IdMixin, TimestampMixin, CreatedByMixin, Base):
    """Commission or trade promotion paid to a customer. Company-funded ones become claims."""

    __tablename__ = "commission_payments"

    doc_no: Mapped[str] = mapped_column(String(30))
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"), index=True)
    business_date: Mapped[date]
    amount: Mapped[Decimal] = mapped_column(MONEY)
    method: Mapped[str] = mapped_column(String(20))  # cash | credit_to_account
    payment_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payment_accounts.id")
    )
    is_company_funded: Mapped[bool] = mapped_column(default=False)
    supplier_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("suppliers.id"))
    scheme_ref: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    claimable_item_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("claimable_items.id")
    )

    customer: Mapped[Customer] = relationship(lazy="joined")
    payment_account: Mapped[PaymentAccount | None] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("tenant_id", "doc_no", name="uq_commission_payments_doc_no"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("method IN ('cash','credit_to_account')", name="method_valid"),
        CheckConstraint(
            "method = 'credit_to_account' OR payment_account_id IS NOT NULL",
            name="cash_needs_account",
        ),
        CheckConstraint(
            "NOT is_company_funded OR supplier_id IS NOT NULL", name="company_needs_supplier"
        ),
    )
