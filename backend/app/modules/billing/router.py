"""The dealership's own billing page. Reads on its own connection; the few changes an owner may
make go through the platform connection, for their dealership only."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import Field
from sqlalchemy import func, select

from app.core.config import settings
from app.core.db import DbSession, PlatformDb
from app.core.errors import ConflictError, NotFoundError
from app.core.permissions import CurrentUser, require
from app.core.tenant_context import tenant_id_of
from app.modules.billing import entitlements, service
from app.modules.billing.models import BillingInvoice, PaymentClaim, Plan
from app.modules.tenancy.models import TenantMembership
from app.shared.audit import record_audit
from app.shared.money import Money, PositiveMoney
from app.shared.schemas import ReadSchema, WriteSchema

router = APIRouter(prefix="/billing", tags=["billing"])

Owner = Annotated[CurrentUser, Depends(require("settings:write"))]


class InvoiceRow(ReadSchema):
    id: int
    number: str
    period_start: date
    period_end: date
    amount: Money
    paid: Money
    currency: str
    status: str
    due_on: date


class BillingRead(ReadSchema):
    enforced: bool
    status: str | None  # None: no subscription, nothing is limited
    plan_name: str | None
    price: Money | None
    currency: str | None
    trial_ends_on: date | None
    period_end: date | None
    cancel_at_period_end: bool
    active_users: int
    max_users: int | None
    max_products: int | None
    payment_instructions: str
    invoices: list[InvoiceRow]


class PaymentClaimCreate(WriteSchema):
    method: Literal["bank", "bkash", "nagad", "other"]
    reference: Annotated[str, Field(min_length=3, max_length=100)]
    amount: PositiveMoney


@router.get("", response_model=BillingRead)
def get_billing(db: DbSession, _: Owner) -> BillingRead:
    tenant_id = tenant_id_of(db)
    current = entitlements.subscription(db)
    plan = db.get(Plan, current.plan_id) if current else None
    rows = service.invoices(db, tenant_id)
    users = db.scalar(
        select(func.count())
        .select_from(TenantMembership)
        .where(TenantMembership.tenant_id == tenant_id, TenantMembership.status == "active")
    )
    return BillingRead(
        enforced=settings.billing_enforcement,
        status=current.status if current else None,
        plan_name=plan.name if plan else None,
        price=plan.price if plan else None,
        currency=plan.currency if plan else None,
        trial_ends_on=current.trial_ends_on if current else None,
        period_end=current.period_end if current else None,
        cancel_at_period_end=bool(current and current.cancel_at_period_end),
        active_users=users or 0,
        max_users=entitlements.value(db, "max_users").limit,
        max_products=entitlements.value(db, "max_products").limit,
        payment_instructions=settings.billing_instructions,
        invoices=[
            InvoiceRow(
                id=i.id,
                number=i.number,
                period_start=i.period_start,
                period_end=i.period_end,
                amount=i.amount,
                paid=service.paid_amount(db, i.id),
                currency=i.currency,
                status=i.status,
                due_on=i.due_on,
            )
            for i in rows
        ],
    )


@router.post("/invoices/{billing_invoice_id}/payment-claims", status_code=status.HTTP_201_CREATED)
def submit_payment_claim(
    db: DbSession, actor: Owner, billing_invoice_id: int, data: PaymentClaimCreate
) -> None:
    invoice = db.scalar(
        select(BillingInvoice).where(
            BillingInvoice.id == billing_invoice_id,
            BillingInvoice.tenant_id == tenant_id_of(db),
        )
    )
    if invoice is None:
        raise NotFoundError("Invoice not found.")
    if invoice.status != "open":
        raise ConflictError(f"The invoice is {invoice.status}.", code="invoice_not_open")
    db.add(
        PaymentClaim(
            tenant_id=invoice.tenant_id,
            invoice_id=invoice.id,
            submitted_by_id=actor.id,
            **data.model_dump(),
        )
    )
    record_audit(
        db,
        actor_id=actor.id,
        action="claim_payment",
        entity_type="billing_invoice",
        entity_id=invoice.id,
        after={"method": data.method, "reference": data.reference},
    )
    db.commit()


@router.post("/cancel", status_code=status.HTTP_204_NO_CONTENT)
def cancel(db: PlatformDb, actor: Owner) -> None:
    """At the end of the paid period; can be withdrawn until then."""
    assert actor.tenant_id is not None
    service.set_cancellation(db, actor.tenant_id, True, actor.id)
    db.commit()


@router.post("/resume", status_code=status.HTTP_204_NO_CONTENT)
def resume(db: PlatformDb, actor: Owner) -> None:
    assert actor.tenant_id is not None
    service.set_cancellation(db, actor.tenant_id, False, actor.id)
    db.commit()
