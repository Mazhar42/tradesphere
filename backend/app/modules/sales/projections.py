"""Projection of posted sales per business date and product (`daily_sales_summary`).

Updated inside the same transaction as the invoice: sign +1 when posted, -1 when reversed.
"""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modules.sales.models import DailySalesSummary, SalesInvoice, SalesReturn
from app.shared.money import ZERO


def apply_sales_summary(session: Session, invoice: SalesInvoice, sign: int) -> None:
    for item in invoice.items:
        delta = {
            "qty_base": sign * item.qty_base,
            "free_qty_base": sign * item.free_qty_base,
            "gross_amount": sign * item.gross_amount,
            "dealer_discount": sign * item.dealer_discount,
            "company_discount": sign * item.company_discount,
            "net_amount": sign * item.line_net,
            "cogs": sign * item.cogs,
        }
        stmt = insert(DailySalesSummary).values(
            business_date=invoice.business_date,
            product_id=item.product_id,
            **delta,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[DailySalesSummary.business_date, DailySalesSummary.product_id],
            set_={
                column: getattr(DailySalesSummary, column) + getattr(stmt.excluded, column)
                for column in delta
            },
        )
        session.execute(stmt)


def apply_return_summary(session: Session, ret: SalesReturn, sign: int = 1) -> None:
    """A posted return is dated on its own business date, like its journal entries: it reduces
    that day's sold quantity, discounts, net and cost, and adds to `returns_amount`."""
    for item in ret.items:
        delta = {
            "qty_base": -item.qty_base,
            "gross_amount": ZERO,
            "returns_amount": item.gross_amount,
            "dealer_discount": -item.dealer_discount,
            "company_discount": -item.company_discount,
            "net_amount": -item.net_amount,
            "cogs": -item.cogs,
        }
        delta = {column: sign * amount for column, amount in delta.items()}
        stmt = insert(DailySalesSummary).values(
            business_date=ret.business_date,
            product_id=item.product_id,
            **delta,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[DailySalesSummary.business_date, DailySalesSummary.product_id],
            set_={
                column: getattr(DailySalesSummary, column) + getattr(stmt.excluded, column)
                for column in delta
            },
        )
        session.execute(stmt)
