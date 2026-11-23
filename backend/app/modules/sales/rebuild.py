"""Rebuild and verify the daily sales projection from the documents it summarises.

`expected_daily_summary` computes what the projection must contain from posted invoices and
posted returns (reversed invoices contribute nothing). `rebuild_sales_summary` rewrites the
projection from those documents; `reconcile` compares the stored rows with the expectation.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.modules.sales.models import DailySalesSummary, SalesInvoice, SalesReturn
from app.modules.sales.projections import apply_return_summary, apply_sales_summary
from app.shared.money import ZERO

FIELDS = (
    "qty_base",
    "free_qty_base",
    "gross_amount",
    "dealer_discount",
    "company_discount",
    "net_amount",
    "cogs",
    "returns_amount",
)

Key = tuple[date, int]


def expected_daily_summary(session: Session) -> dict[Key, dict[str, Decimal]]:
    expected: dict[Key, dict[str, Decimal]] = defaultdict(lambda: {f: ZERO for f in FIELDS})
    invoices = session.scalars(select(SalesInvoice).where(SalesInvoice.status == "posted")).all()
    for invoice in invoices:
        for item in invoice.items:
            acc = expected[(invoice.business_date, item.product_id)]
            acc["qty_base"] += item.qty_base
            acc["free_qty_base"] += item.free_qty_base
            acc["gross_amount"] += item.gross_amount
            acc["dealer_discount"] += item.dealer_discount
            acc["company_discount"] += item.company_discount
            acc["net_amount"] += item.line_net
            acc["cogs"] += item.cogs
    returns = session.scalars(select(SalesReturn).where(SalesReturn.status == "posted")).all()
    for ret in returns:
        for ritem in ret.items:
            acc = expected[(ret.business_date, ritem.product_id)]
            acc["qty_base"] -= ritem.qty_base
            acc["dealer_discount"] -= ritem.dealer_discount
            acc["company_discount"] -= ritem.company_discount
            acc["net_amount"] -= ritem.net_amount
            acc["cogs"] -= ritem.cogs
            acc["returns_amount"] += ritem.gross_amount
    return dict(expected)


def stored_daily_summary(session: Session) -> dict[Key, dict[str, Decimal]]:
    stored: dict[Key, dict[str, Decimal]] = {}
    for row in session.scalars(select(DailySalesSummary)).all():
        stored[(row.business_date, row.product_id)] = {f: getattr(row, f) for f in FIELDS}
    return stored


def projection_drift(session: Session) -> list[str]:
    """Human-readable differences between the stored projection and the documents."""
    expected = expected_daily_summary(session)
    stored = stored_daily_summary(session)
    issues: list[str] = []
    for key in sorted(set(expected) | set(stored), key=str):
        want = expected.get(key, {f: ZERO for f in FIELDS})
        have = stored.get(key, {f: ZERO for f in FIELDS})
        if any(want[f] != have[f] for f in FIELDS):
            issues.append(
                f"daily sales {key[0]} product {key[1]}: documents {want} != projection {have}"
            )
    return issues


def rebuild_sales_summary(session: Session, from_date: date | None = None) -> int:
    """Rewrite the projection for every business date from `from_date` (or all dates)."""
    statement = delete(DailySalesSummary)
    if from_date is not None:
        statement = statement.where(DailySalesSummary.business_date >= from_date)
    session.execute(statement)

    invoices = session.scalars(
        select(SalesInvoice).where(
            SalesInvoice.status == "posted",
            *([SalesInvoice.business_date >= from_date] if from_date else []),
        )
    ).all()
    for invoice in invoices:
        apply_sales_summary(session, invoice, +1)

    returns = session.scalars(
        select(SalesReturn).where(
            SalesReturn.status == "posted",
            *([SalesReturn.business_date >= from_date] if from_date else []),
        )
    ).all()
    for ret in returns:
        apply_return_summary(session, ret)
    session.flush()
    return len(invoices) + len(returns)
