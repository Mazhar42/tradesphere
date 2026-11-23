"""Duplicate-submit protection and period lock, exercised through real posting endpoints."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.purchasing.models import GoodsReceipt, SupplierPayment
from tests.helpers import D, Shop, today


@pytest.fixture
def shop(client: TestClient, owner: dict[str, str], db: Session) -> Shop:
    return Shop(client, owner, db)


def _payment_body(shop: Shop, supplier: int, amount: str = "1000") -> dict:
    cash = next(a for a in shop.get("/api/v1/payment-accounts") if a["name"] == "Cash in Hand")
    return {"supplier_id": supplier, "amount": amount, "payment_account_id": cash["id"]}


def test_same_key_twice_posts_once_and_returns_the_same_response(shop: Shop, db: Session) -> None:
    supplier = shop.supplier()
    body = _payment_body(shop, supplier)
    headers = {**shop.headers, "Idempotency-Key": "double-click-0001"}
    first = shop.client.post("/api/v1/supplier-payments", headers=headers, json=body)
    second = shop.client.post("/api/v1/supplier-payments", headers=headers, json=body)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()  # identical response, same document number
    assert "idempotent-replayed" not in first.headers
    assert second.headers["idempotent-replayed"] == "true"

    count = db.scalar(
        select(func.count())
        .select_from(SupplierPayment)
        .where(SupplierPayment.supplier_id == supplier)
    )
    assert count == 1
    assert D(shop.supplier_summary(supplier)["ledger_balance"]) == D("1000")  # paid once, not twice


def test_same_key_with_a_different_request_is_a_conflict(shop: Shop) -> None:
    supplier = shop.supplier()
    headers = {**shop.headers, "Idempotency-Key": "reused-key-000001"}
    ok = shop.client.post(
        "/api/v1/supplier-payments", headers=headers, json=_payment_body(shop, supplier, "500")
    )
    assert ok.status_code == 201
    clash = shop.client.post(
        "/api/v1/supplier-payments", headers=headers, json=_payment_body(shop, supplier, "900")
    )
    assert clash.status_code == 409 and clash.json()["code"] == "idempotency_key_reuse"
    assert D(shop.supplier_summary(supplier)["ledger_balance"]) == D("500")


def test_a_failed_request_releases_its_key_so_the_user_can_retry(shop: Shop) -> None:
    supplier = shop.supplier()
    headers = {**shop.headers, "Idempotency-Key": "retry-after-failure-1"}
    bad = {**_payment_body(shop, supplier), "payment_account_id": 999999}
    failed = shop.client.post("/api/v1/supplier-payments", headers=headers, json=bad)
    assert failed.status_code == 404
    fixed = shop.client.post(
        "/api/v1/supplier-payments", headers=headers, json=_payment_body(shop, supplier)
    )
    assert fixed.status_code == 201  # not blocked by the failed attempt


def test_posting_endpoints_require_the_header(shop: Shop) -> None:
    supplier = shop.supplier()
    response = shop.client.post(
        "/api/v1/supplier-payments", headers=shop.headers, json=_payment_body(shop, supplier)
    )
    assert response.status_code == 422
    assert any(
        "Idempotency-Key" in e["field"] or "idempotency" in e["field"].lower()
        for e in response.json()["errors"]
    )


def test_double_posting_a_receipt_with_one_key_creates_one_posting(shop: Shop, db: Session) -> None:
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "10", "10")])
    grn = shop.grn(po, [{"received_qty_base": "5"}], post=False)
    headers = {**shop.headers, "Idempotency-Key": "grn-post-key-0001"}
    first = shop.client.post(f"/api/v1/goods-receipts/{grn['id']}/post", headers=headers)
    second = shop.client.post(f"/api/v1/goods-receipts/{grn['id']}/post", headers=headers)
    assert first.status_code == second.status_code == 200 and first.json() == second.json()
    assert shop.stock(product)["qty_total"] == "5.000"  # stock added once
    posted = db.scalar(
        select(func.count())
        .select_from(GoodsReceipt)
        .where(GoodsReceipt.status == "posted", GoodsReceipt.id == grn["id"])
    )
    assert posted == 1


# --- period lock ----------------------------------------------------------------------------------
def _lock(shop: Shop, on: str | None) -> None:
    response = shop.client.put(
        "/api/v1/settings/period-lock", headers=shop.headers, json={"lock_date": on}
    )
    assert response.status_code == 200


def test_period_lock_blocks_backdated_documents_but_not_current_ones(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "100", "10")])
    old = (today() - timedelta(days=10)).isoformat()
    _lock(shop, (today() - timedelta(days=5)).isoformat())

    # Backdated into the closed period: refused for payments and receipts.
    refused = shop.pay(supplier, "100", business_date=old, expect=409)
    assert refused["code"] == "period_locked"
    grn = shop.grn(po, [{"received_qty_base": "10"}], post=False, business_date=old)
    response = shop.post_raw(f"/api/v1/goods-receipts/{grn['id']}/post")
    assert response.status_code == 409 and response.json()["code"] == "period_locked"
    assert shop.stock(product)["qty_total"] == "0.000"  # nothing leaked through

    # Today's work continues as normal.
    shop.pay(supplier, "100")
    shop.grn(po, [{"received_qty_base": "10"}])
    assert shop.stock(product)["qty_total"] == "10.000"

    _lock(shop, None)  # unlocking allows the backdated receipt again
    shop.post(f"/api/v1/goods-receipts/{grn['id']}/post", idem=True)
    assert shop.stock(product)["qty_total"] == "20.000"


def test_reversing_a_document_from_a_closed_period_is_refused(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "10", "10")])
    old = (today() - timedelta(days=10)).isoformat()
    grn = shop.grn(po, [{"received_qty_base": "10"}], business_date=old)
    payment = shop.pay(supplier, "50", business_date=old)
    _lock(shop, (today() - timedelta(days=5)).isoformat())
    for url in (
        f"/api/v1/goods-receipts/{grn['id']}/reverse",
        f"/api/v1/supplier-payments/{payment['id']}/reverse",
    ):
        response = shop.post_raw(url)
        assert response.status_code == 409 and response.json()["code"] == "period_locked"
    assert shop.stock(product)["qty_total"] == "10.000"
