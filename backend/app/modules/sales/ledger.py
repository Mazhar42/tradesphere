"""Customer balances and what is still open on each invoice.

Lock order for every sales posting: customer row first, then invoices, then stock. Keeping one
order everywhere is what prevents deadlocks between concurrent sales and receipts.
"""

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.modules.accounting.journal import RECEIVABLE
from app.modules.accounting.models import Account, JournalLine
from app.modules.parties.models import Customer
from app.modules.sales.models import CustomerReceipt, ReceiptAllocation, SalesInvoice
from app.shared.money import ZERO


def lock_customer(session: Session, customer_id: int) -> Customer:
    customer = session.scalar(
        select(Customer)
        .where(Customer.id == customer_id)
        .with_for_update(of=Customer)
        .execution_options(populate_existing=True)
    )
    if customer is None:
        raise NotFoundError("Customer not found.")
    return customer


def customer_balance(session: Session, customer_id: int) -> Decimal:
    """What the customer owes: Σ(debit - credit) on the receivables control account."""
    value = session.scalar(
        select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), ZERO))
        .select_from(JournalLine)
        .join(Account, Account.id == JournalLine.account_id)
        .where(
            Account.code == RECEIVABLE,
            JournalLine.party_type == "customer",
            JournalLine.party_id == customer_id,
        )
    )
    return Decimal(value or 0)


def posted_receipt_allocations() -> Select[int, Decimal]:
    """Amount received against each invoice from receipts that are still posted."""
    return (
        select(
            ReceiptAllocation.invoice_id.label("invoice_id"),
            func.sum(ReceiptAllocation.amount).label("received"),
        )
        .join(CustomerReceipt, CustomerReceipt.id == ReceiptAllocation.receipt_id)
        .where(CustomerReceipt.status == "posted")
        .group_by(ReceiptAllocation.invoice_id)
    )


def received_for_invoices(session: Session, invoice_ids: Sequence[int]) -> dict[int, Decimal]:
    if not invoice_ids:
        return {}
    rows = session.execute(
        posted_receipt_allocations().where(ReceiptAllocation.invoice_id.in_(invoice_ids))
    ).all()
    return {row.invoice_id: Decimal(row.received) for row in rows}


def outstanding(invoice: SalesInvoice, received: Decimal) -> Decimal:
    """Still owed on a posted invoice (net amount less receipts allocated to it)."""
    return invoice.net_amount - received


def open_invoices_fifo(session: Session, customer_id: int) -> list[tuple[SalesInvoice, Decimal]]:
    """Posted invoices with money still owed, oldest first (the order automatic allocation uses)."""
    received = posted_receipt_allocations().subquery()
    rows = session.execute(
        select(SalesInvoice, func.coalesce(received.c.received, ZERO))
        .outerjoin(received, received.c.invoice_id == SalesInvoice.id)
        .where(SalesInvoice.customer_id == customer_id, SalesInvoice.status == "posted")
        .order_by(SalesInvoice.business_date.asc(), SalesInvoice.id.asc())
        .with_for_update(of=SalesInvoice)
    ).all()
    result: list[tuple[SalesInvoice, Decimal]] = []
    for invoice, received_amount in rows:
        owed = outstanding(invoice, Decimal(received_amount))
        if owed > 0:
            result.append((invoice, owed))
    return result
