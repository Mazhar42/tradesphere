"""Platform billing. Nothing here is ever posted into a dealership's own books.

Not `TenantScoped`: the platform reads across dealerships, and row-level security limits the
application to its own rows (which it may only read, apart from payment claims).
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.shared.models import Base, IdMixin

MONEY = Numeric(18, 2)


class Plan(IdMixin, Base):
    """A published price. Never edited: a change is a new version."""

    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(30))
    version: Mapped[int]
    name: Mapped[str] = mapped_column(String(100))
    interval: Mapped[str] = mapped_column(String(5))
    price: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    trial_days: Mapped[int]
    is_public: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_plans_code_version"),
        CheckConstraint("\"interval\" IN ('month','year')", name="interval_valid"),
        CheckConstraint("price >= 0 AND trial_days >= 0", name="not_negative"),
    )


class PlanEntitlement(Base):
    __tablename__ = "plan_entitlements"

    plan_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("plans.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    limit_value: Mapped[int | None] = mapped_column(BigInteger)
    enabled: Mapped[bool | None]


class _OfTenant:
    @declared_attr
    def tenant_id(cls) -> Mapped[int]:
        return mapped_column(BigInteger, ForeignKey("tenants.id"))

    @declared_attr.directive
    def __table_args__(cls) -> tuple[object, ...]:
        name = cls.__tablename__  # type: ignore[attr-defined]
        return (Index(f"ix_{name}_tenant_id", "tenant_id"), *getattr(cls, "extra_args", ()))


class Subscription(_OfTenant, IdMixin, Base):
    __tablename__ = "subscriptions"

    plan_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("plans.id"))
    status: Mapped[str] = mapped_column(String(12))
    trial_ends_on: Mapped[date | None]
    period_start: Mapped[date | None]
    period_end: Mapped[date | None]
    read_only_since: Mapped[date | None]
    cancel_at_period_end: Mapped[bool] = mapped_column(server_default="false")
    pending_plan_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("plans.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    extra_args = (
        UniqueConstraint("tenant_id", name="uq_subscriptions_tenant_id"),
        CheckConstraint(
            "status IN ('trialing','active','past_due','read_only','suspended')",
            name="status_valid",
        ),
    )


class SubscriptionEvent(_OfTenant, IdMixin, Base):
    __tablename__ = "subscription_events"

    from_status: Mapped[str | None] = mapped_column(String(12))
    to_status: Mapped[str] = mapped_column(String(12))
    reason: Mapped[str] = mapped_column(String(200))
    actor_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())


class EntitlementOverride(_OfTenant, IdMixin, Base):
    __tablename__ = "tenant_entitlement_overrides"

    key: Mapped[str] = mapped_column(String(50))
    limit_value: Mapped[int | None] = mapped_column(BigInteger)
    enabled: Mapped[bool | None]
    reason: Mapped[str] = mapped_column(String(200))
    expires_on: Mapped[date | None]
    created_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class BillingInvoice(_OfTenant, IdMixin, Base):
    __tablename__ = "billing_invoices"

    number: Mapped[str] = mapped_column(String(30))
    plan_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("plans.id"))
    period_start: Mapped[date]
    period_end: Mapped[date]
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(5), server_default="open")
    issued_on: Mapped[date]
    due_on: Mapped[date]
    description: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    extra_args = (
        UniqueConstraint("number", name="uq_billing_invoices_number"),
        CheckConstraint("status IN ('open','paid','void')", name="status_valid"),
        CheckConstraint("amount >= 0", name="amount_not_negative"),
    )


class BillingPayment(_OfTenant, IdMixin, Base):
    """Money received. Corrections are a second row with the negated amount."""

    __tablename__ = "billing_payments"

    invoice_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("billing_invoices.id"))
    method: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(20))
    provider_ref: Mapped[str] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    received_on: Mapped[date]
    recorded_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    reverses_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("billing_payments.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    extra_args = (
        UniqueConstraint("provider", "provider_ref", name="uq_billing_payments_provider_ref"),
        UniqueConstraint("reverses_id", name="uq_billing_payments_reverses_id"),
    )


class PaymentClaim(_OfTenant, IdMixin, Base):
    """A dealership saying it has paid; an operator checks the statement."""

    __tablename__ = "billing_payment_claims"

    invoice_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("billing_invoices.id"))
    method: Mapped[str] = mapped_column(String(20))
    reference: Mapped[str] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    submitted_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
