"""Consistency checks between the authoritative ledgers and their projections.

Run `python -m app.cli reconcile` (exit code 1 on drift). Also useful in tests, after any
sequence of business events: every check must come back clean.
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounting.journal import INVENTORY
from app.modules.accounting.models import Account, JournalLine
from app.modules.inventory.models import InventoryMovement, ProductCost, StockBalance
from app.modules.inventory.schemas import ReconcileIssue, ReconcileRead


def reconcile(session: Session) -> ReconcileRead:
    issues: list[ReconcileIssue] = []

    # 1. stock_balances == sum of movements per (product, batch, location)
    moved = {
        (p, b, loc): Decimal(q)
        for p, b, loc, q in session.execute(
            select(
                InventoryMovement.product_id,
                InventoryMovement.batch_id,
                InventoryMovement.location_id,
                func.sum(InventoryMovement.qty_base),
            ).group_by(
                InventoryMovement.product_id,
                InventoryMovement.batch_id,
                InventoryMovement.location_id,
            )
        )
    }
    held = {
        (b.product_id, b.batch_id, b.location_id): b.qty_base
        for b in session.scalars(select(StockBalance)).all()
    }
    for key in sorted(set(moved) | set(held), key=str):
        if moved.get(key, Decimal(0)) != held.get(key, Decimal(0)):
            issues.append(
                ReconcileIssue(
                    check="stock_balance",
                    detail=(
                        f"product/batch/location {key}: movements {moved.get(key, 0)} "
                        f"!= balance {held.get(key, 0)}"
                    ),
                )
            )

    # 2. product_costs quantity and value == sum of movements per product
    totals = {
        p: (Decimal(q), Decimal(v))
        for p, q, v in session.execute(
            select(
                InventoryMovement.product_id,
                func.sum(InventoryMovement.qty_base),
                func.sum(InventoryMovement.value),
            ).group_by(InventoryMovement.product_id)
        )
    }
    costs = {c.product_id: c for c in session.scalars(select(ProductCost)).all()}
    for product_id in sorted(set(totals) | set(costs)):
        q, v = totals.get(product_id, (Decimal(0), Decimal(0)))
        cost = costs.get(product_id)
        cq = cost.qty_on_hand if cost else Decimal(0)
        cv = cost.total_value if cost else Decimal(0)
        if (q, v) != (cq, cv):
            issues.append(
                ReconcileIssue(
                    check="product_cost",
                    detail=f"product {product_id}: movements ({q}, {v}) != cost row ({cq}, {cv})",
                )
            )

    # 3. Inventory control account == total stock value
    gl = Decimal(
        session.scalar(
            select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
            .join(Account, Account.id == JournalLine.account_id)
            .where(Account.code == INVENTORY)
        )
        or 0
    )
    stock_value = sum((c.total_value for c in costs.values()), Decimal(0))
    if gl != stock_value:
        issues.append(
            ReconcileIssue(
                check="inventory_account",
                detail=f"Inventory account {gl} != stock value {stock_value}",
            )
        )

    # 4. the books balance
    debit, credit = session.execute(
        select(
            func.coalesce(func.sum(JournalLine.debit), 0),
            func.coalesce(func.sum(JournalLine.credit), 0),
        )
    ).one()
    if Decimal(debit) != Decimal(credit):
        issues.append(
            ReconcileIssue(check="trial_balance", detail=f"debits {debit} != credits {credit}")
        )

    return ReconcileRead(ok=not issues, issues=issues)
