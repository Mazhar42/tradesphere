"""Company claims: commissions, claims, settlements (cash and supplier credit), rejections."""

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.claims.models import ClaimableItem
from app.modules.claims.service import create_claimable
from tests.helpers import Shop, account_totals, key, party_balance, today

D = Decimal


@pytest.fixture
def shop(client: TestClient, owner: dict[str, str], db: Session) -> Shop:
    return Shop(client, owner, db)


def commission(
    shop: Shop, customer: int, supplier: int | None, amount: str = "10000", **extra: Any
) -> Any:
    body: dict[str, Any] = {
        "customer_id": customer,
        "amount": amount,
        "method": "cash",
        "payment_account_id": shop.payment_account_id("Cash in Hand"),
        "is_company_funded": supplier is not None,
        "supplier_id": supplier,
        **extra,
    }
    return shop.post("/api/v1/commission-payments", body, expect=201, idem=True)


def new_claim(shop: Shop, supplier: int, item_ids: list[int], *, expect: int = 201) -> Any:
    return shop.post(
        "/api/v1/company-claims",
        {"supplier_id": supplier, "claimable_item_ids": item_ids},
        expect=expect,
    )


def submit(shop: Shop, claim: dict[str, Any], ref: str | None = None) -> Any:
    return shop.post(
        f"/api/v1/company-claims/{claim['id']}/submit", {"company_ref": ref}, idem=True
    )


def settle(
    shop: Shop,
    claim: dict[str, Any],
    amount: str,
    method: str = "cash",
    *,
    expect: int = 201,
    idem_key: str | None = None,
) -> Any:
    body: dict[str, Any] = {"method": method, "amount": amount}
    if method != "supplier_credit":
        body["payment_account_id"] = shop.payment_account_id("Cash in Hand")
    headers = {**shop.headers, **({"Idempotency-Key": idem_key} if idem_key else key())}
    response = shop.client.post(
        f"/api/v1/company-claims/{claim['id']}/settlements", headers=headers, json=body
    )
    assert response.status_code == expect, f"{response.status_code} {response.text}"
    return response.json()


def reject(
    shop: Shop, claim: dict[str, Any], lines: list[dict[str, Any]], *, expect: int = 200
) -> Any:
    return shop.post(
        f"/api/v1/company-claims/{claim['id']}/reject-lines",
        {"lines": lines},
        expect=expect,
        idem=True,
    )


def claim_for_commission(
    shop: Shop, amount: str = "10000"
) -> tuple[dict[str, Any], int, dict[str, Any]]:
    supplier = shop.supplier()
    customer = shop.customer()
    payment = commission(shop, customer["id"], supplier, amount)
    claim = submit(shop, new_claim(shop, supplier, [payment["claimable_item_id"]]))
    return claim, supplier, payment


# --- Commissions ----------------------------------------------------------------------------------
def test_a_company_funded_commission_is_a_receivable_and_a_claimable(
    shop: Shop, db: Session
) -> None:
    supplier = shop.supplier()
    customer = shop.customer()
    payment = commission(shop, customer["id"], supplier)

    assert payment["doc_no"].startswith("CMP-")
    totals = account_totals(db, "commission_payment", payment["id"])
    assert totals["1300"] == (D(10000), D(0))
    assert totals["1000"] == (D(0), D(10000))
    item = db.get(ClaimableItem, payment["claimable_item_id"])
    assert item is not None
    assert (item.claim_type, item.claim_amount, item.status) == (
        "cash_commission",
        D("10000.00"),
        "open",
    )


def test_a_dealer_funded_commission_is_an_expense_and_credit_reduces_dues(
    shop: Shop, db: Session
) -> None:
    customer = shop.customer()
    payment = shop.post(
        "/api/v1/commission-payments",
        {
            "customer_id": customer["id"],
            "amount": "500",
            "method": "credit_to_account",
            "is_company_funded": False,
        },
        expect=201,
        idem=True,
    )
    assert payment["claimable_item_id"] is None
    totals = account_totals(db, "commission_payment", payment["id"])
    assert totals["5200"] == (D(500), D(0))
    assert party_balance(db, "1100", "customer", customer["id"]) == D(-500)


def test_commission_rules_are_validated(shop: Shop) -> None:
    customer = shop.customer()
    funded = shop.post(
        "/api/v1/commission-payments",
        {
            "customer_id": customer["id"],
            "amount": "100",
            "method": "credit_to_account",
            "is_company_funded": True,
        },
        expect=422,
        idem=True,
    )
    assert funded["errors"][0]["field"] == "supplier_id"
    needs_account = shop.post(
        "/api/v1/commission-payments",
        {"customer_id": customer["id"], "amount": "100", "method": "cash"},
        expect=422,
        idem=True,
    )
    assert needs_account["errors"][0]["field"] == "payment_account_id"


# --- Acceptance: settlement in cash and as supplier credit ----------------------------------------
def test_commission_settled_6k_cash_and_4k_as_supplier_credit_adds_4k_advance(
    shop: Shop, db: Session
) -> None:
    claim, supplier, payment = claim_for_commission(shop)
    assert claim["status"] == "submitted" and claim["doc_no"].startswith("CLM-")
    assert claim["total_claimed"] == "10000.00"

    after_cash = settle(shop, claim, "6000")
    assert after_cash["status"] == "partially_settled"
    after_credit = settle(shop, after_cash, "4000", "supplier_credit")

    assert after_credit["status"] == "settled"
    assert (after_credit["total_settled"], after_credit["outstanding"]) == ("10000.00", "0.00")
    assert party_balance(db, "2000", "supplier", supplier) == D(4000)  # advance with the company
    assert party_balance(db, "1300", "supplier", supplier) == D(0)  # nothing left to claim
    assert db.get(ClaimableItem, payment["claimable_item_id"]).status == "settled"  # type: ignore[union-attr]
    assert shop.reconcile()["ok"] is True


def test_a_partly_rejected_claim_writes_the_rejected_amount_off(shop: Shop, db: Session) -> None:
    claim, supplier, _ = claim_for_commission(shop)
    line = claim["lines"][0]

    rejected = reject(shop, claim, [{"claim_line_id": line["id"], "amount": "2000"}])
    assert rejected["status"] == "partially_settled"
    assert (rejected["total_rejected"], rejected["outstanding"]) == ("2000.00", "8000.00")
    [rejection] = [s for s in rejected["settlements"] if s["method"] == "rejection"]
    assert account_totals(db, "claim_settlement", rejection["id"])["5300"] == (D(2000), D(0))

    settled = settle(shop, rejected, "8000")
    assert settled["status"] == "settled"
    assert party_balance(db, "1300", "supplier", supplier) == D(0)


def test_a_fully_rejected_claim_is_rejected(shop: Shop) -> None:
    claim, _, _ = claim_for_commission(shop)
    done = reject(shop, claim, [{"claim_line_id": claim["lines"][0]["id"], "amount": "10000"}])
    assert done["status"] == "rejected"
    assert done["total_settled"] == "0.00"


def test_unclaimed_book_is_written_off_when_the_company_pays_less_than_the_book(
    shop: Shop, db: Session
) -> None:
    """Free goods are booked at cost (20) but claimed at dealer price (16)."""
    product = shop.product(tp="20", dp="8")
    shop.receive(product, "100", price="10")
    supplier = shop.supplier()
    customer = shop.customer()
    posted = shop.post_sale(
        shop.invoice(
            customer["id"],
            [
                shop.sale_line(
                    product,
                    "10",
                    free_qty="2",
                    free_is_company_scheme=True,
                    company_supplier_id=supplier,
                )
            ],
            paid="200",
        )["id"]
    )
    [free] = shop.get("/api/v1/claimable-items", claim_type="free_goods")["items"]
    assert (free["book_amount"], free["claim_amount"]) == ("20.00", "16.00")

    claim = submit(shop, new_claim(shop, supplier, [free["id"]]))
    settled = settle(shop, claim, "16")

    assert settled["status"] == "settled"
    [settlement] = settled["settlements"]
    totals = account_totals(db, "claim_settlement", settlement["id"])
    assert totals["1300"] == (D(0), D(20))  # 16 cleared by cash plus 4 written off
    assert totals["5300"] == (D(4), D(0))
    assert party_balance(db, "1300", "supplier", supplier) == D(0)
    assert posted["status"] == "posted"


def test_settling_above_the_book_amount_is_scheme_income(shop: Shop, db: Session) -> None:
    supplier = shop.supplier()
    item = create_claimable(
        db,
        supplier_id=supplier,
        claim_type="price_drop",
        source_doc_type="manual",
        source_doc_id=1,
        source_line_id=None,
        business_date=today(),
        book_amount=D(10),
        claim_amount=D(15),
        actor_id=None,
    )
    claim = submit(shop, new_claim(shop, supplier, [item.id]))
    settled = settle(shop, claim, "15")
    [settlement] = settled["settlements"]
    totals = account_totals(db, "claim_settlement", settlement["id"])
    assert totals["1000"] == (D(15), D(0))
    assert totals["1300"] == (D(0), D(10))
    assert totals["4300"] == (D(0), D(5))


# --- Claim rules ----------------------------------------------------------------------------------
def test_settlements_cannot_exceed_what_is_outstanding(shop: Shop) -> None:
    claim, _, _ = claim_for_commission(shop)
    assert settle(shop, claim, "10000.01", expect=422)["code"] == "over_settled"
    partial = settle(shop, claim, "9000")
    assert settle(shop, partial, "1000.01", expect=422)["code"] == "over_settled"
    reject_more = reject(
        shop, partial, [{"claim_line_id": claim["lines"][0]["id"], "amount": "1000.01"}], expect=422
    )
    assert reject_more["code"] == "over_rejected"


def test_only_open_items_of_the_same_company_can_be_claimed(shop: Shop) -> None:
    claim, supplier, payment = claim_for_commission(shop)
    again = new_claim(shop, supplier, [payment["claimable_item_id"]], expect=409)
    assert again["code"] == "item_not_open"
    other = shop.supplier()
    wrong = new_claim(shop, other, [payment["claimable_item_id"]], expect=422)
    assert wrong["code"] == "wrong_supplier"
    assert claim["status"] == "submitted"


def test_two_drafts_cannot_hold_the_same_item_and_drafts_can_be_deleted(shop: Shop) -> None:
    supplier = shop.supplier()
    payment = commission(shop, shop.customer()["id"], supplier)
    draft = new_claim(shop, supplier, [payment["claimable_item_id"]])
    assert draft["status"] == "draft" and draft["doc_no"] is None
    clash = new_claim(shop, supplier, [payment["claimable_item_id"]], expect=409)
    assert clash["code"] == "already_claimed"

    assert (
        shop.client.delete(
            f"/api/v1/company-claims/{draft['id']}", headers=shop.headers
        ).status_code
        == 204
    )
    assert new_claim(shop, supplier, [payment["claimable_item_id"]])["status"] == "draft"


def test_a_draft_claim_cannot_be_settled(shop: Shop) -> None:
    supplier = shop.supplier()
    payment = commission(shop, shop.customer()["id"], supplier)
    draft = new_claim(shop, supplier, [payment["claimable_item_id"]])
    assert settle(shop, draft, "100", expect=409)["code"] == "claim_not_open"


def test_settling_twice_with_the_same_key_settles_once(shop: Shop) -> None:
    claim, _, _ = claim_for_commission(shop)
    first = settle(shop, claim, "3000", idem_key="settle-key-1")
    replay = settle(shop, claim, "3000", idem_key="settle-key-1")
    assert replay == first
    assert shop.get(f"/api/v1/company-claims/{claim['id']}")["total_settled"] == "3000.00"


def test_claimable_items_can_be_listed_and_filtered(shop: Shop) -> None:
    supplier = shop.supplier()
    payment = commission(shop, shop.customer()["id"], supplier)
    page = shop.get("/api/v1/claimable-items", supplier_id=supplier, status="open")
    assert [i["id"] for i in page["items"]] == [payment["claimable_item_id"]]
    assert page["items"][0]["supplier_name"]


def test_claims_need_claims_write(
    shop: Shop, client: TestClient, cashier: dict[str, str], storekeeper: dict[str, str]
) -> None:
    supplier = shop.supplier()
    payment = commission(shop, shop.customer()["id"], supplier)
    body = {"supplier_id": supplier, "claimable_item_ids": [payment["claimable_item_id"]]}
    assert client.post("/api/v1/company-claims", headers=cashier, json=body).status_code == 403
    assert client.get("/api/v1/claimable-items", headers=cashier).status_code == 200
    assert client.get("/api/v1/claimable-items", headers=storekeeper).status_code == 403


# --- Sales returns against a claimed discount ---------------------------------------------------
def sell_with_company_discount(shop: Shop) -> tuple[dict[str, Any], int, dict[str, Any]]:
    product = shop.product(tp="20", dp="8")
    shop.receive(product, "100", price="10")
    supplier = shop.supplier()
    customer = shop.customer(credit_limit="1000")
    posted = shop.post_sale(
        shop.invoice(
            customer["id"],
            [shop.sale_line(product, "10", company_discount="30", company_supplier_id=supplier)],
        )["id"]
    )
    return product, supplier, posted


def return_units(shop: Shop, posted: dict[str, Any], qty: str, *, expect: int = 200) -> Any:
    item = posted["items"][0]
    ret = shop.post(
        "/api/v1/sales-returns",
        {
            "invoice_id": posted["id"],
            "refund_method": "credit",
            "items": [{"invoice_item_id": item["id"], "batch_id": None, "qty_base": qty}],
        },
        expect=201,
    )
    return shop.post(f"/api/v1/sales-returns/{ret['id']}/post", idem=True, expect=expect)


def test_a_return_shrinks_the_discount_line_of_a_submitted_claim(shop: Shop, db: Session) -> None:
    _, supplier, posted = sell_with_company_discount(shop)
    [item] = db.scalars(
        select(ClaimableItem).where(ClaimableItem.source_doc_id == posted["id"])
    ).all()
    claim = submit(shop, new_claim(shop, supplier, [item.id]))
    assert claim["total_claimed"] == "30.00"

    return_units(shop, posted, "4")  # gives back 12 of the 30

    updated = shop.get(f"/api/v1/company-claims/{claim['id']}")
    assert updated["total_claimed"] == "18.00"
    assert updated["lines"][0]["claimed_amount"] == "18.00"
    assert updated["status"] == "submitted"


def test_a_return_is_refused_once_the_company_has_settled_the_discount(
    shop: Shop, db: Session
) -> None:
    _, supplier, posted = sell_with_company_discount(shop)
    [item] = db.scalars(
        select(ClaimableItem).where(ClaimableItem.source_doc_id == posted["id"])
    ).all()
    claim = submit(shop, new_claim(shop, supplier, [item.id]))
    settle(shop, claim, "30")

    refused = return_units(shop, posted, "1", expect=409)
    assert refused["code"] == "discount_already_settled"
