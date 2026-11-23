"""Sales returns: stock back at the original cost, books reversed, refunds, limits."""

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.claims.models import ClaimableItem
from tests.helpers import Shop, account_totals, key, stock_at, today

D = Decimal


@pytest.fixture
def shop(client: TestClient, owner: dict[str, str], db: Session) -> Shop:
    return Shop(client, owner, db)


def sell(
    shop: Shop,
    qty: str = "1000",
    *,
    price: str = "30",
    limit: str = "50000",
    paid: str = "0",
    **line: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Receive stock at cost 10 and sell `qty` of it: (product, customer, posted invoice)."""
    product = shop.product(tp=price, dp="8")
    shop.receive(product, qty, price="10")
    customer = shop.customer(credit_limit=limit)
    invoice = shop.invoice(customer["id"], [shop.sale_line(product, qty, **line)], paid=paid)
    return product, customer, shop.post_sale(invoice["id"])


def line_of(
    posted: dict[str, Any], qty: str, condition: str = "good", index: int = 0
) -> dict[str, Any]:
    item = posted["items"][index]
    return {
        "invoice_item_id": item["id"],
        "batch_id": item["batches"][0]["batch_id"],
        "condition": condition,
        "qty_base": qty,
    }


def make_return(
    shop: Shop,
    posted: dict[str, Any],
    lines: list[dict[str, Any]],
    *,
    refund: str = "credit",
    account: str | None = None,
    expect: int = 201,
) -> Any:
    body: dict[str, Any] = {"invoice_id": posted["id"], "refund_method": refund, "items": lines}
    if account:
        body["payment_account_id"] = shop.payment_account_id(account)
    return shop.post("/api/v1/sales-returns", body, expect=expect)


def post_return(
    shop: Shop, ret: dict[str, Any], *, expect: int = 200, idem_key: str | None = None
) -> Any:
    headers = {"Idempotency-Key": idem_key} if idem_key else key()
    response = shop.client.post(
        f"/api/v1/sales-returns/{ret['id']}/post", headers={**shop.headers, **headers}
    )
    assert response.status_code == expect, f"{response.status_code} {response.text}"
    return response.json()


def invoice_of(shop: Shop, posted: dict[str, Any]) -> dict[str, Any]:
    return dict(shop.get(f"/api/v1/sales-invoices/{posted['id']}"))


# --- Acceptance ------------------------------------------------------------------------------
def test_1000_sold_200_returned_nets_800_and_reverses_cost_at_original_cost(
    shop: Shop, db: Session
) -> None:
    product, customer, posted = sell(shop)
    ret = make_return(shop, posted, [line_of(posted, "200")])
    done = post_return(shop, ret)

    assert done["status"] == "posted"
    assert done["doc_no"].startswith("SR-")
    assert (done["gross_amount"], done["net_amount"], done["cogs"]) == (
        "6000.00",
        "6000.00",
        "2000.00",
    )
    # Books: sales returns debited, receivable credited, stock back and cost reversed at 10 each.
    totals = account_totals(db, "sales_return", done["id"])
    assert totals["4100"] == (D(6000), D(0))
    assert totals["1100"] == (D(0), D(6000))
    assert totals["1200"] == (D(2000), D(0))
    assert totals["5000"] == (D(0), D(2000))
    assert stock_at(db, product["id"], "SELLABLE") == D(200)

    inv = invoice_of(shop, posted)
    assert inv["returned_amount"] == "6000.00"
    assert inv["due_amount"] == "24000.00"  # 800 units at 30
    assert shop.customer_summary(customer["id"])["balance"] == "24000.00"

    [row] = shop.get(
        "/api/v1/reports/sales-summary",
        date_from=today().isoformat(),
        date_to=today().isoformat(),
        group="day",
    )
    assert (row["qty_base"], row["gross_amount"], row["returns_amount"], row["cogs"]) == (
        "800.000",
        "30000.00",
        "6000.00",
        "8000.00",
    )
    assert row["gross_profit"] == "16000.00"  # (30000 - 6000) - 8000
    assert shop.reconcile()["ok"] is True


def test_a_damaged_return_is_not_sellable(shop: Shop, db: Session) -> None:
    product, customer, posted = sell(shop, "100")
    post_return(shop, make_return(shop, posted, [line_of(posted, "10", "damaged")]))

    assert stock_at(db, product["id"], "DAMAGED") == D(10)
    assert stock_at(db, product["id"], "SELLABLE") == D(0)
    again = shop.invoice(customer["id"], [shop.sale_line(product, "1")])
    assert shop.post_sale(again["id"], expect=422)["code"] == "insufficient_stock"


def test_an_expired_return_goes_to_the_expired_location(shop: Shop, db: Session) -> None:
    product, _, posted = sell(shop, "50")
    post_return(shop, make_return(shop, posted, [line_of(posted, "5", "expired")]))
    assert stock_at(db, product["id"], "EXPIRED") == D(5)


def test_returning_more_than_was_sold_is_refused(shop: Shop) -> None:
    _, _, posted = sell(shop, "100")
    refused = make_return(shop, posted, [line_of(posted, "101")], expect=422)
    assert refused["code"] == "over_return"

    post_return(shop, make_return(shop, posted, [line_of(posted, "60")]))
    second = make_return(shop, posted, [line_of(posted, "41")], expect=422)  # only 40 left
    assert second["code"] == "over_return"


# --- Refunds -------------------------------------------------------------------------------------
def test_a_cash_refund_pays_money_out_and_leaves_the_invoice_paid(shop: Shop, db: Session) -> None:
    product = shop.product(tp="30", dp="8")
    shop.receive(product, "100", price="10")
    customer = shop.customer(credit_limit="0")
    posted = shop.post_sale(
        shop.invoice(customer["id"], [shop.sale_line(product, "10")], paid="300")["id"]
    )
    cash_before = next(
        a for a in shop.get("/api/v1/payment-accounts") if a["name"] == "Cash in Hand"
    )

    ret = make_return(shop, posted, [line_of(posted, "4")], refund="cash", account="Cash in Hand")
    done = post_return(shop, ret)

    assert done["net_amount"] == "120.00"
    cash_after = next(
        a for a in shop.get("/api/v1/payment-accounts") if a["name"] == "Cash in Hand"
    )
    assert D(cash_before["balance"]) - D(cash_after["balance"]) == D(120)
    inv = invoice_of(shop, posted)
    assert inv["due_amount"] == "0.00"
    assert inv["payment_status"] == "paid"
    assert shop.customer_summary(customer["id"])["balance"] == "0.00"
    assert account_totals(db, "sales_return", done["id"])["1000"] == (D(0), D(120))


def test_a_cash_refund_needs_an_account_and_a_credit_refund_must_not_have_one(shop: Shop) -> None:
    _, _, posted = sell(shop, "10")
    assert make_return(shop, posted, [line_of(posted, "1")], refund="cash", expect=422)
    refused = make_return(
        shop, posted, [line_of(posted, "1")], refund="credit", account="Cash in Hand", expect=422
    )
    assert refused["errors"][0]["field"] == "payment_account_id"


# --- Discounts and claims -------------------------------------------------------------------------
def test_returns_share_the_line_amounts_exactly_with_the_last_one_taking_the_remainder(
    shop: Shop,
) -> None:
    product = shop.product(tp="33.33", dp="8")
    shop.receive(product, "10", price="10")
    customer = shop.customer(credit_limit="1000")
    posted = shop.post_sale(
        shop.invoice(customer["id"], [shop.sale_line(product, "3", dealer_discount="1.00")])["id"]
    )
    returns = [
        post_return(shop, make_return(shop, posted, [line_of(posted, "1")])) for _ in range(3)
    ]
    assert sum(D(r["gross_amount"]) for r in returns) == D("99.99")
    assert sum(D(r["dealer_discount"]) for r in returns) == D("1.00")
    assert sum(D(r["net_amount"]) for r in returns) == D("98.99")
    assert invoice_of(shop, posted)["due_amount"] == "0.00"


def test_returning_company_funded_goods_shrinks_then_cancels_the_open_claim(
    shop: Shop, db: Session
) -> None:
    product = shop.product(tp="20", dp="8")
    shop.receive(product, "100", price="10")
    company = shop.supplier()
    customer = shop.customer(credit_limit="1000")
    posted = shop.post_sale(
        shop.invoice(
            customer["id"],
            [shop.sale_line(product, "10", company_discount="30", company_supplier_id=company)],
        )["id"]
    )

    def claimable() -> ClaimableItem:
        db.expire_all()
        item = db.scalar(select(ClaimableItem).where(ClaimableItem.source_doc_id == posted["id"]))
        assert item is not None
        return item

    first = post_return(shop, make_return(shop, posted, [line_of(posted, "5")]))
    assert first["company_discount"] == "15.00"
    assert (claimable().claim_amount, claimable().status) == (D("15.00"), "open")
    assert account_totals(db, "sales_return", first["id"])["1300"] == (D(0), D(15))

    post_return(shop, make_return(shop, posted, [line_of(posted, "5")]))
    assert claimable().status == "cancelled"


# --- Guards ---------------------------------------------------------------------------------------
def test_an_invoice_with_returns_can_no_longer_be_reversed(shop: Shop) -> None:
    _, _, posted = sell(shop, "10")
    post_return(shop, make_return(shop, posted, [line_of(posted, "1")]))
    assert shop.reverse_sale(posted["id"], expect=409)["code"] == "returns_posted"


def test_goods_cannot_be_returned_against_a_draft_invoice(shop: Shop) -> None:
    product = shop.product(tp="30")
    customer = shop.customer()
    draft = shop.invoice(customer["id"], [shop.sale_line(product, "1")])
    refused = shop.post(
        "/api/v1/sales-returns",
        {
            "invoice_id": draft["id"],
            "refund_method": "credit",
            "items": [{"invoice_item_id": draft["items"][0]["id"], "qty_base": "1"}],
        },
        expect=409,
    )
    assert refused["code"] == "not_posted"


def test_posting_a_return_twice_with_one_key_posts_it_once(shop: Shop, db: Session) -> None:
    product, _, posted = sell(shop, "100")
    ret = make_return(shop, posted, [line_of(posted, "20")])

    first = post_return(shop, ret, idem_key="return-key-1")
    replay = post_return(shop, ret, idem_key="return-key-1")

    assert replay == first
    assert stock_at(db, product["id"], "SELLABLE") == D(20)  # 100 - 100 sold + 20 back, once
    post_return(shop, ret, expect=409)  # a new key on a posted return is a conflict


def test_storekeepers_cannot_post_returns_but_cashiers_can(
    shop: Shop, client: TestClient, storekeeper: dict[str, str], cashier: dict[str, str]
) -> None:
    _, _, posted = sell(shop, "10")
    body = {
        "invoice_id": posted["id"],
        "refund_method": "credit",
        "items": [line_of(posted, "1")],
    }
    assert client.post("/api/v1/sales-returns", headers=storekeeper, json=body).status_code == 403
    assert client.post("/api/v1/sales-returns", headers=cashier, json=body).status_code == 201


def test_a_credit_return_reduces_aging_and_the_ledger_agrees(shop: Shop) -> None:
    _, customer, posted = sell(shop, "100")
    post_return(shop, make_return(shop, posted, [line_of(posted, "40")]))  # 1200 credited

    report = shop.get("/api/v1/reports/customer-aging")
    row = next(r for r in report["rows"] if r["customer_id"] == customer["id"])
    assert (row["current"], row["total"], row["on_account"]) == ("1800.00", "1800.00", "0.00")
    trial = shop.get("/api/v1/reports/trial-balance")
    assert trial["balanced"] is True
