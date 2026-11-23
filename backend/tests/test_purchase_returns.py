"""Purchase returns: goods sent back, credited to the supplier account or claimed."""

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.claims.models import ClaimableItem
from tests.helpers import Shop, account_totals, stock_at

D = Decimal


@pytest.fixture
def shop(client: TestClient, owner: dict[str, str], db: Session) -> Shop:
    return Shop(client, owner, db)


def send_back(
    shop: Shop, supplier: int, lines: list[dict[str, Any]], *, post: bool = True, expect: int = 201
) -> Any:
    doc = shop.post(
        "/api/v1/purchase-returns", {"supplier_id": supplier, "items": lines}, expect=expect
    )
    if post and expect == 201:
        doc = shop.post(f"/api/v1/purchase-returns/{doc['id']}/post", idem=True)
    return doc


def stocked(shop: Shop, qty: str = "100") -> tuple[dict[str, Any], int]:
    product = shop.product()
    supplier = shop.supplier()
    po = shop.po(supplier, [shop.line(product, qty, "10")])
    shop.grn(po, [{"received_qty_base": qty}])
    return product, supplier


def test_returned_goods_credit_the_supplier_and_book_the_difference_as_income(
    shop: Shop, db: Session
) -> None:
    product, supplier = stocked(shop)
    doc = send_back(
        shop, supplier, [{"product_id": product["id"], "qty_base": "10", "unit_value": "12"}]
    )

    assert doc["status"] == "posted" and doc["doc_no"].startswith("PR-")
    assert (doc["total_value"], doc["total_cost"]) == ("120.00", "100.00")
    assert stock_at(db, product["id"], "SELLABLE") == D(90)
    totals = account_totals(db, "purchase_return", doc["id"])
    assert totals["2000"] == (D(120), D(0))  # payable to the company goes down
    assert totals["1200"] == (D(0), D(100))
    assert totals["4300"] == (D(0), D(20))  # credited above the book cost
    assert shop.reconcile()["ok"] is True


def test_a_credit_below_cost_is_written_off(shop: Shop, db: Session) -> None:
    product, supplier = stocked(shop)
    doc = send_back(
        shop, supplier, [{"product_id": product["id"], "qty_base": "10", "unit_value": "8"}]
    )
    totals = account_totals(db, "purchase_return", doc["id"])
    assert totals["2000"] == (D(80), D(0))
    assert totals["5300"] == (D(20), D(0))
    assert totals["1200"] == (D(0), D(100))


def test_damaged_goods_returned_for_a_claim_become_a_claimable_item(
    shop: Shop, db: Session
) -> None:
    product = shop.product()
    supplier = shop.supplier()
    po = shop.po(supplier, [shop.line(product, "20", "10")])
    shop.grn(po, [{"received_qty_base": "20", "damaged_qty_base": "5"}])
    assert stock_at(db, product["id"], "DAMAGED") == D(5)

    doc = send_back(
        shop,
        supplier,
        [
            {
                "product_id": product["id"],
                "location": "DAMAGED",
                "qty_base": "5",
                "unit_value": "6",
                "settlement": "claim",
            }
        ],
    )

    assert stock_at(db, product["id"], "DAMAGED") == D(0)
    totals = account_totals(db, "purchase_return", doc["id"])
    assert totals["1300"] == (D(30), D(0))  # claims receivable, not the payable
    assert "2000" not in totals
    item = db.scalar(select(ClaimableItem).where(ClaimableItem.source_doc_id == doc["id"]))
    assert item is not None
    assert (item.claim_type, item.book_amount, item.claim_amount, item.status) == (
        "damage",
        D("30.00"),
        D("30.00"),
        "open",
    )


def test_returning_more_than_is_on_hand_is_refused_and_changes_nothing(
    shop: Shop, db: Session
) -> None:
    product, supplier = stocked(shop, "5")
    doc = send_back(
        shop,
        supplier,
        [{"product_id": product["id"], "qty_base": "6", "unit_value": "10"}],
        post=False,
    )
    refused = shop.post_raw(f"/api/v1/purchase-returns/{doc['id']}/post")
    assert refused.status_code == 422 and refused.json()["code"] == "insufficient_stock"
    assert stock_at(db, product["id"], "SELLABLE") == D(5)


def test_purchase_returns_need_purchasing_write(
    shop: Shop, client: TestClient, cashier: dict[str, str], storekeeper: dict[str, str]
) -> None:
    product, supplier = stocked(shop)
    body = {
        "supplier_id": supplier,
        "items": [{"product_id": product["id"], "qty_base": "1", "unit_value": "10"}],
    }
    assert client.post("/api/v1/purchase-returns", headers=cashier, json=body).status_code == 403
    assert (
        client.post("/api/v1/purchase-returns", headers=storekeeper, json=body).status_code == 201
    )
