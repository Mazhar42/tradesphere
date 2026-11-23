import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import D, Shop, in_days, today
from tests.helpers import key as idem_key


@pytest.fixture
def shop(client: TestClient, owner: dict[str, str], db: Session) -> Shop:
    return Shop(client, owner, db)


def yesterday() -> str:
    from datetime import timedelta  # noqa: PLC0415

    return (today() - timedelta(days=1)).isoformat()


def _body(shop: Shop, **overrides) -> dict:
    supplier = shop.supplier()
    product = shop.product(track_batch=True, track_expiry=True)
    customer = shop.post("/api/v1/customers", {"name": "Opening Shop"}, expect=201)["id"]
    cash = next(a for a in shop.get("/api/v1/payment-accounts") if a["name"] == "Cash in Hand")
    body = {
        "business_date": yesterday(),
        "stock": [
            {
                "product_id": product["id"],
                "batch_no": "OP1",
                "expiry_date": in_days(120),
                "qty_base": "200",
                "unit_cost": "12.5",
            },
        ],
        "customer_dues": [{"customer_id": customer, "amount": "15000"}],
        "supplier_balances": [{"supplier_id": supplier, "direction": "advance", "amount": "40000"}],
        "claims_receivable": [{"supplier_id": supplier, "amount": "6000"}],
        "cash": [{"payment_account_id": cash["id"], "amount": "25000"}],
        "notes": "Go-live load",
    }
    body.update(overrides)
    body["_ids"] = {
        "supplier": supplier,
        "product": product,
        "customer": customer,
        "cash": cash["id"],
    }
    return body


def _post(shop: Shop, body: dict, expect: int = 201):
    ids = body.pop("_ids", None)
    result = shop.post("/api/v1/opening-balances", body, expect=expect, idem=True)
    if ids:
        body["_ids"] = ids
    return result


def test_opening_balances_load_stock_dues_advances_claims_and_cash(shop: Shop) -> None:
    body = _body(shop)
    ids = body["_ids"]
    opening = _post(shop, body)
    assert opening["doc_no"].startswith("OPN-") and opening["status"] == "posted"
    assert D(opening["summary"]["stock_value"]) == D("2500")  # 200 x 12.5
    assert D(opening["summary"]["customer_dues"]) == D("15000")

    # Stock is on hand at the supplied cost.
    row = shop.stock(ids["product"])
    assert (
        row["qty_total"] == "200.000"
        and D(row["avg_cost"]) == D("12.5")
        and D(row["value"]) == D("2500")
    )
    # Supplier advance and pending claims show on the supplier account.
    assert D(shop.supplier_summary(ids["supplier"])["advance"]) == D("40000")
    # Cash drawer opened with 25,000.
    cash = next(a for a in shop.get("/api/v1/payment-accounts") if a["id"] == ids["cash"])
    assert D(cash["balance"]) == D("25000")

    # Everything is balanced against Opening Balance Equity (3900).
    report = shop.get("/api/v1/reports/trial-balance")
    assert report["balanced"] is True
    by_code = {r["account_code"]: r for r in report["rows"]}
    assert D(by_code["3900"]["credit"]) >= D("2500") + D("15000") + D("40000") + D("6000") + D(
        "25000"
    )
    assert shop.reconcile()["ok"] is True


def test_only_one_opening_balance_can_be_active_and_it_can_be_redone(shop: Shop) -> None:
    body = _body(shop)
    first = _post(shop, body)
    again = shop.post_raw(
        "/api/v1/opening-balances", {k: v for k, v in body.items() if k != "_ids"}
    )
    assert again.status_code == 409 and again.json()["code"] == "opening_already_posted"

    reversed_ = shop.post(f"/api/v1/opening-balances/{first['id']}/reverse", idem=True)
    assert reversed_["status"] == "reversed"
    assert shop.stock(body["_ids"]["product"])["qty_total"] == "0.000"
    assert D(shop.supplier_summary(body["_ids"]["supplier"])["ledger_balance"]) == 0
    assert shop.reconcile()["ok"] is True

    second = _post(shop, body)  # can be loaded again after reversal
    assert second["id"] != first["id"]
    states = {o["id"]: o["status"] for o in shop.get("/api/v1/opening-balances")}
    assert states[first["id"]] == "reversed" and states[second["id"]] == "posted"


def test_opening_balances_must_be_in_the_past_and_not_empty(shop: Shop) -> None:
    body = _body(shop)
    body["business_date"] = today().isoformat()
    ids = body.pop("_ids")
    assert shop.post_raw("/api/v1/opening-balances", body).status_code == 422
    empty = {"business_date": yesterday()}
    assert shop.post_raw("/api/v1/opening-balances", empty).status_code == 422
    assert ids


def test_opening_balance_validation(shop: Shop) -> None:
    body = _body(shop)
    ids = body.pop("_ids")
    dup = {**body, "customer_dues": [{"customer_id": ids["customer"], "amount": "1"}] * 2}
    assert shop.post_raw("/api/v1/opening-balances", dup).status_code == 422
    unknown = {**body, "customer_dues": [{"customer_id": 999999, "amount": "1"}]}
    assert shop.post_raw("/api/v1/opening-balances", unknown).status_code == 422
    no_batch = {
        **body,
        "stock": [{"product_id": ids["product"]["id"], "qty_base": "1", "unit_cost": "1"}],
    }
    response = shop.post_raw("/api/v1/opening-balances", no_batch)
    assert response.status_code == 422 and response.json()["code"] == "batch_required"


def test_opening_stock_can_include_expired_goods_in_the_expired_location(shop: Shop) -> None:
    body = _body(shop)
    ids = body.pop("_ids")
    body["stock"] = [
        {
            "product_id": ids["product"]["id"],
            "batch_no": "OLD",
            "expiry_date": in_days(-20),
            "location": "EXPIRED",
            "qty_base": "10",
            "unit_cost": "5",
        }
    ]
    shop.post("/api/v1/opening-balances", body, expect=201, idem=True)
    row = shop.stock(ids["product"])
    assert row["qty_expired"] == "10.000" and row["qty_sellable"] == "0.000"


def test_only_the_owner_can_load_opening_balances(
    client: TestClient, db: Session, make_user, owner
) -> None:
    make_user("mgr", ["manager"])
    from tests.conftest import login  # noqa: PLC0415

    manager = login(client, "mgr")
    assert client.get("/api/v1/opening-balances", headers=manager).status_code == 403
    response = client.post(
        "/api/v1/opening-balances",
        headers={**manager, **idem_key()},
        json={"business_date": yesterday()},
    )
    assert response.status_code == 403
    assert owner
