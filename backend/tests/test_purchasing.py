"""Phase 2 acceptance: the dealership's purchasing story, end to end through the API."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import D, Shop, in_days, today


@pytest.fixture
def shop(client: TestClient, owner: dict[str, str], db: Session) -> Shop:
    return Shop(client, owner, db)


# --- The owner's scenario ----------------------------------------------------------------
def test_one_million_po_paid_in_full_received_in_two_shipments(shop: Shop) -> None:
    """Tk 1M PO paid up front; Tk 600k then Tk 200k delivered -> Tk 200k still to come."""
    supplier = shop.supplier()
    product = shop.product()
    po = shop.po(supplier, [shop.line(product, "10000", "100")])
    assert po["doc_no"].startswith("PO-") and po["status"] == "confirmed"
    assert D(po["summary"]["ordered_value"]) == D("1000000")

    # Money goes to the company before any goods arrive: it is an advance, not stock.
    shop.pay(supplier, "1000000", allocations=[{"po_id": po["id"], "amount": "1000000"}])
    detail = shop.po_detail(po["id"])
    assert D(detail["summary"]["paid_value"]) == D("1000000")
    assert D(detail["summary"]["unapplied_advance"]) == D("1000000")
    assert D(detail["summary"]["received_value"]) == 0
    assert shop.stock(product)["qty_total"] == "0.000"

    # First shipment: 6,000 units = Tk 600,000.
    shop.grn(po, [{"received_qty_base": "6000"}])
    detail = shop.po_detail(po["id"])
    assert detail["status"] == "partially_received"
    assert D(detail["summary"]["received_value"]) == D("600000")
    assert D(detail["summary"]["remaining_value"]) == D("400000")
    assert shop.stock(product)["qty_total"] == "6000.000"

    # Second shipment: 2,000 units = Tk 200,000.
    shop.grn(po, [{"received_qty_base": "2000"}])
    detail = shop.po_detail(po["id"])
    assert detail["status"] == "partially_received"
    summary = detail["summary"]
    assert D(summary["ordered_value"]) == D("1000000")
    assert D(summary["received_value"]) == D("800000")
    assert D(summary["remaining_value"]) == D("200000")  # goods still undelivered
    assert D(summary["paid_value"]) == D("1000000")
    assert D(summary["unapplied_advance"]) == D("200000")  # paid for, not yet delivered
    assert D(summary["balance_due"]) == 0
    assert len(detail["receipts"]) == 2

    # Supplier account: paid 1,000,000, received 800,000 -> 200,000 advance with the company.
    account = shop.supplier_summary(supplier)
    assert D(account["ledger_balance"]) == D("200000")
    assert account["position"] == "advance"
    assert D(account["outstanding_po_value"]) == D("200000")

    # Stock is valued at the PO price.
    row = shop.stock(product)
    assert D(row["value"]) == D("800000") and D(row["avg_cost"]) == D("100")
    assert shop.reconcile()["ok"] is True


def test_a_second_po_before_the_first_is_complete_stays_separate(shop: Shop) -> None:
    supplier = shop.supplier()
    a, b = shop.product(), shop.product()
    po1 = shop.po(supplier, [shop.line(a, "1000", "100")])  # 100,000
    po2 = shop.po(supplier, [shop.line(b, "500", "200")])  # 100,000
    shop.pay(supplier, "100000", allocations=[{"po_id": po1["id"], "amount": "100000"}])
    shop.grn(po1, [{"received_qty_base": "600"}])

    d1, d2 = shop.po_detail(po1["id"]), shop.po_detail(po2["id"])
    assert D(d1["summary"]["received_value"]) == D("60000")
    assert D(d1["summary"]["remaining_value"]) == D("40000")
    assert D(d1["summary"]["unapplied_advance"]) == D("40000")
    # PO-2 is untouched: nothing paid, nothing received, all of it outstanding.
    assert D(d2["summary"]["paid_value"]) == 0 and D(d2["summary"]["received_value"]) == 0
    assert D(d2["summary"]["remaining_value"]) == D("100000")
    assert D(d2["summary"]["unapplied_advance"]) == 0

    shop.grn(po2, [{"received_qty_base": "500"}])
    d2 = shop.po_detail(po2["id"])
    assert d2["status"] == "received"
    assert D(d2["summary"]["balance_due"]) == D("100000")  # received but unpaid
    # Receiving PO-2 did not touch PO-1's outstanding quantity.
    assert D(shop.po_detail(po1["id"])["summary"]["remaining_value"]) == D("40000")
    # Supplier account: paid 100k, received 160k -> we owe 60k.
    account = shop.supplier_summary(supplier)
    assert D(account["ledger_balance"]) == D("-60000") and account["position"] == "payable"
    assert D(account["payable"]) == D("60000")


def test_receiving_more_than_undelivered_is_rejected(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "100", "10")])
    shop.grn(po, [{"received_qty_base": "60"}])
    response = shop.post_raw(
        "/api/v1/goods-receipts",
        {
            "po_id": po["id"],
            "items": [{"po_item_id": po["items"][0]["id"], "received_qty_base": "41"}],
        },
        idem=False,
    )
    assert response.status_code == 422 and response.json()["code"] == "over_receipt"
    shop.grn(po, [{"received_qty_base": "40"}])  # exactly the remainder is fine
    assert shop.po_detail(po["id"])["status"] == "received"


def test_two_lines_in_one_shipment_cannot_together_exceed_the_remainder(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product(track_batch=True, track_expiry=True)
    po = shop.po(supplier, [shop.line(product, "100", "10")])
    item = po["items"][0]["id"]
    lines = [
        {
            "po_item_id": item,
            "received_qty_base": "60",
            "batch_no": "A",
            "expiry_date": in_days(200),
        },
        {
            "po_item_id": item,
            "received_qty_base": "60",
            "batch_no": "B",
            "expiry_date": in_days(200),
        },
    ]
    response = shop.post_raw(
        "/api/v1/goods-receipts", {"po_id": po["id"], "items": lines}, idem=False
    )
    assert response.status_code == 422 and response.json()["code"] == "over_receipt"


def test_goods_cannot_be_received_against_an_unconfirmed_po(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "10", "10")], confirm=False)
    assert po["doc_no"] is None and po["status"] == "draft"
    response = shop.post_raw(
        "/api/v1/goods-receipts",
        {
            "po_id": po["id"],
            "items": [{"po_item_id": po["items"][0]["id"], "received_qty_base": "1"}],
        },
        idem=False,
    )
    assert response.status_code == 409 and response.json()["code"] == "po_not_receivable"


def test_each_shipment_has_a_gapless_number_and_posts_once(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "100", "10")])
    g1 = shop.grn(po, [{"received_qty_base": "10"}])
    g2 = shop.grn(po, [{"received_qty_base": "10"}])
    n1, n2 = (int(g["doc_no"].rsplit("-", 1)[1]) for g in (g1, g2))
    assert n2 == n1 + 1
    again = shop.post_raw(f"/api/v1/goods-receipts/{g1['id']}/post")
    assert again.status_code == 409 and again.json()["code"] == "not_draft"


# --- Units and prices --------------------------------------------------------------------------
def test_cartons_are_converted_to_base_units_and_prices_are_per_piece(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product(carton=24)
    carton = shop.uom_id("CTN")
    po = shop.po(
        supplier,
        [shop.line(product, "10", "480", uom_id=carton, free_qty="1")],  # 10 ctn @ 480 + 1 free
    )
    item = po["items"][0]
    assert item["ordered_qty_base"] == "240.000" and item["free_qty_base"] == "24.000"
    assert D(item["unit_cost"]) == D("20") and D(item["line_value"]) == D("4800")
    assert item["uom_code"] == "CTN" and D(item["factor"]) == 24


def test_unconfigured_unit_is_rejected(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product()  # no carton configured
    response = shop.post_raw(
        "/api/v1/purchase-orders",
        {
            "supplier_id": supplier,
            "items": [shop.line(product, "1", "1", uom_id=shop.uom_id("CTN"))],
        },
        idem=False,
    )
    assert response.status_code == 422


def test_partial_receipts_value_exactly_even_with_awkward_prices(shop: Shop) -> None:
    """Receipts of 1/3 each must add up to the exact PO value (no lost paisa)."""
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "3", "33.33")])  # 99.99
    for _ in range(3):
        shop.grn(po, [{"received_qty_base": "1"}])
    summary = shop.po_detail(po["id"])["summary"]
    assert D(summary["received_value"]) == D("99.99") and D(summary["remaining_value"]) == 0
    assert D(shop.stock(product)["value"]) == D("99.99")
    assert shop.reconcile()["ok"] is True


# --- Free goods, damaged goods, batches ---------------------------------------------------------
def test_free_goods_increase_quantity_and_lower_the_average_cost(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "100", "1000")])
    shop.grn(po, [{"received_qty_base": "100", "free_qty_base": "100"}])
    row = shop.stock(product)
    assert row["qty_total"] == "200.000"
    assert D(row["value"]) == D("100000")  # free goods add no value...
    assert D(row["avg_cost"]) == D("500")  # ...so the average halves
    # Only the 100 paid units were charged to the supplier account.
    assert D(shop.supplier_summary(supplier)["ledger_balance"]) == D("-100000")


def test_damaged_units_go_to_the_damaged_location_not_sellable_stock(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "100", "10")])
    shop.grn(po, [{"received_qty_base": "100", "damaged_qty_base": "7"}])
    row = shop.stock(product)
    assert row["qty_sellable"] == "93.000" and row["qty_damaged"] == "7.000"
    assert row["qty_total"] == "100.000"
    assert D(row["value"]) == D("1000")  # the company still invoiced all 100
    assert shop.reconcile()["ok"] is True


def test_batch_and_expiry_are_required_when_tracked(shop: Shop) -> None:
    supplier = shop.supplier()
    product = shop.product(track_batch=True, track_expiry=True)
    po = shop.po(supplier, [shop.line(product, "10", "10")])
    item = po["items"][0]["id"]

    def attempt(**fields: str) -> tuple[int, str]:
        grn = shop.post(
            "/api/v1/goods-receipts",
            {
                "po_id": po["id"],
                "items": [{"po_item_id": item, "received_qty_base": "5", **fields}],
            },
            expect=201,
        )
        response = shop.post_raw(f"/api/v1/goods-receipts/{grn['id']}/post")
        shop.client.delete(f"/api/v1/goods-receipts/{grn['id']}", headers=shop.headers)
        return response.status_code, response.json().get("code", "")

    assert attempt() == (422, "batch_required")
    assert attempt(batch_no="B1") == (422, "expiry_required")
    assert attempt(batch_no="B1", expiry_date=today().isoformat()) == (422, "already_expired")
    shop.grn(po, [{"received_qty_base": "5", "batch_no": "B1", "expiry_date": in_days(180)}])

    # Same batch number with a different expiry is a data-entry error.
    clash = shop.post(
        "/api/v1/goods-receipts",
        {
            "po_id": po["id"],
            "items": [
                {
                    "po_item_id": item,
                    "received_qty_base": "5",
                    "batch_no": "B1",
                    "expiry_date": in_days(300),
                }
            ],
        },
        expect=201,
    )
    response = shop.post_raw(f"/api/v1/goods-receipts/{clash['id']}/post")
    assert response.status_code == 422 and response.json()["code"] == "batch_expiry_mismatch"


def test_batches_appear_in_stock_with_earliest_expiry_first(shop: Shop) -> None:
    supplier = shop.supplier()
    product = shop.product(track_batch=True, track_expiry=True)
    po = shop.po(supplier, [shop.line(product, "100", "10")])
    shop.grn(
        po,
        [
            {"received_qty_base": "30", "batch_no": "LATE", "expiry_date": in_days(300)},
            {"received_qty_base": "20", "batch_no": "SOON", "expiry_date": in_days(40)},
        ],
    )
    rows = shop.get("/api/v1/inventory/stock/batches", product_id=product["id"])["items"]
    assert [r["batch_no"] for r in rows] == ["SOON", "LATE"]
    assert rows[0]["days_to_expiry"] == 40
    near = shop.get(
        "/api/v1/inventory/stock/batches", product_id=product["id"], expiring_within_days=60
    )["items"]
    assert [r["batch_no"] for r in near] == ["SOON"]


# --- Closing, cancelling, reversing -------------------------------------------------------------
def test_closing_short_cancels_the_rest_and_keeps_the_money_as_advance(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "1000", "100")])  # 100,000
    shop.pay(supplier, "100000", allocations=[{"po_id": po["id"], "amount": "100000"}])
    shop.grn(po, [{"received_qty_base": "600"}])

    closed = shop.post(f"/api/v1/purchase-orders/{po['id']}/close-short")
    assert closed["status"] == "closed_short"
    summary = closed["summary"]
    assert D(summary["cancelled_value"]) == D("40000") and D(summary["remaining_value"]) == 0
    assert D(summary["unapplied_advance"]) == D("40000")  # still with the company
    assert closed["items"][0]["cancelled_qty_base"] == "400.000"
    # Nothing more can be received or paid against it.
    response = shop.post_raw(
        "/api/v1/goods-receipts",
        {
            "po_id": po["id"],
            "items": [{"po_item_id": po["items"][0]["id"], "received_qty_base": "1"}],
        },
        idem=False,
    )
    assert response.status_code == 409
    assert D(shop.supplier_summary(supplier)["ledger_balance"]) == D("40000")


def test_cannot_close_short_when_nothing_was_received_but_can_cancel(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "10", "10")])
    response = shop.post_raw(f"/api/v1/purchase-orders/{po['id']}/close-short", idem=False)
    assert response.status_code == 422 and response.json()["code"] == "nothing_received"
    cancelled = shop.post(f"/api/v1/purchase-orders/{po['id']}/cancel")
    assert cancelled["status"] == "cancelled" and D(cancelled["summary"]["remaining_value"]) == 0


def test_cannot_cancel_after_goods_arrived(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "10", "10")])
    shop.grn(po, [{"received_qty_base": "1"}])
    response = shop.post_raw(f"/api/v1/purchase-orders/{po['id']}/cancel", idem=False)
    assert response.status_code == 409


def test_reversing_a_receipt_restores_stock_books_and_po(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "100", "10")])
    grn = shop.grn(po, [{"received_qty_base": "100"}])
    assert shop.po_detail(po["id"])["status"] == "received"

    reversed_ = shop.post(f"/api/v1/goods-receipts/{grn['id']}/reverse", idem=True)
    assert reversed_["status"] == "reversed"
    detail = shop.po_detail(po["id"])
    assert detail["status"] == "confirmed" and D(detail["summary"]["received_value"]) == 0
    assert shop.stock(product)["qty_total"] == "0.000"
    assert D(shop.supplier_summary(supplier)["ledger_balance"]) == 0
    assert shop.reconcile()["ok"] is True
    # The undelivered quantity is available again.
    shop.grn(po, [{"received_qty_base": "100"}])


def test_a_receipt_cannot_be_reversed_after_its_stock_moved(shop: Shop, db: Session) -> None:
    from app.modules.inventory.stock import get_location_id, stock_out  # noqa: PLC0415

    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "100", "10")])
    grn = shop.grn(po, [{"received_qty_base": "100"}])
    stock_out(
        db,
        product_id=product["id"],
        location_id=get_location_id(db, "SELLABLE"),
        qty=D(5),
        business_date=today(),
        movement_type="sale",
        source_doc_type="TEST",
        source_doc_id=1,
    )
    response = shop.post_raw(f"/api/v1/goods-receipts/{grn['id']}/reverse")
    assert response.status_code == 409 and response.json()["code"] == "stock_already_moved"
    assert shop.po_detail(po["id"])["status"] == "received"  # nothing half-applied


def test_draft_po_can_be_edited_and_deleted_but_a_confirmed_one_cannot(shop: Shop) -> None:
    supplier, p1, p2 = shop.supplier(), shop.product(), shop.product()
    draft = shop.po(supplier, [shop.line(p1, "10", "5")], confirm=False)
    edited = shop.client.put(
        f"/api/v1/purchase-orders/{draft['id']}",
        headers=shop.headers,
        json={"supplier_id": supplier, "items": [shop.line(p2, "20", "7")]},
    )
    assert edited.status_code == 200
    assert D(edited.json()["summary"]["ordered_value"]) == D("140")
    assert [i["product_id"] for i in edited.json()["items"]] == [p2["id"]]
    assert (
        shop.client.delete(
            f"/api/v1/purchase-orders/{draft['id']}", headers=shop.headers
        ).status_code
        == 204
    )

    confirmed = shop.po(supplier, [shop.line(p1, "10", "5")])
    update = shop.client.put(
        f"/api/v1/purchase-orders/{confirmed['id']}",
        headers=shop.headers,
        json={"supplier_id": supplier, "items": [shop.line(p1, "1", "1")]},
    )
    assert update.status_code == 409
    delete = shop.client.delete(f"/api/v1/purchase-orders/{confirmed['id']}", headers=shop.headers)
    assert delete.status_code == 409


# --- Supplier payments ---------------------------------------------------------------------------
def test_payment_can_be_allocated_to_a_po_created_later(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product()
    payment = shop.pay(supplier, "50000")  # advance with no order yet
    assert payment["allocated_amount"] == "0.00" and payment["unallocated_amount"] == "50000.00"
    po = shop.po(supplier, [shop.line(product, "1000", "100")])
    allocated = shop.post(
        f"/api/v1/supplier-payments/{payment['id']}/allocate",
        {"allocations": [{"po_id": po["id"], "amount": "30000"}]},
    )
    assert (
        allocated["allocated_amount"] == "30000.00"
        and allocated["unallocated_amount"] == "20000.00"
    )
    assert D(shop.po_detail(po["id"])["summary"]["paid_value"]) == D("30000")
    over = shop.post_raw(
        f"/api/v1/supplier-payments/{payment['id']}/allocate",
        {"allocations": [{"po_id": po["id"], "amount": "20001"}]},
        idem=False,
    )
    assert over.status_code == 422 and over.json()["code"] == "over_allocated"


def test_payment_validation(shop: Shop) -> None:
    s1, s2, product = shop.supplier(), shop.supplier(), shop.product()
    po = shop.po(s1, [shop.line(product, "10", "100")])  # payable 1,000
    draft = shop.po(s1, [shop.line(product, "10", "100")], confirm=False)

    def pay(supplier: int, amount: str, allocations: list) -> tuple[int, str]:
        response = shop.pay(supplier, amount, allocations=allocations, expect=422)
        return 422, response["code"]

    assert pay(s1, "1001", [{"po_id": po["id"], "amount": "1001"}]) == (422, "over_payment")
    assert pay(s1, "500", [{"po_id": po["id"], "amount": "600"}]) == (422, "over_allocated")
    assert pay(s2, "500", [{"po_id": po["id"], "amount": "500"}])[0] == 422  # other supplier's PO
    assert pay(s1, "500", [{"po_id": draft["id"], "amount": "500"}]) == (422, "po_not_payable")
    shop.pay(
        s1, "1000", allocations=[{"po_id": po["id"], "amount": "1000"}]
    )  # exactly payable is fine
    assert pay(s1, "1", [{"po_id": po["id"], "amount": "1"}]) == (422, "over_payment")


def test_reversing_a_payment_removes_its_effect(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "10", "100")])
    payment = shop.pay(supplier, "1000", allocations=[{"po_id": po["id"], "amount": "1000"}])
    assert D(shop.supplier_summary(supplier)["ledger_balance"]) == D("1000")
    reversed_ = shop.post(f"/api/v1/supplier-payments/{payment['id']}/reverse", idem=True)
    assert reversed_["status"] == "reversed"
    assert D(shop.supplier_summary(supplier)["ledger_balance"]) == 0
    assert (
        D(shop.po_detail(po["id"])["summary"]["paid_value"]) == 0
    )  # reversed payments don't count
    again = shop.post_raw(f"/api/v1/supplier-payments/{payment['id']}/reverse")
    assert again.status_code == 409


def test_payment_moves_money_out_of_the_chosen_account(shop: Shop) -> None:
    supplier = shop.supplier()
    bank = shop.post("/api/v1/payment-accounts", {"name": "City Bank", "kind": "bank"}, expect=201)
    shop.pay(supplier, "7500", account="City Bank")
    balances = {a["name"]: D(a["balance"]) for a in shop.get("/api/v1/payment-accounts")}
    assert balances["City Bank"] == D("-7500")  # no opening balance yet, so it shows as an outflow
    assert bank["id"]


def test_supplier_ledger_shows_running_balance(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "100", "10")])  # 1,000
    shop.pay(supplier, "400", allocations=[{"po_id": po["id"], "amount": "400"}])
    shop.grn(po, [{"received_qty_base": "100"}])  # +1,000 owed
    entries = shop.get(f"/api/v1/suppliers/{supplier}/ledger")["items"]  # newest first
    assert [(D(e["debit"]), D(e["credit"]), D(e["balance"])) for e in entries] == [
        (D(0), D("1000"), D("-600")),
        (D("400"), D(0), D("400")),
    ]
    assert entries[0]["source_doc_no"].startswith("GRN-")
    assert entries[1]["source_doc_no"].startswith("SPAY-")


def test_purchase_order_list_filters_and_totals(shop: Shop) -> None:
    supplier, other, product = shop.supplier("Alpha Co"), shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "10", "10")])
    shop.po(other, [shop.line(product, "10", "10")])
    listing = shop.get("/api/v1/purchase-orders", supplier_id=supplier)
    assert [p["id"] for p in listing["items"]] == [po["id"]]
    assert listing["items"][0]["supplier_name"] == "Alpha Co"
    assert shop.get("/api/v1/purchase-orders", q=po["doc_no"])["total"] == 1
    assert (
        shop.get("/api/v1/purchase-orders", supplier_id=supplier, status="received")["total"] == 0
    )


# --- Permissions & audit -------------------------------------------------------------
def test_storekeeper_receives_goods_but_cannot_pay_or_reverse(
    client: TestClient, owner: dict[str, str], storekeeper: dict[str, str], db: Session
) -> None:
    owner_shop = Shop(client, owner, db)
    supplier, product = owner_shop.supplier(), owner_shop.product()
    po = owner_shop.po(supplier, [owner_shop.line(product, "10", "10")])
    store = Shop(client, storekeeper, db)
    grn = store.grn(po, [{"received_qty_base": "5"}])  # create + post allowed
    assert grn["status"] == "posted"
    assert store.post_raw("/api/v1/supplier-payments", {}, idem=True).status_code == 403
    assert store.post_raw(f"/api/v1/goods-receipts/{grn['id']}/reverse").status_code == 403
    assert (
        client.get(f"/api/v1/suppliers/{supplier}/ledger", headers=storekeeper).status_code == 403
    )


def test_cashier_cannot_see_purchasing(client: TestClient, cashier: dict[str, str]) -> None:
    assert client.get("/api/v1/purchase-orders", headers=cashier).status_code == 403
    assert (
        client.get("/api/v1/inventory/stock", headers=cashier).status_code == 200
    )  # can see stock


def test_posting_and_reversal_are_audited(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "10", "10")])
    grn = shop.grn(po, [{"received_qty_base": "10"}])
    shop.post(f"/api/v1/goods-receipts/{grn['id']}/reverse", idem=True)
    entries = shop.get("/api/v1/audit-logs", entity_type="goods_receipt", entity_id=str(grn["id"]))
    actions = {e["action"] for e in entries["items"]}
    assert {"create", "post", "reverse"} <= actions
    po_actions = {
        e["action"]
        for e in shop.get(
            "/api/v1/audit-logs", entity_type="purchase_order", entity_id=str(po["id"])
        )["items"]
    }
    assert {"create", "confirm"} <= po_actions


def test_stock_movement_ledger_for_a_product(shop: Shop) -> None:
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "10", "10")])
    shop.grn(po, [{"received_qty_base": "6", "free_qty_base": "2", "damaged_qty_base": "1"}])
    page = shop.get(f"/api/v1/inventory/products/{product['id']}/movements")
    assert page["next_after"] is None
    kinds = sorted((m["movement_type"], m["location_code"], m["qty_base"]) for m in page["items"])
    assert kinds == [
        ("grn", "DAMAGED", "1.000"),
        ("grn", "SELLABLE", "5.000"),
        ("grn_free", "SELLABLE", "2.000"),
    ]
    assert all(m["source_doc_no"].startswith("GRN-") for m in page["items"])
    small = shop.get(f"/api/v1/inventory/products/{product['id']}/movements", limit=2)
    assert len(small["items"]) == 2 and small["next_after"] is not None
    rest = shop.get(
        f"/api/v1/inventory/products/{product['id']}/movements", after=small["next_after"]
    )
    assert len(rest["items"]) == 1


def test_low_stock_filter(shop: Shop) -> None:
    supplier = shop.supplier()
    low = shop.product(reorder_level="50")
    ok = shop.product(reorder_level="5")
    po = shop.po(supplier, [shop.line(low, "100", "1"), shop.line(ok, "100", "1")])
    shop.grn(
        po,
        [
            {"item": 0, "received_qty_base": "40"},
            {"item": 1, "received_qty_base": "40"},
        ],
    )
    ids = {r["product_id"] for r in shop.get("/api/v1/inventory/stock", low_only="true")["items"]}
    assert low["id"] in ids and ok["id"] not in ids


def test_stock_totals_for_the_dashboard(shop: Shop) -> None:
    before = shop.get("/api/v1/inventory/stock/totals")
    supplier = shop.supplier()
    product = shop.product(track_batch=True, track_expiry=True, reorder_level="500")
    po = shop.po(supplier, [shop.line(product, "100", "20")])
    shop.grn(po, [{"received_qty_base": "100", "batch_no": "T1", "expiry_date": in_days(10)}])
    after = shop.get("/api/v1/inventory/stock/totals")
    assert D(after["total_value"]) - D(before["total_value"]) == D("2000")
    assert after["products_in_stock"] == before["products_in_stock"] + 1
    assert after["low_stock_count"] == before["low_stock_count"] + 1  # 100 <= reorder level 500
    assert after["expiring_soon_count"] == before["expiring_soon_count"] + 1  # within 30 days
    assert after["near_expiry_days"] == 30


def test_purchase_cost_is_hidden_from_users_without_the_profit_permission(
    client: TestClient, owner: dict[str, str], cashier: dict[str, str], db: Session
) -> None:
    shop = Shop(client, owner, db)
    supplier, product = shop.supplier(), shop.product()
    po = shop.po(supplier, [shop.line(product, "10", "10")])
    shop.grn(po, [{"received_qty_base": "10"}])

    row = next(
        r
        for r in client.get(
            "/api/v1/inventory/stock", headers=cashier, params={"q": product["sku"]}
        ).json()["items"]
        if r["product_id"] == product["id"]
    )
    assert row["qty_total"] == "10.000"  # the cashier can see quantities...
    assert row["avg_cost"] is None and row["value"] is None  # ...but not what they cost
    totals = client.get("/api/v1/inventory/stock/totals", headers=cashier).json()
    assert totals["total_value"] is None
    moves = client.get(
        f"/api/v1/inventory/products/{product['id']}/movements", headers=cashier
    ).json()
    assert moves["items"] and all(
        m["value"] is None and m["unit_cost"] is None for m in moves["items"]
    )
    assert D(shop.stock(product)["value"]) == D("100")  # the owner still sees them
