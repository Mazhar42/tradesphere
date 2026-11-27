"""Global search, the shared matcher, supplier price lists and discovery."""

from typing import Any
from unicodedata import normalize

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.modules.purchasing.models import SupplierPrice, SupplierProduct
from app.modules.purchasing.supplier_catalog import upsert_supplier_product
from app.shared.search import is_gtin
from tests.helpers import key

API = "/api/v1"
# "ড়" typed as one letter and as letter + dot are different bytes for the same word.
BANGLA_NAME = "গুঁড়া সাবান"


@pytest.fixture()
def auth(client: TestClient, owner: dict[str, str]) -> dict[str, str]:
    return owner


def _post(client: TestClient, auth: dict[str, str], path: str, **body: Any) -> dict[str, Any]:
    response = client.post(f"{API}{path}", headers={**auth, **key()}, json=body)
    assert response.status_code in (200, 201), response.text
    return dict(response.json())


def _product(client: TestClient, auth: dict[str, str], **values: Any) -> dict[str, Any]:
    units = client.get(f"{API}/uoms", headers=auth).json()
    piece = next(u["id"] for u in units if u["code"] == "PCS")
    return _post(client, auth, "/products", base_uom_id=piece, **values)


def _search(client: TestClient, auth: dict[str, str], q: str) -> dict[str, list[dict[str, Any]]]:
    response = client.get(f"{API}/search", headers=auth, params={"q": q})
    assert response.status_code == 200, response.text
    return {g["group"]: g["hits"] for g in response.json()}


def _titles(found: dict[str, list[dict[str, Any]]], group: str) -> list[str]:
    return [h["title"] for h in found.get(group, [])]


def test_codes_rank_before_names_and_words_match_in_any_order(
    client: TestClient, auth: dict[str, str]
) -> None:
    _product(client, auth, sku="TEA-400", name_en="Seylon Gold Tea 400g")
    _product(client, auth, sku="X-1", name_en="Gift box with TEA-400 inside")
    _post(client, auth, "/customers", code="C-TEA", name="Gold Tea Stall")

    found = _search(client, auth, "tea-400")
    assert _titles(found, "products")[0] == "Seylon Gold Tea 400g"
    found = _search(client, auth, "400g gold")
    assert _titles(found, "products") == ["Seylon Gold Tea 400g"]
    assert _titles(found, "customers") == []
    found = _search(client, auth, "gold tea")
    assert _titles(found, "customers") == ["Gold Tea Stall"]


def test_wildcards_are_taken_literally(client: TestClient, auth: dict[str, str]) -> None:
    _product(client, auth, sku="A-1", name_en="Plain Biscuit")
    _product(client, auth, sku="A-2", name_en="100% Juice")
    assert _titles(_search(client, auth, "0% "), "products") == ["100% Juice"]
    assert _search(client, auth, "__") == {}


def test_bangla_in_either_unicode_form_and_bangla_digits(
    client: TestClient, auth: dict[str, str]
) -> None:
    _product(client, auth, sku="SOAP-20", name_en="Soap Powder", name_bn=BANGLA_NAME)
    for form in ("NFC", "NFD"):
        found = _search(client, auth, normalize(form, BANGLA_NAME))
        assert _titles(found, "products") == ["Soap Powder"], form
    assert _titles(_search(client, auth, "SOAP-২০"), "products") == ["Soap Powder"]


def test_a_spelling_slip_finds_the_product_only_when_nothing_else_does(
    client: TestClient, auth: dict[str, str]
) -> None:
    _product(client, auth, sku="B-1", name_en="Chocolate Biscuit")
    assert _titles(_search(client, auth, "choclate"), "products") == ["Chocolate Biscuit"]
    assert _search(client, auth, "zzzqqq") == {}


def test_each_group_needs_its_permission(
    client: TestClient, auth: dict[str, str], storekeeper: dict[str, str]
) -> None:
    _product(client, auth, sku="M-1", name_en="Mango Bar")
    _post(client, auth, "/customers", code="C-M", name="Mango Stores")
    assert set(_search(client, auth, "mango")) == {"products", "customers"}
    allowed = client.get(f"{API}/auth/me", headers=storekeeper).json()["permissions"]
    assert "customers:read" not in allowed
    assert set(_search(client, storekeeper, "mango")) == {"products"}
    assert client.get(f"{API}/search", params={"q": "mango"}).status_code == 401


def test_supplier_lines_keep_price_history_and_are_never_linked_by_guess(
    client: TestClient, auth: dict[str, str], db: Session
) -> None:
    supplier = _post(client, auth, "/suppliers", code="S-1", name="Square Foods")
    product = _product(client, auth, sku="CH-1", name_en="Chanachur 150g")

    def push(**values: Any) -> SupplierProduct:
        return upsert_supplier_product(
            db, supplier_id=supplier["id"], supplier_sku="SQ-77", source="test", values=values
        )

    item = push(description="Chanachur 150g", price=20)
    push(description="Chanachur 150g", price=20)
    push(description="Chanachur 150g", price=22)
    db.commit()
    assert [p.price for p in db.scalars(select(SupplierPrice).order_by(SupplierPrice.id))] == [
        20,
        22,
    ]
    assert item.product_id is None

    queue = client.get(f"{API}/supplier-products", headers=auth, params={"unlinked": True})
    assert [r["supplier_sku"] for r in queue.json()["items"]] == ["SQ-77"]
    found = client.get(f"{API}/search/discover", headers=auth, params={"q": "SQ-77"}).json()
    assert [(c["source"], c["confidence"], c["product_id"]) for c in found] == [
        ("supplier_catalog", "likely", None)
    ]

    linked = client.post(
        f"{API}/supplier-products/{item.id}/link",
        headers=auth,
        json={"product_id": product["id"]},
    )
    assert linked.status_code == 200, linked.text
    other = _product(client, auth, sku="CH-2", name_en="Chanachur 300g")
    again = client.post(
        f"{API}/supplier-products/{item.id}/link", headers=auth, json={"product_id": other["id"]}
    )
    assert again.status_code == 409
    history = client.get(f"{API}/supplier-products/{item.id}/prices", headers=auth).json()
    assert [h["price"] for h in history] == ["22.0000", "20.0000"]
    with pytest.raises(Exception, match=r"append-only|permission denied"):
        db.execute(text("UPDATE supplier_price_history SET price = 1"))
    db.rollback()


def test_a_company_price_list_imports_without_creating_products(
    client: TestClient, auth: dict[str, str], db: Session
) -> None:
    _post(client, auth, "/suppliers", code="S-9", name="Pran")
    csv = "supplier_code,supplier_sku,description,price\nS-9,P-1,Mango Juice 200ml,18\n"
    uploaded = client.post(
        f"{API}/imports",
        headers=auth,
        data={"entity": "supplier_products"},
        files={"file": ("list.csv", csv.encode(), "text/csv")},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert client.get(f"{API}/products", headers=auth).json()["total"] == 0


def test_lists_can_be_read_by_cursor(client: TestClient, auth: dict[str, str]) -> None:
    for number in range(5):
        _post(client, auth, "/customers", code=f"K-{number}", name=f"Shop {number}")
    seen: list[int] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"page_size": 2, **({"cursor": cursor} if cursor else {})}
        page = client.get(f"{API}/audit-logs", headers=auth, params=params).json()
        seen += [row["id"] for row in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == sorted(set(seen), reverse=True)
    assert len(seen) == page["total"]
    bad = client.get(f"{API}/audit-logs", headers=auth, params={"cursor": "x|y"})
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_cursor"


@pytest.mark.parametrize(
    ("code", "valid"),
    [("4006381333931", True), ("4006381333932", False), ("96385074", True), ("12345", False)],
)
def test_barcode_check_digit(code: str, valid: bool) -> None:
    assert is_gtin(code) is valid
