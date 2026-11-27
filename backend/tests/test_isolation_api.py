"""The isolation suite (testing-and-acceptance.md §4): a release blocker.

Dealership B is filled with data through the API. Dealership A's owner then
- calls every route that takes an id, with B's ids;
- sends B's ids in every request body that references another record;
- reads every list, report, export and dashboard.
Nothing of B may show, and nothing of B may change. The routes come from the application's own
route table, so a new route without a case here fails the suite.
"""

import io
import uuid
import zipfile
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.tables import TENANT_TABLES
from app.core.tenant_context import clear_context, set_context
from app.main import app
from app.modules.users.models import User
from app.modules.users.schemas import UserCreate
from app.modules.users.service import create_user
from app.seed import provision_tenant
from app.shared.dates import business_date
from tests.conftest import DEFAULT_PASSWORD, TEST_TENANT, login
from tests.helpers import Shop, in_days

MARK = "ZZB"  # in every name dealership B gives anything
AMOUNT = "7777.00"  # a price no one else uses


class Other:
    """Dealership B and the ids of one record of every kind."""

    def __init__(self) -> None:
        self.tenant_id = 0
        self.ids: dict[str, int] = {}


def _build_other(client: TestClient, db: Session) -> Other:
    other = Other()
    other.tenant_id = provision_tenant(db, slug="zzb-dealer", legal_name=f"{MARK} Dealer").id
    owner = create_user(
        db,
        UserCreate(
            username="zzb-owner",
            full_name=f"{MARK} Owner",
            password=DEFAULT_PASSWORD,
            roles=["owner"],
        ),
        actor=None,
    )
    db.flush()
    headers = login(client, "zzb-owner")
    # Direct queries below (and in the helpers) must see B while it is being built.
    clear_context(db)
    set_context(db, tenant_id=other.tenant_id)
    shop = Shop(client, headers, db)
    ids = other.ids
    ids["user_id"] = owner.id

    def get(url: str, **params: Any) -> Any:
        try:
            return shop.get(url, **params)
        finally:
            set_context(db, tenant_id=other.tenant_id)

    def post(url: str, body: Any = None, *, expect: int = 201, idem: bool = False) -> Any:
        try:
            return shop.post(url, body, expect=expect, idem=idem)
        finally:
            set_context(db, tenant_id=other.tenant_id)

    cash = next(a["id"] for a in get("/api/v1/payment-accounts") if a["name"] == "Cash in Hand")
    bkash = next(a["id"] for a in get("/api/v1/payment-accounts") if a["name"] == "bKash")
    ids["payment_account_id"] = cash
    ids["opening_id"] = post(
        "/api/v1/opening-balances",
        {
            "business_date": (business_date() - timedelta(days=1)).isoformat(),
            "cash": [{"payment_account_id": cash, "amount": "900000"}],
            "notes": f"{MARK} opening",
        },
        idem=True,
    )["id"]
    ids["category_id"] = post("/api/v1/categories", {"name_en": f"{MARK} category"})["id"]
    ids["brand_id"] = post("/api/v1/brands", {"name_en": f"{MARK} brand"})["id"]
    ids["uom_id"] = post("/api/v1/uoms", {"code": MARK, "name_en": f"{MARK} unit"})["id"]
    base = next(u["id"] for u in get("/api/v1/uoms") if u["code"] == "PCS")
    ids["base_uom_id"] = base
    ids["supplier_id"] = post("/api/v1/suppliers", {"name": f"{MARK} supplier"})["id"]
    product = post(
        "/api/v1/products",
        {
            "sku": f"{MARK}-SKU",
            "name_en": f"{MARK} product",
            "base_uom_id": base,
            "brand_id": ids["brand_id"],
            "category_id": ids["category_id"],
            "tp": AMOUNT,
            "mrp": "8888",
            "dp": "7000",
            "reorder_level": "500",
            "track_batch": False,
            "track_expiry": False,
        },
    )
    ids["product_id"] = product["id"]
    ids["customer_id"] = post(
        "/api/v1/customers", {"name": f"{MARK} shop", "credit_limit": "9999999"}
    )["id"]

    po_body = {
        "supplier_id": ids["supplier_id"],
        "items": [{"product_id": product["id"], "qty": "50", "unit_price": "7000"}],
        "notes": f"{MARK} order",
    }
    po = post("/api/v1/purchase-orders", po_body)
    po = post(f"/api/v1/purchase-orders/{po['id']}/confirm", expect=200)
    ids["po_id"] = po["id"]
    ids["po_item_id"] = po["items"][0]["id"]
    grn_body = {
        "po_id": po["id"],
        "items": [{"po_item_id": po["items"][0]["id"], "received_qty_base": "20"}],
    }
    grn = post("/api/v1/goods-receipts", grn_body)
    post(f"/api/v1/goods-receipts/{grn['id']}/post", expect=200, idem=True)
    ids["grn_id"] = grn["id"]
    ids["payment_id"] = post(
        "/api/v1/supplier-payments",
        {"supplier_id": ids["supplier_id"], "amount": "100", "payment_account_id": cash},
        idem=True,
    )["id"]

    def sale(paid: str) -> dict[str, Any]:
        body = {
            "customer_id": ids["customer_id"],
            "notes": f"{MARK} sale",
            "paid_amount": paid,
            "items": [{"product_id": product["id"], "qty": "3"}],
        }
        if paid != "0":
            body["payment_account_id"] = cash
        return dict(post("/api/v1/sales-invoices", body))

    invoice = sale("0")
    post(f"/api/v1/sales-invoices/{invoice['id']}/post", expect=200, idem=True)
    ids["invoice_id"] = invoice["id"]
    ids["draft_invoice_id"] = sale("0")["id"]
    ids["receipt_id"] = post(
        "/api/v1/customer-receipts",
        {"customer_id": ids["customer_id"], "amount": "50", "payment_account_id": cash},
        idem=True,
    )["id"]
    returnable = get(f"/api/v1/sales-invoices/{invoice['id']}/returnable")
    row = (returnable if isinstance(returnable, list) else returnable["items"])[0]
    ids["invoice_item_id"] = row["invoice_item_id"]
    ids["sales_return_id"] = post(
        "/api/v1/sales-returns",
        {
            "invoice_id": invoice["id"],
            "refund_method": "credit",
            "items": [{"invoice_item_id": row["invoice_item_id"], "qty_base": "1"}],
        },
    )["id"]
    ids["purchase_return_id"] = post(
        "/api/v1/purchase-returns",
        {
            "supplier_id": ids["supplier_id"],
            "items": [{"product_id": product["id"], "qty_base": "1", "unit_value": "7000"}],
        },
    )["id"]
    ids["adjustment_id"] = post(
        "/api/v1/stock-adjustments",
        {"reason": "count", "items": [{"product_id": product["id"], "qty_delta": "1"}]},
    )["id"]
    ids["transfer_id"] = post(
        "/api/v1/stock-transfers",
        {
            "from_location": "SELLABLE",
            "to_location": "DAMAGED",
            "items": [{"product_id": product["id"], "qty_base": "1"}],
        },
    )["id"]
    post(
        "/api/v1/commission-payments",
        {
            "customer_id": ids["customer_id"],
            "amount": "70",
            "method": "cash",
            "payment_account_id": cash,
            "is_company_funded": True,
            "supplier_id": ids["supplier_id"],
        },
        idem=True,
    )
    claimables = get("/api/v1/claimable-items")["items"]
    ids["claimable_item_id"] = claimables[0]["id"]
    claim = post(
        "/api/v1/company-claims",
        {"supplier_id": ids["supplier_id"], "claimable_item_ids": [claimables[0]["id"]]},
    )
    ids["claim_id"] = claim["id"]
    ids["claim_line_id"] = claim["lines"][0]["id"]
    ids["expense_account_id"] = next(
        a["id"] for a in get("/api/v1/accounts") if a["code"] == "6010"
    )
    post(
        "/api/v1/expenses",
        {
            "expense_account_id": ids["expense_account_id"],
            "amount": "75",
            "payment_account_id": cash,
            "payee": f"{MARK} landlord",
        },
        idem=True,
    )
    post(
        "/api/v1/fund-transfers",
        {
            "from_payment_account_id": cash,
            "to_payment_account_id": bkash,
            "amount": "40",
            "notes": f"{MARK} transfer",
        },
        idem=True,
    )
    post(
        "/api/v1/owner-transactions",
        {"kind": "capital", "amount": "10", "payment_account_id": cash, "notes": f"{MARK} money"},
        idem=True,
    )
    post("/api/v1/payment-accounts", {"name": f"{MARK} bank", "kind": "bank"})
    ids["role_id"] = post(
        "/api/v1/roles",
        {"code": "zzb-clerk", "name": f"{MARK} clerk", "permissions": ["catalog:read"]},
    )["id"]
    ids["invitation_id"] = post(
        "/api/v1/invitations", {"note": f"{MARK} invitee", "roles": ["cashier"]}
    )["invitation"]["id"]
    clear_context(db)
    set_context(db, tenant_id=TEST_TENANT)
    return other


def _fingerprint(db: Session, tenant_id: int) -> dict[str, tuple[int, str]]:
    """Row count and content hash of every business table of one dealership."""
    db.flush()
    clear_context(db)
    set_context(db, tenant_id=tenant_id)
    try:
        result = {}
        for table in sorted(TENANT_TABLES):
            count, digest = db.execute(
                text(
                    "SELECT count(*), "  # noqa: S608
                    "coalesce(md5(string_agg(t::text, '|' ORDER BY t::text)), '') "
                    f"FROM {table} t"
                )
            ).one()
            result[table] = (count, digest)
        return result
    finally:
        clear_context(db)
        set_context(db, tenant_id=TEST_TENANT)


# --- The cases ------------------------------------------------------------------------------------

Body = Callable[[dict[str, int]], dict[str, Any]]


def _line(ids: dict[str, int]) -> dict[str, Any]:
    return {"product_id": ids["product_id"], "qty": "1"}


def _po(ids: dict[str, int]) -> dict[str, Any]:
    return {
        "supplier_id": ids["supplier_id"],
        "items": [{"product_id": ids["product_id"], "qty": "1", "unit_price": "1"}],
    }


def _invoice(ids: dict[str, int]) -> dict[str, Any]:
    return {"customer_id": ids["customer_id"], "items": [_line(ids)]}


def _money(ids: dict[str, int], **extra: Any) -> dict[str, Any]:
    return {"amount": "1", "payment_account_id": ids["payment_account_id"], **extra}


# Bodies for routes that need one: valid in shape, so that the answer comes from looking the
# ids up and not from request validation.
BODIES: dict[tuple[str, str], Body] = {
    ("PATCH", "/users/{user_id}"): lambda i: {"full_name": "x"},
    ("PATCH", "/roles/{role_id}"): lambda i: {"name": "x"},
    ("POST", "/users/{user_id}/reset-password"): lambda i: {"new_password": "Sneaky-Passw0rd"},
    ("PATCH", "/categories/{category_id}"): lambda i: {"name_en": "x"},
    ("PATCH", "/brands/{brand_id}"): lambda i: {"name_en": "x"},
    ("PATCH", "/uoms/{uom_id}"): lambda i: {"name_en": "x"},
    ("PATCH", "/products/{product_id}"): lambda i: {"name_en": "x"},
    ("PATCH", "/customers/{customer_id}"): lambda i: {"name": "x"},
    ("PATCH", "/suppliers/{supplier_id}"): lambda i: {"name": "x"},
    ("PUT", "/purchase-orders/{po_id}"): _po,
    ("POST", "/purchase-orders/{po_id}/close-short"): lambda i: {},
    ("POST", "/supplier-payments/{payment_id}/allocate"): lambda i: {
        "allocations": [{"po_id": i["po_id"], "amount": "1"}]
    },
    ("PATCH", "/sales-invoices/{invoice_id}"): _invoice,
    ("POST", "/company-claims/{claim_id}/submit"): lambda i: {},
    ("POST", "/company-claims/{claim_id}/settlements"): lambda i: {
        "method": "cash",
        **_money(i),
    },
    ("POST", "/company-claims/{claim_id}/reject-lines"): lambda i: {
        "lines": [{"claim_line_id": i["claim_line_id"], "amount": "1"}]
    },
    ("PATCH", "/payment-accounts/{payment_account_id}"): lambda i: {"name": "x"},
    # creating routes whose body names other records
    ("POST", "/categories"): lambda i: {"name_en": "x", "parent_id": i["category_id"]},
    ("POST", "/products"): lambda i: {
        "sku": "X-1",
        "name_en": "x",
        "base_uom_id": i["base_uom_id"],
        "brand_id": i["brand_id"],
        "category_id": i["category_id"],
    },
    ("POST", "/purchase-orders"): _po,
    ("POST", "/goods-receipts"): lambda i: {
        "po_id": i["po_id"],
        "items": [{"po_item_id": i["po_item_id"], "received_qty_base": "1"}],
    },
    ("POST", "/supplier-payments"): lambda i: _money(i, supplier_id=i["supplier_id"]),
    ("POST", "/sales-invoices"): _invoice,
    ("POST", "/customer-receipts"): lambda i: _money(i, customer_id=i["customer_id"]),
    ("POST", "/sales-returns"): lambda i: {
        "invoice_id": i["invoice_id"],
        "refund_method": "credit",
        "items": [{"invoice_item_id": i["invoice_item_id"], "qty_base": "1"}],
    },
    ("POST", "/purchase-returns"): lambda i: {
        "supplier_id": i["supplier_id"],
        "items": [{"product_id": i["product_id"], "qty_base": "1", "unit_value": "1"}],
    },
    ("POST", "/stock-adjustments"): lambda i: {
        "reason": "count",
        "items": [{"product_id": i["product_id"], "qty_delta": "1"}],
    },
    ("POST", "/stock-transfers"): lambda i: {
        "from_location": "SELLABLE",
        "to_location": "DAMAGED",
        "items": [{"product_id": i["product_id"], "qty_base": "1"}],
    },
    ("POST", "/company-claims"): lambda i: {
        "supplier_id": i["supplier_id"],
        "claimable_item_ids": [i["claimable_item_id"]],
    },
    ("POST", "/commission-payments"): lambda i: _money(
        i, customer_id=i["customer_id"], method="cash"
    ),
    ("POST", "/expenses"): lambda i: _money(i, expense_account_id=i["expense_account_id"]),
    ("POST", "/fund-transfers"): lambda i: {
        "from_payment_account_id": i["payment_account_id"],
        "to_payment_account_id": i["payment_account_id"] + 1,
        "amount": "1",
    },
    ("POST", "/owner-transactions"): lambda i: _money(i, kind="capital"),
    ("POST", "/opening-balances"): lambda i: {
        "business_date": (business_date() - timedelta(days=1)).isoformat(),
        "cash": [{"payment_account_id": i["payment_account_id"], "amount": "1"}],
    },
}

# Path parameters whose name alone does not say which record.
PATH_ID_BY_PREFIX = {
    ("/sales-returns", "return_id"): "sales_return_id",
    ("/purchase-returns", "return_id"): "purchase_return_id",
    ("/customer-receipts", "receipt_id"): "receipt_id",
    ("/sales-invoices", "invoice_id"): "invoice_id",
}


def _routes() -> list[tuple[str, str, list[str], list[str], bool]]:
    """(method, path, path ids, query ids, body names an id) for every API route."""
    schema = app.openapi()
    components = schema["components"]["schemas"]

    def names_an_id(node: dict[str, Any], seen: frozenset[str] = frozenset()) -> bool:
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            return name not in seen and names_an_id(components[name], seen | {name})
        for key, child in node.get("properties", {}).items():
            if key.endswith(("_id", "_ids")) or names_an_id(child, seen):
                return True
        children = [*node.get("anyOf", []), *([node["items"]] if "items" in node else [])]
        return any(names_an_id(child, seen) for child in children)

    found = []
    for path, operations in schema["paths"].items():
        if not path.startswith("/api/v1/") or path.startswith("/api/v1/auth/"):
            continue
        for method, operation in operations.items():
            parameters = operation.get("parameters", [])
            body = operation.get("requestBody", {}).get("content", {}).get("application/json")
            found.append(
                (
                    method.upper(),
                    path.removeprefix("/api/v1"),
                    [p["name"] for p in parameters if p["in"] == "path"],
                    [
                        p["name"]
                        for p in parameters
                        if p["in"] == "query" and p["name"].endswith("_id")
                    ],
                    bool(body) and names_an_id(body["schema"]),
                )
            )
    return found


def _id_for(path: str, name: str, ids: dict[str, int]) -> int:
    for (prefix, parameter), key in PATH_ID_BY_PREFIX.items():
        if parameter == name and path.startswith(prefix):
            return ids[key]
    if name not in ids:
        raise AssertionError(f"No dealership-B record for '{name}' ({path}): add one to the suite.")
    return ids[name]


@pytest.fixture
def two(
    client: TestClient, db: Session, make_user: Callable[..., User]
) -> tuple[dict[str, str], Other]:
    make_user("owner-a", ["owner"])
    other = _build_other(client, db)
    return login(client, "owner-a"), other


def test_no_route_accepts_another_dealerships_ids(
    client: TestClient, db: Session, two: tuple[dict[str, str], Other]
) -> None:
    headers, other = two
    before = _fingerprint(db, other.tenant_id)
    assert before["sales_invoices"][0] == 2, "dealership B was not built"
    problems: list[str] = []
    checked = 0

    for method, path, path_ids, _, body_ids in _routes():
        if not path_ids and not (body_ids and method in ("POST", "PUT", "PATCH")):
            continue
        key = (method, path)
        url = "/api/v1" + path
        for name in path_ids:
            url = url.replace("{" + name + "}", str(_id_for(path, name, other.ids)))
        body = BODIES[key](other.ids) if key in BODIES else None
        assert body is not None or not body_ids, f"{method} {path} needs a case in BODIES"
        response = client.request(
            method,
            url,
            headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
            json=body,
        )
        checked += 1
        code = response.json().get("code") if response.status_code >= 400 else None
        # "Not there" (404) or "unknown reference" (422), exactly as for an id that never existed.
        # 200 means it worked on B's record; 409 means it read B's record's state.
        allowed = {404, 422}
        if response.status_code not in allowed or code == "validation_error":
            problems.append(f"{method} {path}: {response.status_code} {response.text[:200]}")

    assert checked >= 75, checked
    assert not problems, "\n".join(problems)
    assert _fingerprint(db, other.tenant_id) == before, "dealership B was changed"


def _readable(content: bytes, content_type: str) -> str:
    if "spreadsheet" in content_type:
        with zipfile.ZipFile(io.BytesIO(content)) as book:
            return " ".join(book.read(n).decode("utf-8", "replace") for n in book.namelist())
    return content.decode("utf-8", "replace")


def test_no_list_report_or_export_shows_another_dealership(
    client: TestClient, two: tuple[dict[str, str], Other]
) -> None:
    headers, other = two
    problems: list[str] = []
    checked = 0
    window = {"from": in_days(-400), "to": in_days(1), "date_from": in_days(-400)}

    for method, path, path_ids, query_ids, _ in _routes():
        if method != "GET" or path_ids:
            continue
        variants: list[dict[str, Any]] = [{}, {"q": MARK}, {"format": "xlsx"}]
        if query_ids:
            variants.append({n: other.ids.get(n, other.ids["product_id"]) for n in query_ids})
        for extra in variants:
            response = client.get(
                "/api/v1" + path, headers=headers, params={**window, "date_to": in_days(1), **extra}
            )
            if response.status_code >= 500:
                problems.append(f"{path} {extra}: {response.status_code}")
                continue
            if response.status_code != 200:
                continue
            checked += 1
            body = _readable(response.content, response.headers.get("content-type", ""))
            if "q" in extra:
                body = body.replace(f'"{MARK}"', "")  # the query itself, if echoed
            for secret in (MARK, "7777", "zzb-"):
                if secret in body:
                    at = body.index(secret)
                    problems.append(f"{path} {extra}: ...{body[max(0, at - 80) : at + 40]}...")

    assert checked > 40, checked
    assert not problems, "\n".join(sorted(set(problems)))


def test_the_other_dealership_does_see_its_own_data(
    client: TestClient, two: tuple[dict[str, str], Other]
) -> None:
    """Control for the test above: the markers really are there to be found."""
    headers = login(client, "zzb-owner")
    for path in ("/products", "/customers", "/suppliers", "/users", "/audit-logs"):
        assert MARK.lower() in client.get("/api/v1" + path, headers=headers).text.lower(), path


def test_a_suspended_dealership_is_closed(
    client: TestClient, db: Session, two: tuple[dict[str, str], Other]
) -> None:
    headers, _ = two
    db.execute(text("UPDATE tenants SET status = 'suspended' WHERE id = :t"), {"t": TEST_TENANT})
    db.commit()

    refused = client.get("/api/v1/products", headers=headers)

    assert (refused.status_code, refused.json()["code"]) == (403, "tenant_unavailable")
    again = client.post(
        "/api/v1/auth/login", json={"username": "owner-a", "password": DEFAULT_PASSWORD}
    )
    assert again.status_code == 401
