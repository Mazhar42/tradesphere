"""Customer position, customer aging and the sales summary."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounting.journal import RECEIVABLE
from app.modules.accounting.models import Account, JournalLine
from app.modules.parties.models import Customer
from app.modules.sales.ledger import credit_notes, customer_balance, posted_receipt_allocations
from app.modules.sales.models import DailySalesSummary, SalesInvoice
from app.modules.sales.schemas import AgingReport, AgingRow, CustomerSummary, SalesSummaryRow
from app.shared.money import ZERO


def customer_summary(session: Session, customer: Customer) -> CustomerSummary:
    balance = customer_balance(session, customer.id)
    received = posted_receipt_allocations().subquery()
    credited = credit_notes().subquery()
    rows = session.execute(
        select(
            SalesInvoice.net_amount,
            func.coalesce(received.c.received, ZERO),
            func.coalesce(credited.c.credited, ZERO),
        )
        .outerjoin(received, received.c.invoice_id == SalesInvoice.id)
        .outerjoin(credited, credited.c.invoice_id == SalesInvoice.id)
        .where(SalesInvoice.customer_id == customer.id, SalesInvoice.status == "posted")
    ).all()
    open_invoices = sum(1 for net, got, cr in rows if net - Decimal(got) - Decimal(cr) > 0)
    return CustomerSummary(
        customer_id=customer.id,
        code=customer.code,
        name=customer.name,
        credit_limit=customer.credit_limit,
        balance=balance,
        available_credit=customer.credit_limit - balance,
        open_invoices=open_invoices,
    )


def _bucket(age_days: int) -> str:
    if age_days <= 30:
        return "current"
    if age_days <= 60:
        return "days_31_60"
    if age_days <= 90:
        return "days_61_90"
    return "days_over_90"


def aging(session: Session, as_of: date) -> AgingReport:
    """Who owes what, by how long ago the invoice was raised.

    `on_account` is the part of the customer's balance that no invoice explains (opening dues,
    receipts not yet matched to an invoice). It is shown so the rows always add up to the ledger.
    """
    received = posted_receipt_allocations().subquery()
    credited = credit_notes().subquery()
    open_rows = session.execute(
        select(
            SalesInvoice.customer_id,
            SalesInvoice.business_date,
            SalesInvoice.net_amount,
            func.coalesce(received.c.received, ZERO),
            func.coalesce(credited.c.credited, ZERO),
        )
        .outerjoin(received, received.c.invoice_id == SalesInvoice.id)
        .outerjoin(credited, credited.c.invoice_id == SalesInvoice.id)
        .where(SalesInvoice.status == "posted")
    ).all()
    buckets: dict[int, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for customer_id, invoice_date, net, got, cr in open_rows:
        owed = net - Decimal(got) - Decimal(cr)
        if owed > 0:
            buckets[customer_id][_bucket((as_of - invoice_date).days)] += owed

    balance_rows = session.execute(
        select(JournalLine.party_id, func.sum(JournalLine.debit - JournalLine.credit))
        .select_from(JournalLine)
        .join(Account, Account.id == JournalLine.account_id)
        .where(Account.code == RECEIVABLE, JournalLine.party_type == "customer")
        .group_by(JournalLine.party_id)
    ).all()
    balances = {int(pid): Decimal(total or 0) for pid, total in balance_rows if pid is not None}

    customer_ids = set(buckets) | {cid for cid, total in balances.items() if total != 0}
    customers = (
        {
            c.id: c
            for c in session.scalars(select(Customer).where(Customer.id.in_(customer_ids))).all()
        }
        if customer_ids
        else {}
    )

    rows: list[AgingRow] = []
    for customer_id in sorted(customer_ids, key=lambda cid: customers[cid].name.lower()):
        customer = customers[customer_id]
        b = buckets.get(customer_id, {})
        invoiced = sum(b.values(), ZERO)
        total = balances.get(customer_id, ZERO)
        rows.append(
            AgingRow(
                customer_id=customer.id,
                code=customer.code,
                name=customer.name,
                current=b.get("current", ZERO),
                days_31_60=b.get("days_31_60", ZERO),
                days_61_90=b.get("days_61_90", ZERO),
                days_over_90=b.get("days_over_90", ZERO),
                invoiced_due=invoiced,
                on_account=total - invoiced,
                total=total,
            )
        )

    def total_of(field: str) -> Decimal:
        return sum((getattr(r, field) for r in rows), ZERO)

    totals = AgingRow(
        customer_id=0,
        code="",
        name="Total",
        current=total_of("current"),
        days_31_60=total_of("days_31_60"),
        days_61_90=total_of("days_61_90"),
        days_over_90=total_of("days_over_90"),
        invoiced_due=total_of("invoiced_due"),
        on_account=total_of("on_account"),
        total=total_of("total"),
    )
    return AgingReport(as_of=as_of, rows=rows, totals=totals)


def sales_summary(
    session: Session, *, date_from: date, date_to: date, group: str
) -> list[SalesSummaryRow]:
    """Sales per day or per month from the daily projection."""
    rows = session.scalars(
        select(DailySalesSummary).where(
            DailySalesSummary.business_date >= date_from,
            DailySalesSummary.business_date <= date_to,
        )
    ).all()
    periods: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        key = (
            row.business_date.isoformat() if group == "day" else row.business_date.strftime("%Y-%m")
        )
        bucket = periods.setdefault(
            key,
            {
                "qty_base": ZERO,
                "gross_amount": ZERO,
                "returns_amount": ZERO,
                "dealer_discount": ZERO,
                "company_discount": ZERO,
                "net_amount": ZERO,
                "cogs": ZERO,
            },
        )
        bucket["qty_base"] += row.qty_base
        bucket["gross_amount"] += row.gross_amount
        bucket["returns_amount"] += row.returns_amount
        bucket["dealer_discount"] += row.dealer_discount
        bucket["company_discount"] += row.company_discount
        bucket["net_amount"] += row.net_amount
        bucket["cogs"] += row.cogs
    result = []
    for period in sorted(periods):
        b = periods[period]
        result.append(
            SalesSummaryRow(
                period=period,
                qty_base=b["qty_base"],
                gross_amount=b["gross_amount"],
                returns_amount=b["returns_amount"],
                dealer_discount=b["dealer_discount"],
                company_discount=b["company_discount"],
                net_amount=b["net_amount"],
                cogs=b["cogs"],
                # Net sales = gross - returns - dealer discounts. Company-funded discounts are
                # not a reduction of sales (the company pays them).
                gross_profit=(b["gross_amount"] - b["returns_amount"] - b["dealer_discount"])
                - b["cogs"],
            )
        )
    return result
