"""The books and the stock projections must agree after any sequence of events."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.inventory.reconcile import reconcile
from tests.helpers import D, Shop, in_days


@pytest.fixture
def shop(client: TestClient, owner: dict[str, str], db: Session) -> Shop:
    return Shop(client, owner, db)


def _busy_day(shop: Shop) -> None:
    supplier = shop.supplier()
    a = shop.product(track_batch=True, track_expiry=True)
    b = shop.product(carton=12)
    po1 = shop.po(
        supplier,
        [shop.line(a, "500", "33.33"), shop.line(b, "20", "240", uom_id=shop.uom_id("CTN"))],
    )
    po2 = shop.po(supplier, [shop.line(b, "5", "250", uom_id=shop.uom_id("CTN"))])
    shop.pay(supplier, "10000", allocations=[{"po_id": po1["id"], "amount": "10000"}])
    shop.grn(
        po1,
        [
            {
                "item": 0,
                "received_qty_base": "200",
                "damaged_qty_base": "3",
                "free_qty_base": "20",
                "batch_no": "R1",
                "expiry_date": in_days(90),
            },
            {"item": 1, "received_qty_base": "100"},
        ],
    )
    shop.grn(
        po1,
        [{"item": 0, "received_qty_base": "300", "batch_no": "R2", "expiry_date": in_days(150)}],
    )
    grn = shop.grn(po2, [{"received_qty_base": "60"}])
    shop.post(f"/api/v1/goods-receipts/{grn['id']}/reverse", idem=True)
    shop.post(f"/api/v1/purchase-orders/{po1['id']}/close-short")


def test_reconcile_is_clean_after_a_busy_day(shop: Shop, db: Session) -> None:
    _busy_day(shop)
    result = reconcile(db)
    assert result.ok, result.issues
    report = shop.get("/api/v1/reports/trial-balance")
    assert report["balanced"] is True and D(report["total_debit"]) > 0


def test_reconcile_detects_a_tampered_stock_balance(shop: Shop, db: Session) -> None:
    _busy_day(shop)
    db.execute(text("UPDATE stock_balances SET qty_base = qty_base + 1 WHERE qty_base > 0"))
    db.flush()
    result = reconcile(db)
    assert not result.ok
    checks = {i.check for i in result.issues}
    assert "stock_balance" in checks and "product_cost" not in checks


def test_reconcile_detects_a_tampered_cost_row_and_inventory_account(
    shop: Shop, db: Session
) -> None:
    _busy_day(shop)
    db.execute(text("UPDATE product_costs SET total_value = total_value + 5 WHERE qty_on_hand > 0"))
    db.flush()
    checks = {i.check for i in reconcile(db).issues}
    assert "product_cost" in checks and "inventory_account" in checks


def test_reconcile_endpoint_requires_finance_permission(
    client: TestClient, storekeeper: dict[str, str]
) -> None:
    assert client.get("/api/v1/inventory/reconcile", headers=storekeeper).status_code == 403
