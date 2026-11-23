"""Stock adjustments, write-offs and transfers between locations."""

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import Shop, account_totals, key, stock_at

D = Decimal


@pytest.fixture
def shop(client: TestClient, owner: dict[str, str], db: Session) -> Shop:
    return Shop(client, owner, db)


def stocked(shop: Shop, qty: str = "100", **kwargs: Any) -> dict[str, Any]:
    product = shop.product(**kwargs)
    shop.receive(product, qty, price="10")
    return product


def adjust(
    shop: Shop, reason: str, lines: list[dict[str, Any]], *, post: bool = True, expect: int = 201
) -> Any:
    doc = shop.post("/api/v1/stock-adjustments", {"reason": reason, "items": lines}, expect=expect)
    if post and expect == 201:
        doc = shop.post(f"/api/v1/stock-adjustments/{doc['id']}/post", idem=True)
    return doc


def test_a_count_loss_removes_stock_at_cost_and_books_shrinkage(shop: Shop, db: Session) -> None:
    product = stocked(shop)
    doc = adjust(shop, "count", [{"product_id": product["id"], "qty_delta": "-3"}])

    assert doc["status"] == "posted" and doc["doc_no"].startswith("ADJ-")
    assert doc["items"][0]["value"] == "-30.00"
    assert stock_at(db, product["id"], "SELLABLE") == D(97)
    totals = account_totals(db, "stock_adjustment", doc["id"])
    assert totals["5400"] == (D(30), D(0))
    assert totals["1200"] == (D(0), D(30))
    assert shop.reconcile()["ok"] is True


def test_a_count_gain_is_valued_at_the_average_cost(shop: Shop, db: Session) -> None:
    product = stocked(shop)
    doc = adjust(shop, "found", [{"product_id": product["id"], "qty_delta": "5"}])

    assert doc["items"][0]["value"] == "50.00"
    assert stock_at(db, product["id"], "SELLABLE") == D(105)
    totals = account_totals(db, "stock_adjustment", doc["id"])
    assert totals["1200"] == (D(50), D(0))
    assert totals["5400"] == (D(0), D(50))
    assert shop.reconcile()["ok"] is True


def test_damage_and_expiry_are_written_off_not_booked_as_shrinkage(shop: Shop, db: Session) -> None:
    product = stocked(shop)
    doc = adjust(shop, "damage", [{"product_id": product["id"], "qty_delta": "-4"}])
    totals = account_totals(db, "stock_adjustment", doc["id"])
    assert totals["5100"] == (D(40), D(0))
    assert "5400" not in totals


def test_a_loss_larger_than_stock_is_refused_and_leaves_stock_alone(
    shop: Shop, db: Session
) -> None:
    product = stocked(shop, "5")
    doc = adjust(shop, "count", [{"product_id": product["id"], "qty_delta": "-6"}], post=False)
    refused = shop.post_raw(f"/api/v1/stock-adjustments/{doc['id']}/post")
    assert refused.status_code == 422 and refused.json()["code"] == "insufficient_stock"
    assert stock_at(db, product["id"], "SELLABLE") == D(5)


def test_adjustment_lines_are_validated(shop: Shop) -> None:
    plain = stocked(shop)
    tracked = shop.product(track_batch=True, track_expiry=True)
    shop.receive(tracked, "10", price="10", batch="B1", expiry_days=30)
    zero = adjust(shop, "count", [{"product_id": plain["id"], "qty_delta": "0"}], expect=422)
    assert zero["errors"][0]["field"] == "items.0.qty_delta"
    unbatched = adjust(
        shop, "count", [{"product_id": tracked["id"], "qty_delta": "-1"}], expect=422
    )
    assert unbatched["errors"][0]["field"] == "items.0.batch_id"


def test_a_batch_tracked_adjustment_hits_the_chosen_batch(shop: Shop, db: Session) -> None:
    product = shop.product(track_batch=True, track_expiry=True)
    shop.receive(product, "20", price="10", batch="B1", expiry_days=30)
    batch_id = shop.get("/api/v1/inventory/batches", product_id=product["id"])[0]["id"]
    adjust(shop, "expiry", [{"product_id": product["id"], "batch_id": batch_id, "qty_delta": "-5"}])
    assert stock_at(db, product["id"], "SELLABLE") == D(15)


# --- Transfers ------------------------------------------------------------------------------------
def test_a_transfer_moves_stock_between_locations_without_touching_the_books(
    shop: Shop, db: Session
) -> None:
    product = stocked(shop)
    doc = shop.post(
        "/api/v1/stock-transfers",
        {
            "from_location": "SELLABLE",
            "to_location": "EXPIRED",
            "items": [{"product_id": product["id"], "qty_base": "10"}],
        },
        expect=201,
    )
    posted = shop.post(f"/api/v1/stock-transfers/{doc['id']}/post", idem=True)

    assert posted["status"] == "posted" and posted["doc_no"].startswith("TRF-")
    assert posted["items"][0]["value"] == "100.00"  # cost travels with the goods
    assert stock_at(db, product["id"], "SELLABLE") == D(90)
    assert stock_at(db, product["id"], "EXPIRED") == D(10)
    assert account_totals(db, "stock_transfer", doc["id"]) == {}
    assert shop.reconcile()["ok"] is True


def test_a_transfer_needs_two_different_locations_and_enough_stock(shop: Shop) -> None:
    product = stocked(shop, "5")
    line = [{"product_id": product["id"], "qty_base": "6"}]
    same = shop.post(
        "/api/v1/stock-transfers",
        {"from_location": "SELLABLE", "to_location": "SELLABLE", "items": line},
        expect=422,
    )
    assert same["errors"][0]["field"] == "to_location"
    doc = shop.post(
        "/api/v1/stock-transfers",
        {"from_location": "SELLABLE", "to_location": "DAMAGED", "items": line},
        expect=201,
    )
    refused = shop.post_raw(f"/api/v1/stock-transfers/{doc['id']}/post")
    assert refused.status_code == 422


# --- Permissions and idempotency -----------------------------------------------------------------
def test_only_roles_with_inventory_adjust_can_post_stock_documents(
    shop: Shop, client: TestClient, cashier: dict[str, str], storekeeper: dict[str, str]
) -> None:
    product = stocked(shop)
    body = {"reason": "count", "items": [{"product_id": product["id"], "qty_delta": "-1"}]}
    assert client.post("/api/v1/stock-adjustments", headers=cashier, json=body).status_code == 403
    assert (
        client.post("/api/v1/stock-adjustments", headers=storekeeper, json=body).status_code == 201
    )


def test_posting_an_adjustment_twice_with_one_key_adjusts_once(shop: Shop, db: Session) -> None:
    product = stocked(shop)
    doc = adjust(shop, "count", [{"product_id": product["id"], "qty_delta": "-2"}], post=False)
    headers = {**shop.headers, "Idempotency-Key": key()["Idempotency-Key"]}
    url = f"/api/v1/stock-adjustments/{doc['id']}/post"
    first = shop.client.post(url, headers=headers)
    replay = shop.client.post(url, headers=headers)
    assert first.status_code == 200 and replay.json() == first.json()
    assert stock_at(db, product["id"], "SELLABLE") == D(98)


def test_drafts_can_be_deleted_but_posted_documents_cannot(shop: Shop) -> None:
    product = stocked(shop)
    draft = adjust(shop, "count", [{"product_id": product["id"], "qty_delta": "1"}], post=False)
    assert (
        shop.client.delete(
            f"/api/v1/stock-adjustments/{draft['id']}", headers=shop.headers
        ).status_code
        == 204
    )
    posted = adjust(shop, "count", [{"product_id": product["id"], "qty_delta": "1"}])
    assert (
        shop.client.delete(
            f"/api/v1/stock-adjustments/{posted['id']}", headers=shop.headers
        ).status_code
        == 409
    )
