"""Consistency checks between the authoritative ledgers and their projections.

Run `python -m app.cli reconcile` (exit code 1 on drift). Also useful in tests, after any
sequence of business events: every check must come back clean.
"""

from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.modules.accounting.journal import INVENTORY
from app.modules.accounting.models import Account, JournalLine
from app.modules.inventory.models import InventoryMovement, ProductCost, StockBalance
from app.modules.inventory.schemas import ReconcileIssue, ReconcileRead
from app.modules.sales.rebuild import projection_drift


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

    # 5. the daily sales projection == the posted invoices and returns it summarises
    for detail in projection_drift(session):
        issues.append(ReconcileIssue(check="daily_sales", detail=detail))

    # 6. every ledger row's source document exists in this dealership (these references cannot
    #    be foreign keys; row-level security hides another dealership's documents)
    issues.extend(_missing_sources(session))

    return ReconcileRead(ok=not issues, issues=issues)


# source_doc_type -> table of the document. Other types are not checked.
SOURCE_TABLES = {
    "sales_invoice": "sales_invoices",
    "customer_receipt": "customer_receipts",
    "sales_return": "sales_returns",
    "GRN": "goods_receipts",
    "SPAY": "supplier_payments",
    "purchase_return": "purchase_returns",
    "OPENING": "opening_balances",
    "stock_adjustment": "stock_adjustments",
    "stock_transfer": "stock_transfers",
    "claim_settlement": "claim_settlements",
    "commission_payment": "commission_payments",
    "expense": "expenses",
    "fund_transfer": "fund_transfers",
    "owner_transaction": "owner_transactions",
}


def _missing_sources(session: Session) -> list[ReconcileIssue]:
    issues = []
    for ledger in ("journal_entries", "inventory_movements", "claimable_items"):
        for doc_type, table in SOURCE_TABLES.items():
            # Names come from the constants above, never from input.
            count = session.scalar(
                text(
                    f"SELECT count(*) FROM {ledger} l WHERE l.source_doc_type = :type "  # noqa: S608
                    f"AND NOT EXISTS (SELECT 1 FROM {table} d "
                    "WHERE d.id = l.source_doc_id AND d.tenant_id = l.tenant_id)"
                ),
                {"type": doc_type},
            )
            if count:
                issues.append(
                    ReconcileIssue(
                        check="missing_source",
                        detail=f"{count} {ledger} rows name a {doc_type} that does not exist",
                    )
                )
    return issues
