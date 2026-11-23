"""Stock engine: the only code allowed to change stock or product cost.

Rules
- Every change appends an `inventory_movements` row and updates the `stock_balances`
  and `product_costs` projections inside the caller's transaction.
- Cost is a moving weighted average per product. Inbound adds quantity and value
  (free goods have value 0, which lowers the average). Outbound removes value
  proportionally; taking *all* remaining quantity removes *all* remaining value, so
  rounding never leaves a residue.
- Callers lock every product they will touch with `lock_costs` first (rows are locked
  in product-id order, which prevents deadlocks between concurrent postings).
- Negative stock is impossible: the service checks first, and a CHECK constraint is the
  last line of defence.
"""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.errors import BusinessRuleError, ConflictError
from app.modules.catalog.models import Product
from app.modules.inventory.models import InventoryMovement, Location, ProductCost, StockBalance
from app.shared.money import ZERO, round_money

COST_Q = Decimal("0.0001")

MOVEMENT_TYPES = {
    "opening",
    "grn",
    "grn_free",
    "sale",
    "sale_free",
    "sales_return",
    "purchase_return",
    "adjustment",
    "transfer_in",
    "transfer_out",
    "claim_out",
    "write_off",
    "reversal",
}


def get_location_id(session: Session, code: str) -> int:
    location_id = session.scalar(select(Location.id).where(Location.code == code))
    if location_id is None:
        raise BusinessRuleError(f"Unknown stock location '{code}'.", code="unknown_location")
    return location_id


def lock_costs(session: Session, product_ids: Iterable[int]) -> dict[int, ProductCost]:
    """Create missing cost rows and lock them (ordered by product id)."""
    ids = sorted(set(product_ids))
    if not ids:
        return {}
    session.flush()
    session.execute(
        insert(ProductCost).values([{"product_id": i} for i in ids]).on_conflict_do_nothing()
    )
    rows = session.scalars(
        select(ProductCost)
        .where(ProductCost.product_id.in_(ids))
        .order_by(ProductCost.product_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    return {row.product_id: row for row in rows}


def _balance_filter(
    product_id: int, batch_id: int | None, location_id: int
) -> tuple[ColumnElement[bool], ...]:
    return (
        StockBalance.product_id == product_id,
        StockBalance.batch_id.is_(None) if batch_id is None else StockBalance.batch_id == batch_id,
        StockBalance.location_id == location_id,
    )


def _refresh_avg(cost: ProductCost) -> None:
    cost.avg_cost = (
        (cost.total_value / cost.qty_on_hand).quantize(COST_Q) if cost.qty_on_hand > 0 else ZERO
    )


def _product_label(session: Session, product_id: int) -> str:
    product = session.get(Product, product_id)
    return f"{product.sku} - {product.name_en}" if product else f"product {product_id}"


def stock_in(
    session: Session,
    *,
    product_id: int,
    location_id: int,
    qty: Decimal,
    value: Decimal,
    business_date: date,
    movement_type: str,
    source_doc_type: str,
    source_doc_id: int,
    source_doc_no: str | None = None,
    source_line_id: int | None = None,
    batch_id: int | None = None,
    actor_id: int | None = None,
    reversal_of_id: int | None = None,
) -> InventoryMovement:
    """Add `qty` base units worth `value` (0 for free goods)."""
    if qty <= 0 or value < 0:
        raise BusinessRuleError("Inbound stock needs a positive quantity and a non-negative value.")
    value = round_money(value)
    cost = lock_costs(session, [product_id])[product_id]
    cost.qty_on_hand += qty
    cost.total_value += value
    _refresh_avg(cost)

    session.execute(
        insert(StockBalance)
        .values(product_id=product_id, batch_id=batch_id, location_id=location_id, qty_base=qty)
        .on_conflict_do_update(
            constraint="uq_stock_balances_key",
            set_={"qty_base": StockBalance.qty_base + qty},
        )
    )
    movement = InventoryMovement(
        business_date=business_date,
        product_id=product_id,
        batch_id=batch_id,
        location_id=location_id,
        qty_base=qty,
        unit_cost=(value / qty).quantize(COST_Q),
        value=value,
        movement_type=movement_type,
        source_doc_type=source_doc_type,
        source_doc_id=source_doc_id,
        source_doc_no=source_doc_no,
        source_line_id=source_line_id,
        reversal_of_id=reversal_of_id,
        created_by_id=actor_id,
    )
    session.add(movement)
    session.flush()
    return movement


def stock_out(
    session: Session,
    *,
    product_id: int,
    location_id: int,
    qty: Decimal,
    business_date: date,
    movement_type: str,
    source_doc_type: str,
    source_doc_id: int,
    source_doc_no: str | None = None,
    source_line_id: int | None = None,
    batch_id: int | None = None,
    actor_id: int | None = None,
    value: Decimal | None = None,
    reversal_of_id: int | None = None,
) -> InventoryMovement:
    """Remove `qty` base units. Value defaults to the current weighted average cost."""
    if qty <= 0:
        raise BusinessRuleError("Outbound stock needs a positive quantity.")
    cost = lock_costs(session, [product_id])[product_id]
    balance = session.scalar(
        select(StockBalance)
        .where(*_balance_filter(product_id, batch_id, location_id))
        .with_for_update(of=StockBalance)
        .execution_options(populate_existing=True)
    )
    if cost.qty_on_hand < qty or balance is None or balance.qty_base < qty:
        available = balance.qty_base if balance else ZERO
        raise BusinessRuleError(
            f"Insufficient stock for {_product_label(session, product_id)}: "
            f"need {qty:f}, available {available:f}.",
            code="insufficient_stock",
        )
    if value is None:
        value = (
            cost.total_value
            if qty == cost.qty_on_hand
            else round_money(cost.total_value * qty / cost.qty_on_hand)
        )
    else:
        value = round_money(value)
        if value > cost.total_value:
            raise ConflictError(
                f"Cannot remove value {value:f}; only {cost.total_value:f} on hand for "
                f"{_product_label(session, product_id)}.",
                code="stock_value_mismatch",
            )
    cost.qty_on_hand -= qty
    cost.total_value -= value
    _refresh_avg(cost)
    balance.qty_base -= qty

    movement = InventoryMovement(
        business_date=business_date,
        product_id=product_id,
        batch_id=batch_id,
        location_id=location_id,
        qty_base=-qty,
        unit_cost=(value / qty).quantize(COST_Q),
        value=-value,
        movement_type=movement_type,
        source_doc_type=source_doc_type,
        source_doc_id=source_doc_id,
        source_doc_no=source_doc_no,
        source_line_id=source_line_id,
        reversal_of_id=reversal_of_id,
        created_by_id=actor_id,
    )
    session.add(movement)
    session.flush()
    return movement


def reverse_movements(
    session: Session,
    *,
    source_doc_type: str,
    source_doc_id: int,
    business_date: date,
    actor_id: int | None,
) -> list[InventoryMovement]:
    """Mirror every movement of a document (same quantity and value, opposite sign).

    Refused when any later stock movement exists for one of the products: with a moving
    average cost, removing a past receipt after the average has been used would silently
    change historical cost. In that case use a purchase return or an adjustment.
    """
    originals = list(
        session.scalars(
            select(InventoryMovement)
            .where(
                InventoryMovement.source_doc_type == source_doc_type,
                InventoryMovement.source_doc_id == source_doc_id,
                InventoryMovement.reversal_of_id.is_(None),
            )
            .order_by(InventoryMovement.id)
        ).all()
    )
    if not originals:
        return []
    product_ids = {m.product_id for m in originals}
    lock_costs(session, product_ids)
    for product_id in sorted(product_ids):
        own_last = max(m.id for m in originals if m.product_id == product_id)
        latest = session.scalar(
            select(func.max(InventoryMovement.id)).where(InventoryMovement.product_id == product_id)
        )
        if latest != own_last:
            raise ConflictError(
                f"Cannot reverse: {_product_label(session, product_id)} has later stock "
                "movements. Use a purchase return or a stock adjustment instead.",
                code="stock_already_moved",
            )

    created: list[InventoryMovement] = []
    for original in reversed(originals):
        common = {
            "product_id": original.product_id,
            "location_id": original.location_id,
            "batch_id": original.batch_id,
            "business_date": business_date,
            "movement_type": "reversal",
            "source_doc_type": original.source_doc_type,
            "source_doc_id": original.source_doc_id,
            "source_doc_no": original.source_doc_no,
            "source_line_id": original.source_line_id,
            "actor_id": actor_id,
            "reversal_of_id": original.id,
        }
        if original.qty_base > 0:
            created.append(
                stock_out(session, qty=original.qty_base, value=original.value, **common)  # type: ignore[arg-type]
            )
        else:
            created.append(
                stock_in(session, qty=-original.qty_base, value=-original.value, **common)  # type: ignore[arg-type]
            )
    return created
