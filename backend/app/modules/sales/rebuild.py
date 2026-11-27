"""Rebuild the daily sales projection from posted invoices and returns. Whether it matches
is checked in SQL by `integrity.reconcile`."""

from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.modules.sales.models import DailySalesSummary, SalesInvoice, SalesReturn
from app.modules.sales.projections import apply_return_summary, apply_sales_summary


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
