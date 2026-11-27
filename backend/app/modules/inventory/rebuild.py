"""Rewrite the stock projections from the movement ledger (6.5), for use after a check fails."""

from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.modules.inventory.models import InventoryMovement, ProductCost, StockBalance
from app.modules.inventory.stock import COST_Q

ZERO = Decimal(0)


def rebuild_stock(session: Session) -> tuple[int, int]:
    """Returns (balance rows, cost rows) written. Run while nobody is posting."""
    m = InventoryMovement
    balances = session.execute(
        select(m.product_id, m.batch_id, m.location_id, func.sum(m.qty_base)).group_by(
            m.product_id, m.batch_id, m.location_id
        )
    ).all()
    costs = session.execute(
        select(m.product_id, func.sum(m.qty_base), func.sum(m.value)).group_by(m.product_id)
    ).all()
    session.execute(delete(StockBalance))
    session.execute(delete(ProductCost))
    session.add_all(
        StockBalance(product_id=p, batch_id=b, location_id=loc, qty_base=qty)
        for p, b, loc, qty in balances
    )
    session.add_all(
        ProductCost(
            product_id=p,
            qty_on_hand=qty,
            total_value=value,
            avg_cost=(value / qty).quantize(COST_Q) if qty > 0 else ZERO,
        )
        for p, qty, value in costs
    )
    session.flush()
    return len(balances), len(costs)
