"""FEFO (first-expired, first-out) batch allocation for outbound stock.

Callers must already hold the product's cost lock (`lock_costs`). Every writer of stock for a
product takes that lock first, so the balances read here cannot change underneath the caller.
Batches with no expiry date are used last; untracked products allocate from the batch-less row.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError
from app.modules.catalog.models import Product
from app.modules.inventory.models import Batch, StockBalance

# (batch id or None for untracked stock, base quantity taken from it)
Allocation = tuple[int | None, Decimal]


def allocate_fefo(
    session: Session, *, product: Product, location_id: int, qty: Decimal
) -> list[Allocation]:
    """Split `qty` base units across the batches in `location_id`, earliest expiry first."""
    rows = session.execute(
        select(StockBalance.batch_id, StockBalance.qty_base)
        .outerjoin(Batch, Batch.id == StockBalance.batch_id)
        .where(
            StockBalance.product_id == product.id,
            StockBalance.location_id == location_id,
            StockBalance.qty_base > 0,
        )
        .order_by(
            Batch.expiry_date.asc().nulls_last(),
            StockBalance.batch_id.asc().nulls_last(),
        )
    ).all()

    available = sum((row.qty_base for row in rows), Decimal(0))
    if available < qty:
        raise BusinessRuleError(
            f"Insufficient stock for {product.sku} - {product.name_en}: "
            f"need {qty:f}, available {available:f}.",
            code="insufficient_stock",
        )

    allocations: list[Allocation] = []
    remaining = qty
    for row in rows:
        if remaining <= 0:
            break
        take = min(row.qty_base, remaining)
        allocations.append((row.batch_id, take))
        remaining -= take
    return allocations
