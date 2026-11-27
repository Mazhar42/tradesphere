"""Operators publish plans, manage subscriptions and record money received."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import Field
from sqlalchemy import select

from app.core.db import PlatformDb
from app.core.errors import ForbiddenError, NotFoundError
from app.modules.billing import service as billing
from app.modules.billing.entitlements import FEATURES, LIMITS
from app.modules.billing.models import BillingPayment, Plan, Subscription
from app.modules.platform import service
from app.modules.platform.models import PlatformOperator
from app.modules.platform.router import Operator
from app.shared.money import Money, PositiveMoney
from app.shared.schemas import ReadSchema, WriteSchema

router = APIRouter(prefix="/platform", tags=["platform"])


def billing_operator(db: PlatformDb, operator: Operator) -> int:
    row = db.get(PlatformOperator, operator)
    if row is None or row.role not in ("admin", "billing"):
        raise ForbiddenError("Billing changes need a billing operator.", code="permission_denied")
    return operator


BillingOperator = Annotated[int, Depends(billing_operator)]
Amount = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
Reason = Annotated[str, Field(min_length=5, max_length=200)]


class GrantIn(WriteSchema):
    key: Literal[
        "max_users",
        "max_products",
        "max_invoices_per_month",
        "max_import_rows",
        "max_locations",
        "advanced_reports",
        "data_export",
        "api_access",
        "advanced_inventory",
    ]
    limit: Annotated[int | None, Field(ge=0)] = None
    enabled: bool | None = None


assert set(GrantIn.model_fields["key"].annotation.__args__) == {*FEATURES, *LIMITS}  # type: ignore[union-attr]


class PlanCreate(WriteSchema):
    code: Annotated[str, Field(pattern=r"^[a-z0-9-]{2,30}$")]
    name: Annotated[str, Field(min_length=1, max_length=100)]
    interval: Literal["month", "year"]
    price: Amount
    currency: Annotated[str, Field(min_length=3, max_length=3)] = "BDT"
    trial_days: Annotated[int, Field(ge=0, le=90)] = 14
    is_public: bool = False
    grants: list[GrantIn] = Field(default_factory=list)


class PlanRead(ReadSchema):
    id: int
    code: str
    version: int
    name: str
    interval: str
    price: Money
    currency: str
    trial_days: int
    is_public: bool


class SubscriptionRead(ReadSchema):
    tenant_id: int
    plan_id: int
    status: str
    trial_ends_on: date | None
    period_end: date | None
    cancel_at_period_end: bool
    pending_plan_id: int | None


class PlanChoice(WriteSchema):
    plan_id: int


class ClaimRead(ReadSchema):
    method: str
    reference: str
    amount: Money


class PlatformPaymentRead(ReadSchema):
    id: int
    amount: Money
    method: str
    provider_ref: str
    received_on: date
    reverses_id: int | None


class PlatformInvoiceRead(ReadSchema):
    id: int
    tenant_id: int
    number: str
    period_start: date
    period_end: date
    amount: Money
    paid: Money
    status: str
    claims: list[ClaimRead]
    payments: list[PlatformPaymentRead]


class PaymentRecord(WriteSchema):
    amount: PositiveMoney
    method: Literal["bank", "bkash", "nagad", "other"]
    reference: Annotated[str, Field(min_length=3, max_length=100)]
    received_on: date


class Reversal(WriteSchema):
    reason: Reason


class OverrideIn(GrantIn):
    reason: Reason
    expires_on: date | None = None


@router.get("/plans", response_model=list[PlanRead])
def list_plans(db: PlatformDb, _: Operator) -> list[PlanRead]:
    rows = db.scalars(select(Plan).order_by(Plan.code, Plan.version.desc()))
    return [PlanRead.model_validate(p) for p in rows]


@router.post("/plans", response_model=PlanRead, status_code=status.HTTP_201_CREATED)
def publish_plan(db: PlatformDb, operator: BillingOperator, data: PlanCreate) -> PlanRead:
    plan = billing.publish_plan(
        db,
        grants=[billing.Grant(g.key, g.limit, g.enabled) for g in data.grants],
        **data.model_dump(exclude={"grants"}),
    )
    service.audit(
        db,
        "plan_published",
        operator_id=operator,
        details={"code": plan.code, "version": plan.version, "price": str(plan.price)},
    )
    result = PlanRead.model_validate(plan)
    db.commit()
    return result


@router.get("/subscriptions", response_model=list[SubscriptionRead])
def list_subscriptions(db: PlatformDb, _: Operator) -> list[SubscriptionRead]:
    rows = db.scalars(select(Subscription).order_by(Subscription.tenant_id))
    return [SubscriptionRead.model_validate(s) for s in rows]


@router.post("/tenants/{tenant_id}/subscription", status_code=status.HTTP_204_NO_CONTENT)
def set_plan(db: PlatformDb, operator: BillingOperator, tenant_id: int, data: PlanChoice) -> None:
    """Start a trial, or change the plan of an existing subscription."""
    if billing.subscription_of(db, tenant_id) is None:
        billing.start_trial(db, tenant_id, data.plan_id, operator)
    else:
        billing.change_plan(db, tenant_id, data.plan_id, operator)
    service.audit(
        db,
        "subscription_set",
        operator_id=operator,
        tenant_id=tenant_id,
        details={"plan_id": data.plan_id},
    )
    db.commit()


@router.post("/tenants/{tenant_id}/invoices", status_code=status.HTTP_201_CREATED)
def issue_invoice(db: PlatformDb, operator: BillingOperator, tenant_id: int) -> None:
    invoice = billing.issue_invoice(db, tenant_id)
    service.audit(
        db,
        "invoice_issued",
        operator_id=operator,
        tenant_id=tenant_id,
        details={"number": invoice.number},
    )
    db.commit()


@router.put("/tenants/{tenant_id}/overrides", status_code=status.HTTP_204_NO_CONTENT)
def set_override(
    db: PlatformDb, operator: BillingOperator, tenant_id: int, data: OverrideIn
) -> None:
    billing.set_override(db, tenant_id, actor_id=operator, **data.model_dump())
    service.audit(
        db,
        "entitlement_override",
        operator_id=operator,
        tenant_id=tenant_id,
        reason=data.reason,
        details={"key": data.key, "limit": data.limit, "enabled": data.enabled},
    )
    db.commit()


@router.get("/invoices", response_model=list[PlatformInvoiceRead])
def list_invoices(db: PlatformDb, _: Operator) -> list[PlatformInvoiceRead]:
    rows = billing.invoices(db)
    claims = billing.claims(db, [i.id for i in rows])
    payments = list(
        db.scalars(
            select(BillingPayment)
            .where(BillingPayment.invoice_id.in_([i.id for i in rows]))
            .order_by(BillingPayment.id)
        )
    )
    return [
        PlatformInvoiceRead(
            id=i.id,
            tenant_id=i.tenant_id,
            number=i.number,
            period_start=i.period_start,
            period_end=i.period_end,
            amount=i.amount,
            paid=billing.paid_amount(db, i.id),
            status=i.status,
            claims=[ClaimRead.model_validate(c) for c in claims if c.invoice_id == i.id],
            payments=[
                PlatformPaymentRead.model_validate(p) for p in payments if p.invoice_id == i.id
            ],
        )
        for i in rows
    ]


@router.post("/invoices/{invoice_id}/payments", status_code=status.HTTP_201_CREATED)
def record_payment(
    db: PlatformDb, operator: BillingOperator, invoice_id: int, data: PaymentRecord
) -> None:
    payment = billing.record_payment(db, invoice_id, actor_id=operator, **data.model_dump())
    service.audit(
        db,
        "payment_recorded",
        operator_id=operator,
        tenant_id=payment.tenant_id,
        details={"invoice_id": invoice_id, "amount": str(data.amount), "ref": data.reference},
    )
    db.commit()


@router.post("/payments/{payment_id}/reverse", status_code=status.HTTP_204_NO_CONTENT)
def reverse_payment(
    db: PlatformDb, operator: BillingOperator, payment_id: int, data: Reversal
) -> None:
    billing.reverse_payment(db, payment_id, data.reason, operator)
    payment = db.get(BillingPayment, payment_id)
    if payment is None:
        raise NotFoundError("Payment not found.")
    service.audit(
        db,
        "payment_reversed",
        operator_id=operator,
        tenant_id=payment.tenant_id,
        reason=data.reason,
        details={"payment_id": payment_id},
    )
    db.commit()


@router.post("/invoices/{invoice_id}/void", status_code=status.HTTP_204_NO_CONTENT)
def void_invoice(db: PlatformDb, operator: BillingOperator, invoice_id: int) -> None:
    billing.void_invoice(db, invoice_id)
    service.audit(db, "invoice_voided", operator_id=operator, details={"invoice_id": invoice_id})
    db.commit()
