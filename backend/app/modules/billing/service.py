"""Plans, the subscription lifecycle and manual payments (ADR-009).

Runs on the platform connection. Every function takes the dealership explicitly.
Agreed timings: 14-day trial (per plan), 7-day grace, 90 days read-only, then suspension.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.modules.billing.models import (
    BillingInvoice,
    BillingPayment,
    EntitlementOverride,
    PaymentClaim,
    Plan,
    PlanEntitlement,
    Subscription,
    SubscriptionEvent,
)
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.service import business_config
from app.shared.dates import business_date

GRACE_DAYS = 7
READ_ONLY_DAYS = 90
RENEWAL_NOTICE_DAYS = 7
MANUAL = "manual"
ZERO = Decimal(0)


def today_for(session: Session, tenant_id: int) -> date:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError("Dealership not found.")
    return business_date(config=business_config(tenant))


def add_months(start: date, months: int) -> date:
    month = start.month - 1 + months
    year, month = start.year + month // 12, month % 12 + 1
    first_next = date(year + month // 12, month % 12 + 1, 1)
    last_day = (first_next - timedelta(days=1)).day
    return date(year, month, min(start.day, last_day))


def period_end(start: date, interval: str) -> date:
    """The last day covered."""
    return add_months(start, 1 if interval == "month" else 12) - timedelta(days=1)


# --- Plans ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Grant:
    key: str
    limit: int | None = None
    enabled: bool | None = None


def publish_plan(
    session: Session,
    *,
    code: str,
    name: str,
    interval: str,
    price: Decimal,
    currency: str,
    trial_days: int,
    is_public: bool,
    grants: Sequence[Grant],
) -> Plan:
    """A new code, or a new version of one. Issued invoices keep their own amounts."""
    latest = session.scalar(select(func.max(Plan.version)).where(Plan.code == code)) or 0
    plan = Plan(
        code=code,
        version=latest + 1,
        name=name,
        interval=interval,
        price=price,
        currency=currency,
        trial_days=trial_days,
        is_public=is_public,
    )
    session.add(plan)
    session.flush()
    session.add_all(
        PlanEntitlement(plan_id=plan.id, key=g.key, limit_value=g.limit, enabled=g.enabled)
        for g in grants
    )
    session.flush()
    return plan


# --- Subscriptions ---------------------------------------------------------------------------


def subscription_of(session: Session, tenant_id: int, *, lock: bool = False) -> Subscription | None:
    statement = select(Subscription).where(Subscription.tenant_id == tenant_id)
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement.execution_options(populate_existing=True))


def _move(
    session: Session,
    subscription: Subscription,
    status: str,
    reason: str,
    actor_id: int | None,
    today: date,
) -> None:
    if subscription.status == status:
        return
    session.add(
        SubscriptionEvent(
            tenant_id=subscription.tenant_id,
            from_status=subscription.status,
            to_status=status,
            reason=reason,
            actor_id=actor_id,
        )
    )
    if status == "read_only":
        subscription.read_only_since = today
    elif status in ("active", "trialing"):
        subscription.read_only_since = None
    subscription.status = status


def start_trial(session: Session, tenant_id: int, plan_id: int, actor_id: int) -> Subscription:
    if subscription_of(session, tenant_id) is not None:
        raise ConflictError("This dealership already has a subscription.", code="already_exists")
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise NotFoundError("Plan not found.")
    today = today_for(session, tenant_id)
    subscription = Subscription(
        tenant_id=tenant_id,
        plan_id=plan.id,
        status="trialing",
        trial_ends_on=today + timedelta(days=plan.trial_days),
    )
    session.add(subscription)
    session.add(
        SubscriptionEvent(
            tenant_id=tenant_id, to_status="trialing", reason="Trial started", actor_id=actor_id
        )
    )
    session.flush()
    return subscription


def set_cancellation(session: Session, tenant_id: int, cancel: bool, actor_id: int) -> None:
    subscription = subscription_of(session, tenant_id, lock=True)
    if subscription is None or subscription.status not in ("trialing", "active", "past_due"):
        raise ConflictError("There is no running subscription to change.", code="not_active")
    subscription.cancel_at_period_end = cancel
    session.add(
        SubscriptionEvent(
            tenant_id=tenant_id,
            from_status=subscription.status,
            to_status=subscription.status,
            reason="Cancellation requested" if cancel else "Cancellation withdrawn",
            actor_id=actor_id,
        )
    )


def change_plan(session: Session, tenant_id: int, plan_id: int, actor_id: int) -> None:
    """Takes effect with the next period (a trial changes at once). Prorated upgrades within a
    paid period are not built."""
    subscription = subscription_of(session, tenant_id, lock=True)
    plan = session.get(Plan, plan_id)
    if subscription is None or plan is None:
        raise NotFoundError("Subscription or plan not found.")
    if subscription.status == "trialing":
        subscription.plan_id = plan.id
        subscription.pending_plan_id = None
    else:
        subscription.pending_plan_id = plan.id
    session.add(
        SubscriptionEvent(
            tenant_id=tenant_id,
            from_status=subscription.status,
            to_status=subscription.status,
            reason=f"Plan change to {plan.code} v{plan.version}",
            actor_id=actor_id,
        )
    )


# --- Invoices and payments ---------------------------------------------------------------------


def _open_invoice(session: Session, tenant_id: int, start: date) -> BillingInvoice | None:
    return session.scalar(
        select(BillingInvoice).where(
            BillingInvoice.tenant_id == tenant_id,
            BillingInvoice.period_start == start,
            BillingInvoice.status != "void",
        )
    )


def issue_invoice(session: Session, tenant_id: int, start: date | None = None) -> BillingInvoice:
    """For the next period: after the paid one, or from today for a trial or lapsed account (no
    back-charging of lapsed time)."""
    subscription = subscription_of(session, tenant_id, lock=True)
    if subscription is None:
        raise NotFoundError("This dealership has no subscription.")
    today = today_for(session, tenant_id)
    if start is None:
        start = today
        if subscription.status in ("active", "past_due") and subscription.period_end:
            start = subscription.period_end + timedelta(days=1)
    existing = _open_invoice(session, tenant_id, start)
    if existing is not None:
        return existing
    plan = session.get(Plan, subscription.pending_plan_id or subscription.plan_id)
    assert plan is not None
    number = session.scalar(text("SELECT nextval('billing_invoice_number_seq')"))
    invoice = BillingInvoice(
        tenant_id=tenant_id,
        number=f"TS-{today.year}-{number:06d}",
        plan_id=plan.id,
        period_start=start,
        period_end=period_end(start, plan.interval),
        amount=plan.price,
        currency=plan.currency,
        status="open",
        issued_on=today,
        due_on=max(today, start),
        description=f"{plan.name}, {plan.interval}ly subscription",
    )
    session.add(invoice)
    session.flush()
    return invoice


def paid_amount(session: Session, invoice_id: int) -> Decimal:
    return Decimal(
        session.scalar(
            select(func.coalesce(func.sum(BillingPayment.amount), 0)).where(
                BillingPayment.invoice_id == invoice_id
            )
        )
        or 0
    )


def _invoice(session: Session, invoice_id: int) -> BillingInvoice:
    invoice = session.get(BillingInvoice, invoice_id, with_for_update=True)
    if invoice is None:
        raise NotFoundError("Invoice not found.")
    return invoice


def _activate(session: Session, invoice: BillingInvoice, actor_id: int | None, today: date) -> None:
    subscription = subscription_of(session, invoice.tenant_id, lock=True)
    assert subscription is not None
    subscription.period_start = invoice.period_start
    subscription.period_end = invoice.period_end
    subscription.plan_id = invoice.plan_id
    if subscription.pending_plan_id == invoice.plan_id:
        subscription.pending_plan_id = None
    _move(session, subscription, "active", f"Invoice {invoice.number} paid", actor_id, today)


def record_payment(
    session: Session,
    invoice_id: int,
    *,
    amount: Decimal,
    method: str,
    reference: str,
    received_on: date,
    actor_id: int,
    provider: str = MANUAL,
) -> BillingPayment:
    """Partial payments keep the invoice open. A reference counts once per provider."""
    invoice = _invoice(session, invoice_id)
    if invoice.status != "open":
        raise ConflictError(f"The invoice is {invoice.status}.", code="invoice_not_open")
    if session.scalar(
        select(BillingPayment.id).where(
            BillingPayment.provider == provider, BillingPayment.provider_ref == reference
        )
    ):
        raise ConflictError("This payment reference is already recorded.", code="duplicate")
    outstanding = invoice.amount - paid_amount(session, invoice.id)
    if amount <= 0 or amount > outstanding:
        raise BusinessRuleError(
            f"Enter an amount up to {outstanding}.",
            errors=[{"field": "amount", "message": "Out of range"}],
        )
    payment = BillingPayment(
        tenant_id=invoice.tenant_id,
        invoice_id=invoice.id,
        method=method,
        provider=provider,
        provider_ref=reference,
        amount=amount,
        received_on=received_on,
        recorded_by_id=actor_id,
    )
    session.add(payment)
    session.flush()
    if amount == outstanding:
        invoice.status = "paid"
        _activate(session, invoice, actor_id, today_for(session, invoice.tenant_id))
    return payment


def reverse_payment(session: Session, payment_id: int, reason: str, actor_id: int) -> None:
    """A mistake is cancelled by an opposite record; the invoice reopens. If it had activated
    the period, the account returns to read-only until paid."""
    payment = session.get(BillingPayment, payment_id)
    if payment is None or payment.reverses_id is not None:
        raise NotFoundError("Payment not found.")
    invoice = _invoice(session, payment.invoice_id)
    if session.scalar(select(BillingPayment.id).where(BillingPayment.reverses_id == payment.id)):
        raise ConflictError("This payment is already reversed.", code="already_reversed")
    session.add(
        BillingPayment(
            tenant_id=payment.tenant_id,
            invoice_id=payment.invoice_id,
            method=payment.method,
            provider=payment.provider,
            provider_ref=f"reversal-of-{payment.id}",
            amount=-payment.amount,
            received_on=today_for(session, payment.tenant_id),
            recorded_by_id=actor_id,
            reverses_id=payment.id,
        )
    )
    session.flush()
    if invoice.status == "paid":
        invoice.status = "open"
        subscription = subscription_of(session, invoice.tenant_id, lock=True)
        assert subscription is not None
        if subscription.period_start == invoice.period_start:
            _move(
                session,
                subscription,
                "read_only",
                f"Payment reversed: {reason}"[:200],
                actor_id,
                today_for(session, invoice.tenant_id),
            )


def void_invoice(session: Session, invoice_id: int) -> None:
    """Invoices are never edited: void and issue again."""
    invoice = _invoice(session, invoice_id)
    if invoice.status != "open" or paid_amount(session, invoice.id) != ZERO:
        raise ConflictError("Only an unpaid open invoice can be voided.", code="invoice_not_open")
    invoice.status = "void"


def set_override(
    session: Session,
    tenant_id: int,
    *,
    key: str,
    limit: int | None,
    enabled: bool | None,
    reason: str,
    expires_on: date | None,
    actor_id: int,
) -> None:
    session.add(
        EntitlementOverride(
            tenant_id=tenant_id,
            key=key,
            limit_value=limit,
            enabled=enabled,
            reason=reason,
            expires_on=expires_on,
            created_by_id=actor_id,
        )
    )


def invoices(session: Session, tenant_id: int | None = None) -> list[BillingInvoice]:
    statement = select(BillingInvoice).order_by(BillingInvoice.id.desc()).limit(200)
    if tenant_id is not None:
        statement = statement.where(BillingInvoice.tenant_id == tenant_id)
    return list(session.scalars(statement))


def claims(session: Session, invoice_ids: Sequence[int]) -> list[PaymentClaim]:
    return list(
        session.scalars(
            select(PaymentClaim)
            .where(PaymentClaim.invoice_id.in_(invoice_ids))
            .order_by(PaymentClaim.id)
        )
    )


# --- Time ------------------------------------------------------------------------------------


def advance(session: Session, tenant_id: int, today: date | None = None) -> str | None:
    """Apply whatever the passage of time requires. Safe to run any number of times."""
    subscription = subscription_of(session, tenant_id, lock=True)
    if subscription is None:
        return None
    today = today or today_for(session, tenant_id)

    if subscription.status == "trialing" and subscription.trial_ends_on:
        if today > subscription.trial_ends_on:
            _move(session, subscription, "read_only", "Trial ended unpaid", None, today)
    elif subscription.status == "active" and subscription.period_end:
        if today > subscription.period_end:
            if subscription.cancel_at_period_end:
                _move(session, subscription, "read_only", "Cancelled", None, today)
            else:
                _move(session, subscription, "past_due", "Renewal unpaid", None, today)
        elif (
            not subscription.cancel_at_period_end
            and (subscription.period_end - today).days < RENEWAL_NOTICE_DAYS
        ):
            issue_invoice(session, tenant_id, subscription.period_end + timedelta(days=1))
    # Not `elif`: a job that did not run for a while catches up in one pass.
    if (
        subscription.status == "past_due"
        and subscription.period_end
        and today > subscription.period_end + timedelta(days=GRACE_DAYS)
    ):
        _move(session, subscription, "read_only", "Grace period ended", None, today)
    if (
        subscription.status == "read_only"
        and subscription.read_only_since
        and (today - subscription.read_only_since).days > READ_ONLY_DAYS
    ):
        _move(session, subscription, "suspended", "Read-only period ended", None, today)
    return subscription.status
