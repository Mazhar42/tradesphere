"""Finance and reporting: expenses, transfers, owner money, cashbook, P&L, margins, valuation,
near-expiry, claims aging, PO status, dashboard visibility, Excel export and projection repair.

Hand-calculated fixture used by several tests (one day of trading):
  Sold 10 @ 100 (gross 1000, cost 600). Customer returned 2 good units (returns 200, cost 120).
  Rent 150 paid in cash. Transfer 500 cash to bKash with 5 charges (Bank & MFS charges 6060).
  Net sales 800, COGS 480, gross profit 320 (40%), operating expenses 155, net profit 165.
"""

import io
import zipfile
from datetime import timedelta
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.accounting.models import Account
from app.modules.claims.service import create_claimable
from app.modules.sales.models import DailySalesSummary
from app.modules.sales.rebuild import rebuild_sales_summary
from tests.helpers import Shop, account_totals, in_days, key, today

D = Decimal
SHEET_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@pytest.fixture
def shop(client: TestClient, owner: dict[str, str], db: Session) -> Shop:
    return Shop(client, owner, db)


def num(value: Any) -> Decimal:
    return D(str(value))


def period() -> dict[str, str]:
    iso = today().isoformat()
    return {"date_from": iso, "date_to": iso}


def account_id(db: Session, code: str) -> int:
    value = db.scalar(select(Account.id).where(Account.code == code))
    assert value is not None, code
    return int(value)


def sell(
    shop: Shop, *, price: str = "100", cost: str = "60", qty: str = "10", paid: str = "0"
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Receive stock at `cost`, sell `qty` at list price (no dealer discount), post the invoice."""
    product = shop.product(tp=price, dp=price)
    shop.receive(product, "20", price=cost)
    customer = shop.customer(credit_limit="100000")
    invoice = shop.invoice(customer["id"], [shop.sale_line(product, qty)], paid=paid)
    return product, customer, shop.post_sale(invoice["id"])


def return_units(shop: Shop, posted: dict[str, Any], qty: str) -> dict[str, Any]:
    item = posted["items"][0]
    body = {
        "invoice_id": posted["id"],
        "refund_method": "credit",
        "items": [
            {
                "invoice_item_id": item["id"],
                "batch_id": item["batches"][0]["batch_id"],
                "condition": "good",
                "qty_base": qty,
            }
        ],
    }
    ret = shop.post("/api/v1/sales-returns", body, expect=201)
    return dict(shop.post(f"/api/v1/sales-returns/{ret['id']}/post", idem=True))


def add_expense(
    shop: Shop, db: Session, code: str, amount: str, account: str = "Cash in Hand"
) -> Any:
    body = {
        "expense_account_id": account_id(db, code),
        "amount": amount,
        "payment_account_id": shop.payment_account_id(account),
    }
    return shop.post("/api/v1/expenses", body, expect=201, idem=True)


def sheet_rows(content: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.testzip() is None
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))  # noqa: S314
    rows: list[list[str]] = []
    for row in sheet.iterfind(".//x:row", SHEET_NS):
        cells = []
        for cell in row.iterfind("x:c", SHEET_NS):
            text = cell.findtext("x:is/x:t", default=None, namespaces=SHEET_NS)
            cells.append(text if text is not None else cell.findtext("x:v", "", SHEET_NS))
        rows.append(cells)
    return rows


# --- Profit and loss ----------------------------------------------------------------------------
def test_profit_loss_matches_hand_calculated_fixture(shop: Shop, db: Session) -> None:
    _, _, posted = sell(shop)
    return_units(shop, posted, "2")
    add_expense(shop, db, "6010", "150")
    shop.post(
        "/api/v1/fund-transfers",
        {
            "from_payment_account_id": shop.payment_account_id("Cash in Hand"),
            "to_payment_account_id": shop.payment_account_id("bKash"),
            "amount": "500",
            "charges": "5",
        },
        expect=201,
        idem=True,
    )

    report = shop.get("/api/v1/reports/profit-loss", **period())

    assert num(report["gross_sales"]) == 1000
    assert num(report["sales_returns"]) == 200
    assert num(report["dealer_discounts"]) == 0
    assert num(report["net_sales"]) == 800
    assert num(report["cogs"]) == 480
    assert num(report["gross_profit"]) == 320
    assert num(report["gross_margin_pct"]) == 40
    assert num(report["operating_expenses"]) == 155
    assert num(report["net_profit"]) == 165
    assert [(line["label"], num(line["amount"])) for line in report["expense_lines"]] == [
        ("6010 Rent", 150),
        ("6060 Bank & MFS Charges", 5),
    ]


def test_profit_loss_ignores_owner_money_and_transfers(shop: Shop) -> None:
    cash = shop.payment_account_id("Cash in Hand")
    shop.post(
        "/api/v1/owner-transactions",
        {"kind": "capital", "amount": "5000", "payment_account_id": cash},
        expect=201,
        idem=True,
    )
    shop.post(
        "/api/v1/owner-transactions",
        {"kind": "drawing", "amount": "700", "payment_account_id": cash},
        expect=201,
        idem=True,
    )
    shop.post(
        "/api/v1/fund-transfers",
        {
            "from_payment_account_id": cash,
            "to_payment_account_id": shop.payment_account_id("bKash"),
            "amount": "500",
        },
        expect=201,
        idem=True,
    )

    report = shop.get("/api/v1/reports/profit-loss", **period())

    assert num(report["net_profit"]) == 0
    assert report["expense_lines"] == []


def test_profit_loss_excludes_other_days(shop: Shop, db: Session) -> None:
    add_expense(shop, db, "6010", "150")
    yesterday = (today() - timedelta(days=1)).isoformat()

    report = shop.get("/api/v1/reports/profit-loss", date_from=yesterday, date_to=yesterday)

    assert num(report["operating_expenses"]) == 0


def test_profit_loss_exports_a_readable_workbook(shop: Shop, db: Session) -> None:
    sell(shop)
    add_expense(shop, db, "6010", "150")

    response = shop.client.get(
        "/api/v1/reports/profit-loss",
        headers=shop.headers,
        params={**period(), "format": "xlsx"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert 'filename="profit-loss.xlsx"' in response.headers["content-disposition"]
    rows = sheet_rows(response.content)
    assert rows[0] == ["Line", "Amount"]
    values = {row[0]: row[1] for row in rows[1:]}
    assert num(values["Gross sales"]) == 1000
    assert num(values["Net profit"]) == 250


def test_profit_loss_requires_profit_permission(
    client: TestClient, cashier: dict[str, str]
) -> None:
    response = client.get("/api/v1/reports/profit-loss", headers=cashier, params=period())
    assert response.status_code == 403


# --- Expenses, transfers, owner money -----------------------------------------------------------
def test_expense_posts_balanced_entry_and_rejects_non_expense_accounts(
    shop: Shop, db: Session
) -> None:
    cash = shop.payment_account_id("Cash in Hand")
    shop.post(
        "/api/v1/expenses",
        {"expense_account_id": account_id(db, "1000"), "amount": "50", "payment_account_id": cash},
        expect=422,
        idem=True,
    )

    expense = shop.post(
        "/api/v1/expenses",
        {
            "expense_account_id": account_id(db, "6040"),
            "amount": "75.50",
            "payment_account_id": cash,
            "payee": "DESCO",
        },
        expect=201,
        idem=True,
    )

    assert expense["doc_no"].startswith("EXP-")
    assert num(expense["amount"]) == D("75.50")
    assert account_totals(db, "expense", expense["id"]) == {
        "6040": (D("75.50"), D(0)),
        "1000": (D(0), D("75.50")),
    }


def test_expense_replay_with_same_key_posts_once(shop: Shop, db: Session) -> None:
    body = {
        "expense_account_id": account_id(db, "6010"),
        "amount": "150",
        "payment_account_id": shop.payment_account_id("Cash in Hand"),
    }
    headers = key()

    first = shop.post_raw("/api/v1/expenses", body, idem=False, headers=headers)
    second = shop.post_raw("/api/v1/expenses", body, idem=False, headers=headers)

    assert first.status_code == second.status_code == 201
    assert first.json()["doc_no"] == second.json()["doc_no"]
    assert shop.get("/api/v1/expenses")["total"] == 1


def test_fund_transfer_moves_money_and_books_charges(shop: Shop, db: Session) -> None:
    cash = shop.payment_account_id("Cash in Hand")
    bkash = shop.payment_account_id("bKash")
    body = {
        "from_payment_account_id": cash,
        "to_payment_account_id": bkash,
        "amount": "500",
        "charges": "5",
    }
    shop.post(
        "/api/v1/fund-transfers", {**body, "to_payment_account_id": cash}, expect=422, idem=True
    )

    transfer = shop.post("/api/v1/fund-transfers", body, expect=201, idem=True)

    assert transfer["doc_no"].startswith("FTR-")
    assert transfer["from_account"] == "Cash in Hand"
    assert transfer["to_account"] == "bKash"
    assert account_totals(db, "fund_transfer", transfer["id"]) == {
        "1020": (D(500), D(0)),
        "1000": (D(0), D(505)),
        "6060": (D(5), D(0)),
    }


def test_owner_capital_and_drawings_post_to_equity(shop: Shop, db: Session) -> None:
    cash = shop.payment_account_id("Cash in Hand")

    capital = shop.post(
        "/api/v1/owner-transactions",
        {"kind": "capital", "amount": "5000", "payment_account_id": cash},
        expect=201,
        idem=True,
    )
    drawing = shop.post(
        "/api/v1/owner-transactions",
        {"kind": "drawing", "amount": "700", "payment_account_id": cash},
        expect=201,
        idem=True,
    )

    assert account_totals(db, "owner_transaction", capital["id"]) == {
        "1000": (D(5000), D(0)),
        "3000": (D(0), D(5000)),
    }
    assert account_totals(db, "owner_transaction", drawing["id"]) == {
        "3100": (D(700), D(0)),
        "1000": (D(0), D(700)),
    }


# --- Cashbook -----------------------------------------------------------------------------------
def test_cashbook_tracks_cash_in_out_and_carries_the_balance_forward(
    shop: Shop, db: Session
) -> None:
    cash = shop.payment_account_id("Cash in Hand")
    sell(shop, paid="1000")
    add_expense(shop, db, "6010", "150")

    book = shop.get("/api/v1/reports/cashbook", payment_account_id=cash, **period())

    assert num(book["opening"]) == 0
    assert num(book["inflow"]) == 1000
    assert num(book["outflow"]) == 150
    assert num(book["closing"]) == 850
    assert len(book["days"]) == 1

    tomorrow = in_days(1)
    later = shop.get(
        "/api/v1/reports/cashbook",
        date_from=tomorrow,
        date_to=tomorrow,
        payment_account_id=cash,
    )
    assert num(later["opening"]) == 850
    assert later["days"] == []


# --- Margins ------------------------------------------------------------------------------------
def test_margin_by_product_customer_and_category(shop: Shop) -> None:
    _, customer_a, _ = sell(shop, price="100", cost="60", qty="10")
    _, customer_b, _ = sell(shop, price="50", cost="30", qty="4")
    url = "/api/v1/reports/margin"

    by_product = shop.get(url, by="product", **period())
    assert [num(r["gross_profit"]) for r in by_product["rows"]] == [400, 80]
    assert num(by_product["rows"][0]["margin_pct"]) == 40

    by_customer = shop.get(url, by="customer", **period())
    assert {r["label"] for r in by_customer["rows"]} == {customer_a["name"], customer_b["name"]}

    by_category = shop.get(url, by="category", **period())
    assert [r["label"] for r in by_category["rows"]] == ["Uncategorised"]
    assert num(by_category["rows"][0]["gross_profit"]) == 480


# --- Stock valuation and near-expiry ------------------------------------------------------------
def test_stock_valuation_and_near_expiry(shop: Shop) -> None:
    tracked = shop.product(track_batch=True, track_expiry=True, tp="100", dp="100")
    shop.receive(tracked, "5", price="10", batch="B-1", expiry_days=10)
    plain = shop.product(tp="30", dp="30")
    shop.receive(plain, "3", price="20")

    valuation = shop.get("/api/v1/reports/stock-valuation")
    assert num(valuation["total_value"]) == 110

    near = shop.get("/api/v1/reports/near-expiry", days=30)
    assert [r["batch_no"] for r in near["rows"]] == ["B-1"]
    assert near["rows"][0]["days_left"] == 10
    assert num(near["rows"][0]["value"]) == 50

    assert shop.get("/api/v1/reports/near-expiry", days=5)["rows"] == []


# --- Claims aging and PO status -----------------------------------------------------------------
def test_claims_aging_buckets_open_items_by_age(shop: Shop, db: Session) -> None:
    supplier_id = shop.supplier("Acme Pharma")
    for source_id, days_old in ((101, 45), (102, 100)):
        create_claimable(
            db,
            supplier_id=supplier_id,
            claim_type="damage",
            source_doc_type="purchase_return",
            source_doc_id=source_id,
            source_line_id=None,
            business_date=today() - timedelta(days=days_old),
            book_amount=D(100),
            claim_amount=D(100),
            actor_id=None,
        )

    report = shop.get("/api/v1/reports/claims-aging", as_of=today().isoformat())

    row = next(r for r in report["rows"] if r["supplier"] == "Acme Pharma")
    assert num(row["days_31_60"]) == 100
    assert num(row["days_over_90"]) == 100
    assert num(row["total"]) == 200
    assert num(report["total"]) == 200


def test_po_status_shows_ordered_received_and_undelivered(shop: Shop) -> None:
    supplier = shop.supplier("Lotus Foods")
    product = shop.product()
    po = shop.po(supplier, [shop.line(product, "10", "50")])
    shop.grn(po, [{"received_qty_base": "4"}])

    report = shop.get("/api/v1/reports/po-status")

    row = next(r for r in report["rows"] if r["supplier"] == "Lotus Foods")
    assert num(row["ordered"]) == 500
    assert num(row["received"]) == 200
    assert num(row["undelivered"]) == 300


# --- Dashboard ----------------------------------------------------------------------------------
def test_dashboard_today_figures(shop: Shop) -> None:
    _, _, posted = sell(shop, paid="1000")
    return_units(shop, posted, "2")

    dash = shop.get("/api/v1/reports/dashboard")

    assert num(dash["today_gross"]) == 1000
    assert num(dash["today_returns"]) == 200
    assert num(dash["today_net_sales"]) == 800
    assert num(dash["today_gross_profit"]) == 320
    assert num(dash["today_margin_pct"]) == 40
    assert num(dash["cash_total"]) == 1000
    assert len(dash["sales_last_30_days"]) == 30
    assert num(dash["sales_last_30_days"][-1]["net_sales"]) == 800


def test_cashier_dashboard_hides_profit_and_stock_value(
    client: TestClient, cashier: dict[str, str]
) -> None:
    response = client.get("/api/v1/reports/dashboard", headers=cashier)

    assert response.status_code == 200
    body = response.json()
    assert body["today_gross_profit"] is None
    assert body["today_margin_pct"] is None
    assert body["stock_value"] is None


def test_cashier_cannot_post_expenses(
    client: TestClient, cashier: dict[str, str], db: Session
) -> None:
    body = {
        "expense_account_id": account_id(db, "6010"),
        "amount": "150",
        "payment_account_id": next(
            a["id"]
            for a in client.get("/api/v1/sales-payment-accounts", headers=cashier).json()
            if a["name"] == "Cash in Hand"
        ),
    }
    response = client.post("/api/v1/expenses", headers={**cashier, **key()}, json=body)
    assert response.status_code == 403


# --- Projection repair --------------------------------------------------------------------------
def test_projection_drift_is_detected_and_rebuilt(shop: Shop, db: Session) -> None:
    _, _, posted = sell(shop)
    return_units(shop, posted, "2")
    assert shop.reconcile()["ok"] is True

    db.execute(update(DailySalesSummary).values(returns_amount=D("999")))
    db.expire_all()
    drift = shop.reconcile()
    assert drift["ok"] is False
    assert {issue["check"] for issue in drift["issues"]} == {"daily_sales"}

    assert rebuild_sales_summary(db) == 2
    db.expire_all()
    assert shop.reconcile()["ok"] is True


def test_sales_summary_hides_cost_and_margin_from_cashiers(
    client: TestClient, cashier: dict[str, str], shop: Shop
) -> None:
    sell(shop)
    params = {**period(), "group": "day"}

    owner_rows = shop.get("/api/v1/reports/sales-summary", **params)
    cashier_rows = client.get("/api/v1/reports/sales-summary", headers=cashier, params=params)

    assert cashier_rows.status_code == 200
    assert num(owner_rows[0]["gross_profit"]) == 400
    assert cashier_rows.json()[0]["cogs"] is None
    assert cashier_rows.json()[0]["gross_profit"] is None
    assert num(cashier_rows.json()[0]["gross_amount"]) == 1000
