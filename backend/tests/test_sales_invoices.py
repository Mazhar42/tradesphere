"""Sales invoices: posting rules, FEFO, credit, company funding, reversal and reporting."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounting.models import Account, JournalEntry, JournalLine
from app.modules.claims.models import ClaimableItem
from app.modules.inventory.models import ProductCost
from tests.helpers import Shop, in_days, today

D = Decimal


@pytest.fixture
def shop(client: TestClient, owner: dict[str, str], db: Session) -> Shop:
    return Shop(client, owner, db)


def account_totals(db: Session, doc_type: str, doc_id: int) -> dict[str, tuple[Decimal, Decimal]]:
    """Σ debit and Σ credit per account code for one document's journal entries."""
    rows = db.execute(
        select(Account.code, func.sum(JournalLine.debit), func.sum(JournalLine.credit))
        .select_from(JournalLine)
        .join(Account, Account.id == JournalLine.account_id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalEntry.source_doc_type == doc_type, JournalEntry.source_doc_id == doc_id)
        .group_by(Account.code)
    ).all()
    return {code: (D(debit or 0), D(credit or 0)) for code, debit, credit in rows}


def stocked_units(db: Session, product_id: int) -> Decimal:
    return db.scalar(
        select(ProductCost.qty_on_hand).where(ProductCost.product_id == product_id)
    ) or D(0)


# --- Stock issue and FEFO -----------------------------------------------------------------
def test_sale_takes_the_earliest_expiring_batch_first(shop: Shop) -> None:
    product = shop.product(track_batch=True, track_expiry=True, tp="20", dp="8", mrp="30")
    shop.receive(product, "100", price="10", batch="NEW", expiry_days=200)
    shop.receive(product, "50", price="10", batch="OLD", expiry_days=10)
    customer = shop.customer()
    invoice = shop.invoice(customer["id"], [shop.sale_line(product, "30")], paid="600")

    posted = shop.post_sale(invoice["id"])

    assert posted["status"] == "posted"
    assert posted["doc_no"].startswith("INV-")
    [item] = posted["items"]
    assert [(b["batch_no"], b["qty_base"]) for b in item["batches"]] == [("OLD", "30.000")]


def test_a_sale_spanning_two_batches_takes_the_old_one_out_before_the_new(shop: Shop) -> None:
    product = shop.product(track_batch=True, track_expiry=True, tp="20")
    shop.receive(product, "100", price="10", batch="NEW", expiry_days=200)
    shop.receive(product, "50", price="10", batch="OLD", expiry_days=10)
    customer = shop.customer()
    invoice = shop.invoice(customer["id"], [shop.sale_line(product, "60")], paid="1200")

    posted = shop.post_sale(invoice["id"])

    [item] = posted["items"]
    assert [(b["batch_no"], b["qty_base"]) for b in item["batches"]] == [
        ("OLD", "50.000"),
        ("NEW", "10.000"),
    ]
    assert item["cogs"] == "600.00"  # 60 units at the weighted average of 10


def test_part_payment_leaves_the_rest_due_and_records_the_cash(shop: Shop) -> None:
    """Acceptance: Tk 300k sale with Tk 200k paid leaves Tk 100k due."""
    product = shop.product(tp="300", dp="100", mrp="400")
    shop.receive(product, "1000", price="100")
    customer = shop.customer(credit_limit="150000")
    invoice = shop.invoice(customer["id"], [shop.sale_line(product, "1000")], paid="200000")

    posted = shop.post_sale(invoice["id"])

    assert posted["net_amount"] == "300000.00"
    assert posted["received_amount"] == "200000.00"
    assert posted["due_amount"] == "100000.00"
    assert posted["payment_status"] == "partial"
    [receipt] = posted["receipts"]
    assert receipt["amount"] == "200000.00"
    summary = shop.customer_summary(customer["id"])
    assert summary["balance"] == "100000.00"
    assert summary["available_credit"] == "50000.00"


def test_insufficient_stock_is_refused_and_nothing_changes(shop: Shop, db: Session) -> None:
    product = shop.product(tp="20")
    shop.receive(product, "5", price="10")
    customer = shop.customer()
    invoice = shop.invoice(customer["id"], [shop.sale_line(product, "6")], paid="120")

    refused = shop.post_sale(invoice["id"], expect=422)

    assert refused["code"] == "insufficient_stock"
    assert shop.get(f"/api/v1/sales-invoices/{invoice['id']}")["status"] == "draft"
    assert stocked_units(db, product["id"]) == D(5)


# --- Idempotency -------------------------------------------------------------------------------
def test_duplicate_submit_with_the_same_key_creates_one_invoice(shop: Shop, db: Session) -> None:
    """Acceptance: a duplicate submit with the same Idempotency-Key gives one posting."""
    product = shop.product(tp="20")
    shop.receive(product, "100", price="10")
    customer = shop.customer()
    invoice = shop.invoice(customer["id"], [shop.sale_line(product, "5")], paid="100")

    first = shop.post_sale(invoice["id"], idem_key="same-key-1")
    replay = shop.post_sale(invoice["id"], idem_key="same-key-1")

    assert replay == first
    entries = db.scalar(
        select(func.count())
        .select_from(JournalEntry)
        .where(
            JournalEntry.source_doc_type == "sales_invoice",
            JournalEntry.source_doc_id == invoice["id"],
        )
    )
    assert entries == 1
    assert stocked_units(db, product["id"]) == D(95)

    # A new key on an already posted invoice is a plain conflict, not a second sale.
    shop.post_sale(invoice["id"], expect=409)


# --- Pricing ------------------------------------------------------------------------------------
def test_selling_below_dp_is_refused_until_the_owner_allows_it(shop: Shop) -> None:
    product = shop.product(tp="20", dp="15")
    shop.receive(product, "100", price="10")
    customer = shop.customer()
    body = [shop.sale_line(product, "5", unit_price="12")]

    refused = shop.invoice(customer["id"], body, expect=422)
    assert refused["code"] == "below_dp"

    shop.put_preferences(allow_sell_below_dp=True)
    allowed = shop.invoice(customer["id"], body, expect=201)
    assert allowed["items"][0]["base_price"] == "12.0000"


def test_changing_the_trade_price_needs_the_override_permission(
    shop: Shop, client: TestClient, cashier: dict[str, str], db: Session
) -> None:
    product = shop.product(tp="20", dp="8")
    shop.receive(product, "100", price="10")
    customer = shop.customer()
    counter = Shop(client, cashier, db)

    denied = counter.invoice(
        customer["id"], [counter.sale_line(product, "5", unit_price="18")], expect=403
    )
    assert denied["code"] == "price_override_denied"

    at_trade_price = counter.invoice(customer["id"], [counter.sale_line(product, "5")], paid="100")
    assert at_trade_price["net_amount"] == "100.00"


def test_cartons_are_converted_to_base_units_and_priced_per_carton(shop: Shop) -> None:
    product = shop.product(carton=24, tp="20")
    shop.receive(product, "100", price="10")
    customer = shop.customer()
    invoice = shop.invoice(
        customer["id"],
        [
            shop.sale_line(product, "2", uom_id=shop.uom_id("CTN")),
            shop.sale_line(product, "5"),
        ],
        paid="1060",
    )

    posted = shop.post_sale(invoice["id"])

    ctn, pcs = posted["items"]
    assert ctn["uom_code"] == "CTN"
    assert ctn["qty_base"] == "48.000"
    assert ctn["unit_price"] == "480.0000"  # 24 x trade price 20
    assert pcs["qty_base"] == "5.000"
    assert posted["net_amount"] == "1060.00"


# --- Credit -------------------------------------------------------------------------------------
def test_credit_limit_blocks_sales_that_would_exceed_it(shop: Shop) -> None:
    product = shop.product(tp="20")
    shop.receive(product, "100", price="10")
    tight = shop.customer(credit_limit="500")
    invoice = shop.invoice(tight["id"], [shop.sale_line(product, "30")])  # 600, nothing paid

    refused = shop.post_sale(invoice["id"], expect=422)

    assert refused["code"] == "credit_limit_exceeded"
    assert shop.get(f"/api/v1/sales-invoices/{invoice['id']}")["status"] == "draft"


def test_a_customer_with_no_credit_limit_must_pay_in_full(shop: Shop) -> None:
    product = shop.product(tp="20")
    shop.receive(product, "100", price="10")
    cash_only = shop.customer(credit_limit="0")
    invoice = shop.invoice(cash_only["id"], [shop.sale_line(product, "2")])  # 40, unpaid

    refused = shop.post_sale(invoice["id"], expect=422)

    assert "no credit limit" in refused["detail"]


# --- Company funding ---------------------------------------------------------------------------
def test_company_funded_discount_is_a_claim_and_does_not_reduce_revenue(
    shop: Shop, db: Session
) -> None:
    product = shop.product(tp="20", dp="8")
    shop.receive(product, "100", price="10")
    company = shop.supplier()
    customer = shop.customer()
    invoice = shop.invoice(
        customer["id"],
        [
            shop.sale_line(
                product,
                "10",
                dealer_discount="10",
                company_discount="30",
                company_supplier_id=company,
            )
        ],
        paid="160",
    )

    posted = shop.post_sale(invoice["id"])

    assert posted["gross_amount"] == "200.00"
    assert posted["net_amount"] == "160.00"
    totals = account_totals(db, "sales_invoice", posted["id"])
    assert totals["4000"] == (D(0), D(200))  # revenue is the full gross
    assert totals["4200"] == (D(10), D(0))  # dealer discount
    assert totals["1300"] == (D(30), D(0))  # company owes the discount
    assert totals["5000"] == (D(100), D(0))  # cost of 10 units at 10
    claim = db.scalar(
        select(ClaimableItem).where(
            ClaimableItem.source_doc_type == "sales_invoice",
            ClaimableItem.source_doc_id == posted["id"],
        )
    )
    assert claim is not None
    assert (claim.claim_type, claim.claim_amount, claim.supplier_id) == (
        "trade_discount",
        D("30.00"),
        company,
    )


def test_company_funded_free_goods_cost_is_claimed_at_dealer_price(shop: Shop, db: Session) -> None:
    product = shop.product(tp="20", dp="8")
    shop.receive(product, "100", price="10")
    company = shop.supplier()
    customer = shop.customer()
    invoice = shop.invoice(
        customer["id"],
        [
            shop.sale_line(
                product,
                "10",
                free_qty="2",
                free_is_company_scheme=True,
                company_supplier_id=company,
            )
        ],
        paid="200",
    )

    posted = shop.post_sale(invoice["id"])

    [item] = posted["items"]
    assert item["free_cost"] == "20.00"
    assert item["cogs"] == "100.00"
    assert sorted((b["is_free"], b["qty_base"]) for b in item["batches"]) == [
        (False, "10.000"),
        (True, "2.000"),
    ]
    totals = account_totals(db, "sales_invoice", posted["id"])
    assert totals["1300"] == (D(20), D(0))  # the free units are owed back at cost
    claim = db.scalar(select(ClaimableItem).where(ClaimableItem.claim_type == "free_goods"))
    assert claim is not None
    assert (claim.book_amount, claim.claim_amount) == (D("20.00"), D("16.00"))  # 2 x DP 8


# --- Reversal -----------------------------------------------------------------------------------
def test_reversal_restores_stock_clears_the_balance_and_reverses_the_cash(
    shop: Shop, db: Session
) -> None:
    product = shop.product(tp="20")
    shop.receive(product, "100", price="10")
    customer = shop.customer()
    invoice = shop.invoice(customer["id"], [shop.sale_line(product, "10")], paid="200")
    posted = shop.post_sale(invoice["id"])
    assert stocked_units(db, product["id"]) == D(90)

    reversed_invoice = shop.reverse_sale(posted["id"])

    assert reversed_invoice["status"] == "reversed"
    assert reversed_invoice["payment_status"] == "reversed"
    assert stocked_units(db, product["id"]) == D(100)
    assert shop.customer_summary(customer["id"])["balance"] == "0.00"
    assert all(r["status"] == "reversed" for r in reversed_invoice["receipts"])


def test_a_separate_receipt_blocks_reversal_until_it_is_reversed(shop: Shop) -> None:
    product = shop.product(tp="20")
    shop.receive(product, "100", price="10")
    customer = shop.customer(credit_limit="1000")
    invoice = shop.invoice(customer["id"], [shop.sale_line(product, "5")])
    posted = shop.post_sale(invoice["id"])
    receipt = shop.receipt(
        customer["id"], "100", allocations=[{"invoice_id": posted["id"], "amount": "100"}]
    )

    blocked = shop.reverse_sale(posted["id"], expect=409)
    assert blocked["code"] == "receipts_allocated"

    shop.post(f"/api/v1/customer-receipts/{receipt['id']}/reverse", idem=True)
    assert shop.reverse_sale(posted["id"])["status"] == "reversed"


def test_drafts_can_be_edited_and_deleted_but_posted_invoices_cannot(shop: Shop) -> None:
    product = shop.product(tp="20")
    shop.receive(product, "100", price="10")
    customer = shop.customer(credit_limit="1000")
    draft = shop.invoice(customer["id"], [shop.sale_line(product, "2")])

    edited = shop.client.patch(
        f"/api/v1/sales-invoices/{draft['id']}",
        headers=shop.headers,
        json={
            "customer_id": customer["id"],
            "items": [shop.sale_line(product, "3")],
            "paid_amount": "0",
        },
    )
    assert edited.status_code == 200
    assert edited.json()["net_amount"] == "60.00"

    posted = shop.post_sale(draft["id"])
    locked = shop.client.patch(
        f"/api/v1/sales-invoices/{posted['id']}",
        headers=shop.headers,
        json={
            "customer_id": customer["id"],
            "items": [shop.sale_line(product, "1")],
            "paid_amount": "0",
        },
    )
    assert locked.status_code == 409
    assert (
        shop.client.delete(
            f"/api/v1/sales-invoices/{posted['id']}", headers=shop.headers
        ).status_code
        == 409
    )

    other = shop.invoice(customer["id"], [shop.sale_line(product, "1")])
    assert (
        shop.client.delete(
            f"/api/v1/sales-invoices/{other['id']}", headers=shop.headers
        ).status_code
        == 204
    )


def test_the_period_lock_blocks_posting_into_a_closed_period(shop: Shop) -> None:
    product = shop.product(tp="20")
    shop.receive(product, "100", price="10")
    customer = shop.customer()
    invoice = shop.invoice(customer["id"], [shop.sale_line(product, "1")], paid="20")
    shop.put_period_lock(today().isoformat())

    refused = shop.post_sale(invoice["id"], expect=409)

    assert refused["code"] == "period_locked"


# --- Receipts and allocation ---------------------------------------------------------------------
def test_automatic_receipts_pay_the_oldest_invoice_first_and_keep_credit(shop: Shop) -> None:
    product = shop.product(tp="50")
    shop.receive(product, "100", price="10")
    customer = shop.customer(credit_limit="1000")
    older = shop.invoice(
        customer["id"],
        [shop.sale_line(product, "2")],
        business_date=in_days(-10),
    )
    older_posted = shop.post_sale(older["id"])
    newer = shop.invoice(customer["id"], [shop.sale_line(product, "1")])
    newer_posted = shop.post_sale(newer["id"])

    first = shop.receipt(customer["id"], "120")
    assert [(a["invoice_doc_no"], a["amount"]) for a in first["allocations"]] == [
        (older_posted["doc_no"], "100.00"),
        (newer_posted["doc_no"], "20.00"),
    ]
    assert shop.customer_summary(customer["id"])["balance"] == "30.00"

    second = shop.receipt(customer["id"], "80")
    assert second["allocated"] == "30.00"
    assert second["unallocated"] == "50.00"
    assert shop.customer_summary(customer["id"])["balance"] == "-50.00"  # credit held


def test_a_receipt_cannot_allocate_more_than_is_due(shop: Shop) -> None:
    product = shop.product(tp="50")
    shop.receive(product, "100", price="10")
    customer = shop.customer(credit_limit="1000")
    posted = shop.post_sale(shop.invoice(customer["id"], [shop.sale_line(product, "2")])["id"])

    refused = shop.receipt(
        customer["id"],
        "150",
        allocations=[{"invoice_id": posted["id"], "amount": "150"}],
        expect=422,
    )

    assert refused["code"] == "over_payment"


def test_a_receipt_cannot_pay_another_customers_invoice(shop: Shop) -> None:
    product = shop.product(tp="50")
    shop.receive(product, "100", price="10")
    owner_of_invoice = shop.customer(credit_limit="1000")
    other = shop.customer(credit_limit="1000")
    posted = shop.post_sale(
        shop.invoice(owner_of_invoice["id"], [shop.sale_line(product, "1")])["id"]
    )

    shop.receipt(
        other["id"], "10", allocations=[{"invoice_id": posted["id"], "amount": "10"}], expect=422
    )


def test_reversing_a_receipt_puts_the_balance_back(shop: Shop) -> None:
    product = shop.product(tp="50")
    shop.receive(product, "100", price="10")
    customer = shop.customer(credit_limit="1000")
    shop.post_sale(shop.invoice(customer["id"], [shop.sale_line(product, "2")])["id"])
    receipt = shop.receipt(customer["id"], "40")
    assert shop.customer_summary(customer["id"])["balance"] == "60.00"

    shop.post(f"/api/v1/customer-receipts/{receipt['id']}/reverse", idem=True)

    assert shop.customer_summary(customer["id"])["balance"] == "100.00"


# --- Reports, ledger and printing ---------------------------------------------------------------
def test_customer_aging_buckets_by_invoice_age_and_matches_the_ledger(shop: Shop) -> None:
    product = shop.product(tp="50")
    shop.receive(product, "100", price="10")
    customer = shop.customer(credit_limit="1000")
    shop.post_sale(shop.invoice(customer["id"], [shop.sale_line(product, "2")])["id"])

    today_report = shop.get("/api/v1/reports/customer-aging")
    row = next(r for r in today_report["rows"] if r["customer_id"] == customer["id"])
    assert (row["current"], row["total"], row["on_account"]) == ("100.00", "100.00", "0.00")

    in_45_days = shop.get("/api/v1/reports/customer-aging", as_of=in_days(45))
    row = next(r for r in in_45_days["rows"] if r["customer_id"] == customer["id"])
    assert (row["current"], row["days_31_60"]) == ("0.00", "100.00")

    ledger = shop.get(f"/api/v1/customers/{customer['id']}/ledger")
    assert ledger["total"] == 1
    assert ledger["items"][0]["debit"] == "100.00"


def test_sales_summary_reports_gross_profit_without_company_discounts(shop: Shop) -> None:
    product = shop.product(tp="20", dp="8")
    shop.receive(product, "100", price="10")
    customer = shop.customer()
    company = shop.supplier()
    shop.post_sale(shop.invoice(customer["id"], [shop.sale_line(product, "10")], paid="200")["id"])
    shop.post_sale(
        shop.invoice(
            customer["id"],
            [
                shop.sale_line(
                    product,
                    "5",
                    dealer_discount="10",
                    company_discount="5",
                    company_supplier_id=company,
                )
            ],
            paid="85",
        )["id"]
    )

    [row] = shop.get(
        "/api/v1/reports/sales-summary",
        date_from=today().isoformat(),
        date_to=today().isoformat(),
        group="day",
    )
    assert row["gross_amount"] == "300.00"
    assert row["dealer_discount"] == "10.00"
    assert row["company_discount"] == "5.00"
    assert row["net_amount"] == "285.00"
    assert row["cogs"] == "150.00"
    assert row["gross_profit"] == "140.00"  # (300 - 10 dealer discount) - 150 cogs


def test_printable_invoice_renders_for_a4_and_thermal(shop: Shop) -> None:
    product = shop.product(tp="20")
    shop.receive(product, "100", price="10")
    customer = shop.customer(name="Rahman & Sons")
    posted = shop.post_sale(
        shop.invoice(customer["id"], [shop.sale_line(product, "3")], paid="60")["id"]
    )

    a4 = shop.client.get(f"/api/v1/sales-invoices/{posted['id']}/print", headers=shop.headers)
    thermal = shop.client.get(
        f"/api/v1/sales-invoices/{posted['id']}/print",
        headers=shop.headers,
        params={"paper": "thermal80"},
    )

    assert a4.status_code == 200
    assert a4.headers["content-type"].startswith("text/html")
    assert posted["doc_no"] in a4.text
    assert "Rahman &amp; Sons" in a4.text  # escaped
    assert "80mm" in thermal.text
