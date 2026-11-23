"""Customer receipts: money in, allocated to invoices (oldest first unless told otherwise).

Whatever is not allocated stays on the customer's account as credit.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.accounting.journal import RECEIVABLE, JLine, post_journal, reverse_entries
from app.modules.accounting.models import PaymentAccount
from app.modules.accounting.service import require_active_payment_account
from app.modules.parties.models import Customer
from app.modules.sales.ledger import (
    lock_customer,
    open_invoices_fifo,
    outstanding,
    received_for_invoices,
)
from app.modules.sales.models import CustomerReceipt, ReceiptAllocation, SalesInvoice
from app.modules.sales.schemas import (
    ReceiptAllocationRead,
    ReceiptCreate,
    ReceiptListItem,
    ReceiptRead,
)
from app.modules.settings.service import ensure_period_open
from app.shared.audit import record_audit, snapshot
from app.shared.dates import business_date as today
from app.shared.dates import now_utc
from app.shared.money import ZERO
from app.shared.pagination import PageParams, paginate
from app.shared.sequences import next_doc_number

DOC_TYPE = "customer_receipt"
DOC_PREFIX = "RCT"


def get_receipt(session: Session, receipt_id: int) -> CustomerReceipt:
    receipt = session.get(CustomerReceipt, receipt_id)
    if receipt is None:
        raise NotFoundError("Receipt not found.")
    return receipt


def _lock_receipt(session: Session, receipt_id: int) -> CustomerReceipt:
    receipt = session.scalar(
        select(CustomerReceipt)
        .where(CustomerReceipt.id == receipt_id)
        .with_for_update(of=CustomerReceipt)
        .execution_options(populate_existing=True)
    )
    if receipt is None:
        raise NotFoundError("Receipt not found.")
    return receipt


def _resolve_allocations(
    session: Session, customer: Customer, data: ReceiptCreate
) -> list[tuple[SalesInvoice, Decimal]]:
    if not data.allocations:
        # Automatic: oldest open invoices first.
        remaining = data.amount
        chosen: list[tuple[SalesInvoice, Decimal]] = []
        for open_invoice, owed in open_invoices_fifo(session, customer.id):
            if remaining <= 0:
                break
            take = min(owed, remaining)
            chosen.append((open_invoice, take))
            remaining -= take
        return chosen

    ids = [a.invoice_id for a in data.allocations]
    if len(ids) != len(set(ids)):
        raise BusinessRuleError(
            "Each invoice may appear once in the allocation.",
            errors=[{"field": "allocations", "message": "Duplicate invoice"}],
        )
    total = sum((a.amount for a in data.allocations), ZERO)
    if total > data.amount:
        raise BusinessRuleError(
            f"Allocations ({total:f}) exceed the receipt amount ({data.amount:f}).",
            code="over_allocated",
            errors=[{"field": "allocations", "message": "More than the receipt amount"}],
        )
    invoices = {
        i.id: i
        for i in session.scalars(
            select(SalesInvoice)
            .where(SalesInvoice.id.in_(ids))
            .order_by(SalesInvoice.id)
            .with_for_update(of=SalesInvoice)
            .execution_options(populate_existing=True)
        ).all()
    }
    received = received_for_invoices(session, ids)
    chosen = []
    for index, allocation in enumerate(data.allocations):
        invoice = invoices.get(allocation.invoice_id)
        field = {"field": f"allocations.{index}.invoice_id", "message": "Unknown invoice"}
        if invoice is None:
            raise BusinessRuleError("Unknown invoice.", errors=[field])
        if invoice.customer_id != customer.id:
            raise BusinessRuleError(
                f"{invoice.doc_no or 'That invoice'} belongs to a different customer.",
                errors=[field],
            )
        if invoice.status != "posted":
            raise BusinessRuleError(
                f"{invoice.doc_no or 'That invoice'} is not posted.",
                code="invoice_not_posted",
                errors=[field],
            )
        owed = outstanding(invoice, received.get(invoice.id, ZERO))
        if allocation.amount > owed:
            raise BusinessRuleError(
                f"{invoice.doc_no}: allocating {allocation.amount:f} exceeds "
                f"the {owed:f} still due.",
                code="over_payment",
                errors=[{"field": f"allocations.{index}.amount", "message": "More than due"}],
            )
        chosen.append((invoice, allocation.amount))
    return chosen


def post_money_in(
    session: Session,
    receipt: CustomerReceipt,
    customer: Customer,
    payment_account: PaymentAccount,
    *,
    actor_id: int | None,
) -> None:
    post_journal(
        session,
        business_date=receipt.business_date,
        source_doc_type=DOC_TYPE,
        source_doc_id=receipt.id,
        source_doc_no=receipt.doc_no,
        memo=f"Receipt {receipt.doc_no} from {customer.name}",
        lines=[
            JLine(
                payment_account.account_id,
                debit=receipt.amount,
                payment_account_id=payment_account.id,
                memo=f"Received {receipt.doc_no}",
            ),
            JLine(
                RECEIVABLE,
                credit=receipt.amount,
                party_type="customer",
                party_id=customer.id,
                memo=f"Receipt {receipt.doc_no}",
            ),
        ],
        actor_id=actor_id,
    )


def create_receipt(session: Session, data: ReceiptCreate, actor: CurrentUser) -> CustomerReceipt:
    customer = lock_customer(session, data.customer_id)
    if not customer.is_active:
        raise BusinessRuleError(
            "This customer is inactive.",
            errors=[{"field": "customer_id", "message": "Inactive customer"}],
        )
    pa = require_active_payment_account(session, data.payment_account_id)
    on = data.business_date or today()
    ensure_period_open(session, on)
    allocations = _resolve_allocations(session, customer, data)

    receipt = CustomerReceipt(
        doc_no=next_doc_number(session, DOC_PREFIX, on),
        customer_id=customer.id,
        business_date=on,
        amount=data.amount,
        payment_account_id=pa.id,
        reference=data.reference,
        notes=data.notes,
        status="posted",
        posted_at=now_utc(),
        created_by_id=actor.id,
    )
    receipt.allocations = [
        ReceiptAllocation(invoice_id=invoice.id, amount=amount) for invoice, amount in allocations
    ]
    session.add(receipt)
    session.flush()
    post_money_in(session, receipt, customer, pa, actor_id=actor.id)
    record_audit(
        session,
        actor_id=actor.id,
        action="post",
        entity_type="customer_receipt",
        entity_id=receipt.id,
        after=snapshot(
            receipt,
            extra={
                "allocations": [
                    {"invoice_id": a.invoice_id, "amount": a.amount} for a in receipt.allocations
                ]
            },
        ),
    )
    return receipt


def create_cash_receipt_for_sale(
    session: Session, invoice: SalesInvoice, *, actor_id: int | None
) -> CustomerReceipt:
    """The cash taken at the counter with an invoice: a receipt fully allocated to that invoice."""
    pa = session.get(PaymentAccount, invoice.payment_account_id)
    if pa is None:
        raise NotFoundError("Payment account not found.")
    receipt = CustomerReceipt(
        doc_no=next_doc_number(session, DOC_PREFIX, invoice.business_date),
        customer_id=invoice.customer_id,
        business_date=invoice.business_date,
        amount=invoice.paid_amount,
        payment_account_id=pa.id,
        notes=f"Cash at the counter for {invoice.doc_no}",
        status="posted",
        sales_invoice_id=invoice.id,
        posted_at=now_utc(),
        created_by_id=actor_id,
    )
    receipt.allocations = [ReceiptAllocation(invoice_id=invoice.id, amount=invoice.paid_amount)]
    session.add(receipt)
    session.flush()
    post_money_in(session, receipt, invoice.customer, pa, actor_id=actor_id)
    return receipt


def reverse_receipt_record(
    session: Session, receipt: CustomerReceipt, *, on: date, actor_id: int | None
) -> None:
    reverse_entries(
        session,
        source_doc_type=DOC_TYPE,
        source_doc_id=receipt.id,
        business_date=on,
        actor_id=actor_id,
    )
    receipt.status = "reversed"
    receipt.reversed_at = now_utc()
    session.flush()


def reverse_receipt(session: Session, receipt_id: int, actor: CurrentUser) -> CustomerReceipt:
    customer_id = session.scalar(
        select(CustomerReceipt.customer_id).where(CustomerReceipt.id == receipt_id)
    )
    if customer_id is None:
        raise NotFoundError("Receipt not found.")
    lock_customer(session, customer_id)  # same lock order as every sales posting
    receipt = _lock_receipt(session, receipt_id)
    if receipt.status != "posted":
        raise ConflictError("Only posted receipts can be reversed.", code="not_posted")
    if receipt.sales_invoice_id is not None:
        raise ConflictError(
            "This cash was taken with a sale. Reverse that invoice instead.",
            code="sale_receipt",
        )
    on = today()
    ensure_period_open(session, receipt.business_date)
    ensure_period_open(session, on)
    reverse_receipt_record(session, receipt, on=on, actor_id=actor.id)
    record_audit(
        session,
        actor_id=actor.id,
        action="reverse",
        entity_type="customer_receipt",
        entity_id=receipt.id,
        after={"doc_no": receipt.doc_no, "status": receipt.status},
    )
    return receipt


# --- Reads ------------------------------------------------------------------------------------


def receipt_to_read(receipt: CustomerReceipt) -> ReceiptRead:
    posted = receipt.status == "posted"
    allocated = sum((a.amount for a in receipt.allocations), ZERO) if posted else ZERO
    return ReceiptRead(
        id=receipt.id,
        doc_no=receipt.doc_no,
        customer_id=receipt.customer_id,
        customer_name=receipt.customer.name,
        business_date=receipt.business_date,
        amount=receipt.amount,
        payment_account_id=receipt.payment_account_id,
        payment_account_name=receipt.payment_account.name,
        reference=receipt.reference,
        notes=receipt.notes,
        status=receipt.status,
        sales_invoice_id=receipt.sales_invoice_id,
        allocated=allocated,
        unallocated=receipt.amount - allocated,
        allocations=[
            ReceiptAllocationRead(
                invoice_id=a.invoice_id, invoice_doc_no=a.invoice.doc_no, amount=a.amount
            )
            for a in receipt.allocations
        ],
        posted_at=receipt.posted_at,
        reversed_at=receipt.reversed_at,
    )


def receipt_list_item(receipt: CustomerReceipt) -> ReceiptListItem:
    allocated = (
        sum((a.amount for a in receipt.allocations), ZERO) if receipt.status == "posted" else ZERO
    )
    return ReceiptListItem(
        id=receipt.id,
        doc_no=receipt.doc_no,
        customer_id=receipt.customer_id,
        customer_name=receipt.customer.name,
        business_date=receipt.business_date,
        amount=receipt.amount,
        payment_account_name=receipt.payment_account.name,
        reference=receipt.reference,
        status=receipt.status,
        sales_invoice_id=receipt.sales_invoice_id,
        allocated=allocated,
    )


def list_receipts(
    session: Session,
    params: PageParams,
    *,
    customer_id: int | None,
    date_from: date | None,
    date_to: date | None,
) -> tuple[list[CustomerReceipt], int]:
    stmt = select(CustomerReceipt).order_by(
        CustomerReceipt.business_date.desc(), CustomerReceipt.id.desc()
    )
    if customer_id is not None:
        stmt = stmt.where(CustomerReceipt.customer_id == customer_id)
    if date_from is not None:
        stmt = stmt.where(CustomerReceipt.business_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(CustomerReceipt.business_date <= date_to)
    items, total = paginate(session, stmt, params)
    return list(items), total
