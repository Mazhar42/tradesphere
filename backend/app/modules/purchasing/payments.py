"""Supplier payments and the supplier account.

A payment is money handed to the company; it does not mean goods arrived. It is posted as
Dr Supplier account / Cr Cash-Bank-bKash, so an unapplied payment shows as an *advance*
(a debit balance). Allocating a payment to purchase orders only links it to those orders
for the paid / received / undelivered view; it does not change the books.
"""

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.accounting.journal import (
    SUPPLIER_PAYABLE,
    JLine,
    account_balance,
    post_journal,
    reverse_entries,
)
from app.modules.accounting.service import require_active_payment_account
from app.modules.parties.models import Supplier
from app.modules.purchasing.models import (
    OPEN_PO_STATUSES,
    PAYABLE_PO_STATUSES,
    PaymentPoAllocation,
    PurchaseOrder,
    PurchaseOrderItem,
    SupplierPayment,
)
from app.modules.purchasing.schemas import (
    AllocationIn,
    AllocationRead,
    SupplierPaymentCreate,
    SupplierPaymentRead,
    SupplierSummary,
)
from app.modules.purchasing.service import get_supplier, lock_po, paid_by_po, payable_value
from app.modules.settings.service import ensure_period_open
from app.shared.audit import record_audit
from app.shared.dates import business_date as today
from app.shared.dates import now_utc
from app.shared.money import ZERO
from app.shared.pagination import PageParams, paginate
from app.shared.sequences import next_doc_number

DOC_TYPE = "SPAY"


def payment_to_read(payment: SupplierPayment) -> SupplierPaymentRead:
    allocated = sum((a.amount for a in payment.allocations), ZERO)
    return SupplierPaymentRead(
        id=payment.id,
        doc_no=payment.doc_no,
        supplier_id=payment.supplier_id,
        supplier_name=payment.supplier.name,
        business_date=payment.business_date,
        amount=payment.amount,
        payment_account_id=payment.payment_account_id,
        payment_account_name=payment.payment_account.name,
        reference=payment.reference,
        notes=payment.notes,
        status=payment.status,
        allocated_amount=allocated,
        unallocated_amount=payment.amount - allocated,
        allocations=[
            AllocationRead(id=a.id, po_id=a.po_id, po_doc_no=a.po.doc_no, amount=a.amount)
            for a in payment.allocations
        ],
        created_at=payment.created_at,
    )


def get_payment(session: Session, payment_id: int) -> SupplierPayment:
    payment = session.get(SupplierPayment, payment_id)
    if payment is None:
        raise NotFoundError("Supplier payment not found.")
    return payment


def _lock_payment(session: Session, payment_id: int) -> SupplierPayment:
    payment = session.scalar(
        select(SupplierPayment)
        .where(SupplierPayment.id == payment_id)
        .with_for_update(of=SupplierPayment)
        .execution_options(populate_existing=True)
    )
    if payment is None:
        raise NotFoundError("Supplier payment not found.")
    return payment


def list_payments(
    session: Session,
    params: PageParams,
    *,
    q: str | None,
    supplier_id: int | None,
    po_id: int | None,
    status: str | None,
) -> tuple[Sequence[SupplierPayment], int]:
    stmt = (
        select(SupplierPayment)
        .join(Supplier, Supplier.id == SupplierPayment.supplier_id)
        .order_by(SupplierPayment.business_date.desc(), SupplierPayment.id.desc())
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                SupplierPayment.doc_no.ilike(like),
                SupplierPayment.reference.ilike(like),
                Supplier.name.ilike(like),
            )
        )
    if supplier_id is not None:
        stmt = stmt.where(SupplierPayment.supplier_id == supplier_id)
    if po_id is not None:
        stmt = stmt.where(
            SupplierPayment.id.in_(
                select(PaymentPoAllocation.payment_id).where(PaymentPoAllocation.po_id == po_id)
            )
        )
    if status:
        stmt = stmt.where(SupplierPayment.status == status)
    return paginate(session, stmt, params)


def _validate_allocations(
    session: Session,
    *,
    supplier_id: int,
    capacity: Decimal,
    allocations: Sequence[AllocationIn],
) -> list[PurchaseOrder]:
    """Check the allocation list; returns the (locked) purchase orders in id order."""
    if not allocations:
        return []
    po_ids = [a.po_id for a in allocations]
    if len(po_ids) != len(set(po_ids)):
        raise BusinessRuleError(
            "Each purchase order may appear once.",
            errors=[{"field": "allocations", "message": "Duplicate order"}],
        )
    if sum((a.amount for a in allocations), ZERO) > capacity:
        raise BusinessRuleError(
            f"Allocations ({sum((a.amount for a in allocations), ZERO):f}) exceed the unallocated "
            f"amount of this payment ({capacity:f}).",
            code="over_allocated",
            errors=[{"field": "allocations", "message": "More than the payment amount"}],
        )
    pos = {po_id: lock_po(session, po_id) for po_id in sorted(po_ids)}
    already = paid_by_po(session, list(pos))
    for index, allocation in enumerate(allocations):
        po = pos[allocation.po_id]
        if po.supplier_id != supplier_id:
            raise BusinessRuleError(
                "That purchase order belongs to a different supplier.",
                errors=[{"field": f"allocations.{index}.po_id", "message": "Different supplier"}],
            )
        if po.status not in PAYABLE_PO_STATUSES:
            raise BusinessRuleError(
                f"{po.doc_no or 'Draft order'} is {po.status}; confirm it before "
                "allocating payments.",
                code="po_not_payable",
                errors=[
                    {"field": f"allocations.{index}.po_id", "message": f"Order is {po.status}"}
                ],
            )
        room = payable_value(po) - already.get(po.id, ZERO)
        if allocation.amount > room:
            raise BusinessRuleError(
                f"{po.doc_no}: allocating {allocation.amount:f} exceeds what is still payable "
                f"on this order ({max(room, ZERO):f}).",
                code="over_payment",
                errors=[{"field": f"allocations.{index}.amount", "message": "More than payable"}],
            )
    return list(pos.values())


def create_payment(
    session: Session, data: SupplierPaymentCreate, actor: CurrentUser
) -> SupplierPayment:
    supplier = get_supplier(session, data.supplier_id)
    pa = require_active_payment_account(session, data.payment_account_id)
    on = data.business_date or today()
    ensure_period_open(session, on)
    _validate_allocations(
        session, supplier_id=supplier.id, capacity=data.amount, allocations=data.allocations
    )

    payment = SupplierPayment(
        doc_no=next_doc_number(session, DOC_TYPE, on),
        supplier_id=supplier.id,
        business_date=on,
        amount=data.amount,
        payment_account_id=pa.id,
        reference=data.reference,
        notes=data.notes,
        status="posted",
        created_by_id=actor.id,
        allocations=[PaymentPoAllocation(po_id=a.po_id, amount=a.amount) for a in data.allocations],
    )
    session.add(payment)
    session.flush()
    post_journal(
        session,
        business_date=on,
        source_doc_type=DOC_TYPE,
        source_doc_id=payment.id,
        source_doc_no=payment.doc_no,
        memo=f"Payment {payment.doc_no} to {supplier.name}",
        lines=[
            JLine(
                SUPPLIER_PAYABLE,
                debit=data.amount,
                party_type="supplier",
                party_id=supplier.id,
                memo=f"Payment {payment.doc_no}"
                + (f" ({data.reference})" if data.reference else ""),
            ),
            JLine(
                pa.account_id,
                credit=data.amount,
                payment_account_id=pa.id,
                memo=f"Paid to {supplier.name}",
            ),
        ],
        actor_id=actor.id,
    )
    session.refresh(payment)
    record_audit(
        session,
        actor_id=actor.id,
        action="post",
        entity_type="supplier_payment",
        entity_id=payment.id,
        after={
            "doc_no": payment.doc_no,
            "supplier_id": supplier.id,
            "amount": payment.amount,
            "allocations": [{"po_id": a.po_id, "amount": a.amount} for a in payment.allocations],
        },
    )
    return payment


def allocate_payment(
    session: Session,
    payment_id: int,
    allocations: Sequence[AllocationIn],
    actor: CurrentUser,
) -> SupplierPayment:
    """Link (more of) an existing payment to purchase orders, e.g. an advance paid earlier."""
    payment = _lock_payment(session, payment_id)
    if payment.status != "posted":
        raise ConflictError("This payment was reversed.", code="not_posted")
    unallocated = payment.amount - sum((a.amount for a in payment.allocations), ZERO)
    _validate_allocations(
        session, supplier_id=payment.supplier_id, capacity=unallocated, allocations=allocations
    )
    existing = {a.po_id: a for a in payment.allocations}
    for allocation in allocations:
        if allocation.po_id in existing:
            existing[allocation.po_id].amount += allocation.amount
        else:
            payment.allocations.append(
                PaymentPoAllocation(po_id=allocation.po_id, amount=allocation.amount)
            )
    session.flush()
    session.refresh(payment)
    record_audit(
        session,
        actor_id=actor.id,
        action="allocate",
        entity_type="supplier_payment",
        entity_id=payment.id,
        after={"allocations": [{"po_id": a.po_id, "amount": a.amount} for a in allocations]},
    )
    return payment


def reverse_payment(session: Session, payment_id: int, actor: CurrentUser) -> SupplierPayment:
    payment = _lock_payment(session, payment_id)
    if payment.status != "posted":
        raise ConflictError("This payment was already reversed.", code="not_posted")
    ensure_period_open(session, payment.business_date)
    reverse_entries(
        session,
        source_doc_type=DOC_TYPE,
        source_doc_id=payment.id,
        business_date=today(),
        actor_id=actor.id,
    )
    payment.status = "reversed"
    payment.reversed_at = now_utc()
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="reverse",
        entity_type="supplier_payment",
        entity_id=payment.id,
        after={"doc_no": payment.doc_no},
    )
    return payment


def supplier_summary(session: Session, supplier_id: int) -> SupplierSummary:
    get_supplier(session, supplier_id, must_be_active=False)
    balance = account_balance(
        session, SUPPLIER_PAYABLE, party_type="supplier", party_id=supplier_id
    )
    outstanding, open_count = session.execute(
        select(
            func.coalesce(
                func.sum(
                    PurchaseOrderItem.line_value
                    - PurchaseOrderItem.received_value
                    - PurchaseOrderItem.cancelled_value
                ),
                0,
            ),
            func.count(func.distinct(PurchaseOrder.id)),
        )
        .select_from(PurchaseOrder)
        .join(PurchaseOrderItem, PurchaseOrderItem.po_id == PurchaseOrder.id)
        .where(
            PurchaseOrder.supplier_id == supplier_id,
            PurchaseOrder.status.in_(OPEN_PO_STATUSES),
        )
    ).one()
    return SupplierSummary(
        supplier_id=supplier_id,
        ledger_balance=balance,
        position="advance" if balance > 0 else "payable" if balance < 0 else "settled",
        advance=max(balance, ZERO),
        payable=max(-balance, ZERO),
        outstanding_po_value=Decimal(outstanding),
        open_po_count=int(open_count),
    )
