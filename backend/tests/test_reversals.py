"""Every correctable document: reversing it restores every account, party and stock balance
(testing-and-acceptance.md §3), and each financial check fails on corrupted data."""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.integrity.reconcile import reconcile
from tests.helpers import Shop, key

SNAPSHOTS = (
    "SELECT a.code, l.party_type, l.party_id, l.payment_account_id, sum(l.debit - l.credit) "
    "FROM journal_lines l JOIN accounts a ON a.id = l.account_id "
    "GROUP BY 1, 2, 3, 4 HAVING sum(l.debit - l.credit) <> 0",
    "SELECT product_id, batch_id, location_id, qty_base FROM stock_balances WHERE qty_base <> 0",
    "SELECT product_id, qty_on_hand, total_value FROM product_costs WHERE qty_on_hand <> 0",
    "SELECT supplier_id, claim_type, status, book_amount, claim_amount FROM claimable_items "
    "WHERE status <> 'cancelled'",
    "SELECT claim_id, book_cleared, settled_amount, rejected_amount FROM company_claim_lines",
    "SELECT status, total_settled, total_rejected FROM company_claims",
    "SELECT * FROM daily_sales_summary WHERE qty_base <> 0 OR returns_amount <> 0",
)


def balances(db: Session) -> list[list[str]]:
    db.flush()
    return [sorted(str(tuple(row)) for row in db.execute(text(q))) for q in SNAPSHOTS]


@pytest.fixture
def shop(client: TestClient, owner: dict[str, str], db: Session) -> Shop:
    return Shop(client, owner, db)


def reverse(shop: Shop, path: str, expect: int = 200) -> Any:
    response = shop.client.post(f"/api/v1{path}", headers={**shop.headers, **key()})
    assert response.status_code == expect, f"{path}: {response.status_code} {response.text}"
    return response.json()


def create(shop: Shop, path: str, body: dict[str, Any]) -> Any:
    return shop.post(f"/api/v1{path}", body, expect=201, idem=True)


def stocked(shop: Shop) -> tuple[dict[str, Any], int, int]:
    """A product with stock from one company, and a customer."""
    supplier = shop.supplier()
    product = shop.product(tp="100", dp="80", mrp="120")
    po = shop.po(supplier, [shop.line(product, "50", "60")])
    shop.grn(po, [{"received_qty_base": "50"}])
    return product, supplier, shop.customer(credit_limit="100000")["id"]


def assert_round_trip(shop: Shop, db: Session, make: Callable[[], str]) -> None:
    before = balances(db)
    path = make()
    assert balances(db) != before, "the document changed nothing"
    reverse(shop, path)
    assert balances(db) == before
    assert reconcile(db).issues == []
    again = shop.client.post(f"/api/v1{path}", headers={**shop.headers, **key()})
    assert again.status_code == 409  # only once


def money_accounts(shop: Shop) -> tuple[int, int, int]:
    cash = shop.payment_account_id("Cash in Hand")
    bkash = shop.payment_account_id("bKash")
    rent = next(a["id"] for a in shop.get("/api/v1/accounts") if a["code"] == "6010")
    return cash, bkash, rent


@pytest.mark.parametrize("kind", ["expense", "transfer", "capital", "drawing"])
def test_money_documents_reverse_completely(shop: Shop, db: Session, kind: str) -> None:
    cash, bkash, rent = money_accounts(shop)

    def make() -> str:
        if kind == "expense":
            body = {"expense_account_id": rent, "amount": "75", "payment_account_id": cash}
            return f"/expenses/{create(shop, '/expenses', body)['id']}/reverse"
        if kind == "transfer":
            body = {
                "from_payment_account_id": cash,
                "to_payment_account_id": bkash,
                "amount": "40",
                "charges": "2",
            }
            return f"/fund-transfers/{create(shop, '/fund-transfers', body)['id']}/reverse"
        body = {"kind": kind, "amount": "10", "payment_account_id": cash}
        return f"/owner-transactions/{create(shop, '/owner-transactions', body)['id']}/reverse"

    assert_round_trip(shop, db, make)


@pytest.mark.parametrize("method", ["cash", "credit_to_account"])
def test_a_commission_reverses_with_its_claim_item(shop: Shop, db: Session, method: str) -> None:
    _, supplier, customer = stocked(shop)
    cash = shop.payment_account_id("Cash in Hand")

    def make() -> str:
        body = {
            "customer_id": customer,
            "amount": "70",
            "method": method,
            "payment_account_id": cash if method == "cash" else None,
            "is_company_funded": True,
            "supplier_id": supplier,
        }
        return f"/commission-payments/{create(shop, '/commission-payments', body)['id']}/reverse"

    assert_round_trip(shop, db, make)


def submitted_claim(shop: Shop) -> dict[str, Any]:
    _, supplier, customer = stocked(shop)
    cash = shop.payment_account_id("Cash in Hand")
    for amount in ("70", "30"):
        create(
            shop,
            "/commission-payments",
            {
                "customer_id": customer,
                "amount": amount,
                "method": "cash",
                "payment_account_id": cash,
                "is_company_funded": True,
                "supplier_id": supplier,
            },
        )
    items = [i["id"] for i in shop.get("/api/v1/claimable-items")["items"]]
    claim = shop.post(
        "/api/v1/company-claims",
        {"supplier_id": supplier, "claimable_item_ids": items},
        expect=201,
    )
    return dict(shop.post(f"/api/v1/company-claims/{claim['id']}/submit", {}, idem=True))


@pytest.mark.parametrize("method", ["cash", "supplier_credit"])
def test_a_settlement_reverses_and_reopens_the_claim(shop: Shop, db: Session, method: str) -> None:
    claim = submitted_claim(shop)
    cash = shop.payment_account_id("Cash in Hand")

    def make() -> str:
        body = {
            "method": method,
            "amount": "90",
            "payment_account_id": cash if method == "cash" else None,
        }
        settled = create(shop, f"/company-claims/{claim['id']}/settlements", body)
        settlement = settled["settlements"][-1]["id"]
        return f"/company-claims/{claim['id']}/settlements/{settlement}/reverse"

    assert_round_trip(shop, db, make)
    assert shop.get(f"/api/v1/company-claims/{claim['id']}")["status"] == "submitted"


def test_a_rejection_reverses_and_settlements_undo_newest_first(shop: Shop, db: Session) -> None:
    claim = submitted_claim(shop)
    cash = shop.payment_account_id("Cash in Hand")
    base = f"/company-claims/{claim['id']}"
    first = create(
        shop, f"{base}/settlements", {"method": "cash", "amount": "20", "payment_account_id": cash}
    )["settlements"][-1]["id"]

    def make() -> str:
        line = shop.get(f"/api/v1{base}")["lines"][-1]
        rejected = shop.post(
            f"/api/v1{base}/reject-lines",
            {"lines": [{"claim_line_id": line["id"], "amount": "30"}]},
            idem=True,
        )
        return f"{base}/settlements/{rejected['settlements'][-1]['id']}/reverse"

    before = balances(db)
    path = make()
    refused = shop.client.post(
        f"/api/v1{base}/settlements/{first}/reverse", headers={**shop.headers, **key()}
    )
    assert refused.json()["code"] == "reverse_later_first"
    reverse(shop, path)
    assert balances(db) == before
    reverse(shop, f"{base}/settlements/{first}/reverse")
    assert reconcile(db).issues == []


@pytest.mark.parametrize("refund", ["credit", "cash"])
def test_a_sales_return_reverses_including_the_trade_discount(
    shop: Shop, db: Session, refund: str
) -> None:
    product, supplier, customer = stocked(shop)
    cash = shop.payment_account_id("Cash in Hand")
    line = shop.sale_line(product, "5", company_discount="25", company_supplier_id=supplier)
    paid = "0" if refund == "credit" else "475"
    invoice = shop.invoice(customer, [line], paid=paid)
    shop.post_sale(invoice["id"])
    row = shop.get(f"/api/v1/sales-invoices/{invoice['id']}/returnable")[0]

    def make() -> str:
        draft = shop.post(
            "/api/v1/sales-returns",
            {
                "invoice_id": invoice["id"],
                "refund_method": refund,
                "payment_account_id": cash if refund == "cash" else None,
                "items": [{"invoice_item_id": row["invoice_item_id"], "qty_base": "2"}],
            },
            expect=201,
        )
        shop.post(f"/api/v1/sales-returns/{draft['id']}/post", idem=True)
        return f"/sales-returns/{draft['id']}/reverse"

    assert_round_trip(shop, db, make)
    # The return and its reversal are later stock movements, so under the moving-average rule
    # the invoice stays irreversible: correct it with another return (reversals-impact.md).
    refused = reverse(shop, f"/sales-invoices/{invoice['id']}/reverse", expect=409)
    assert refused["code"] == "stock_already_moved"


@pytest.mark.parametrize("settlement", ["credit", "claim"])
def test_a_purchase_return_reverses(shop: Shop, db: Session, settlement: str) -> None:
    product, supplier, _ = stocked(shop)

    def make() -> str:
        draft = shop.post(
            "/api/v1/purchase-returns",
            {
                "supplier_id": supplier,
                "items": [
                    {
                        "product_id": product["id"],
                        "qty_base": "3",
                        "unit_value": "65",
                        "settlement": "supplier_credit" if settlement == "credit" else "claim",
                    }
                ],
            },
            expect=201,
        )
        shop.post(f"/api/v1/purchase-returns/{draft['id']}/post", idem=True)
        return f"/purchase-returns/{draft['id']}/reverse"

    assert_round_trip(shop, db, make)


def test_a_return_cannot_be_reversed_once_the_stock_has_moved(shop: Shop) -> None:
    product, supplier, customer = stocked(shop)
    draft = shop.post(
        "/api/v1/purchase-returns",
        {
            "supplier_id": supplier,
            "items": [{"product_id": product["id"], "qty_base": "3", "unit_value": "60"}],
        },
        expect=201,
    )
    shop.post(f"/api/v1/purchase-returns/{draft['id']}/post", idem=True)
    shop.post_sale(shop.invoice(customer, [shop.sale_line(product, "1")])["id"])

    refused = reverse(shop, f"/purchase-returns/{draft['id']}/reverse", expect=409)

    assert refused["code"] == "stock_already_moved"


def test_reversal_respects_the_period_lock_and_permission(
    shop: Shop, client: TestClient, cashier: dict[str, str]
) -> None:
    cash, _, rent = money_accounts(shop)
    body = {"expense_account_id": rent, "amount": "5", "payment_account_id": cash}
    expense = create(shop, "/expenses", body)
    path = f"/expenses/{expense['id']}/reverse"
    assert client.post(f"/api/v1{path}", headers={**cashier, **key()}).status_code == 403

    shop.put_period_lock(expense["business_date"])

    assert reverse(shop, path, expect=409)["code"] == "period_locked"


CORRUPTIONS = {
    "receivables": "UPDATE sales_invoices SET net_amount = net_amount + 1, "
    "gross_amount = gross_amount + 1 WHERE status = 'posted'",
    "payables": "UPDATE goods_receipt_items SET value = value + 1",
    "claims": "UPDATE claimable_items SET book_amount = book_amount + 1 WHERE status = 'open'",
    "cost_of_goods_sold": "UPDATE sales_invoice_items SET cogs = cogs + 1",
    "product_cost": "UPDATE product_costs SET total_value = total_value + 1",
    "stock_balance": "UPDATE stock_balances SET qty_base = qty_base + 1",
}


@pytest.mark.parametrize("check", sorted(CORRUPTIONS))
def test_each_check_fails_on_corrupted_data(shop: Shop, db: Session, check: str) -> None:
    product, supplier, customer = stocked(shop)
    line = shop.sale_line(product, "2", company_discount="10", company_supplier_id=supplier)
    shop.post_sale(shop.invoice(customer, [line])["id"])
    assert reconcile(db).issues == []

    db.execute(text(CORRUPTIONS[check]))

    assert check in {issue.check for issue in reconcile(db).issues}


def test_stored_result_is_returned_until_a_fresh_one_is_asked(shop: Shop, db: Session) -> None:
    first = shop.get("/api/v1/inventory/reconcile")
    stocked(shop)
    db.execute(text("UPDATE product_costs SET total_value = total_value + 1"))

    stored = shop.get("/api/v1/inventory/reconcile")
    fresh = shop.get("/api/v1/inventory/reconcile", fresh=True)

    assert first["ok"] and stored["ok"] and stored["ran_at"]
    assert not fresh["ok"]
