"""Test helpers that drive the real HTTP API the way the frontend does."""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.catalog.models import Product, Uom
from app.shared.dates import business_date

D = Decimal


def today() -> date:
    return business_date()


def in_days(days: int) -> str:
    return (today() + timedelta(days=days)).isoformat()


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


def create_product_row(
    db: Session, sku: str, *, track_batch: bool = False, track_expiry: bool = False
) -> Product:
    """Insert a product directly (for engine-level tests)."""
    pcs = db.scalar(select(Uom).where(Uom.code == "PCS"))
    assert pcs is not None
    product = Product(
        sku=sku,
        name_en=f"Product {sku}",
        base_uom_id=pcs.id,
        dp=D(0),
        tp=D(0),
        mrp=D(0),
        reorder_level=D(0),
        track_batch=track_batch,
        track_expiry=track_expiry,
    )
    db.add(product)
    db.flush()
    return product


class Shop:
    """Convenience wrapper: owner-authenticated API calls with sensible defaults."""

    def __init__(self, client: TestClient, headers: dict[str, str], db: Session) -> None:
        self.client = client
        self.headers = headers
        self.db = db
        self._n = 0

    def _next(self) -> int:
        self._n += 1
        return self._n

    # -- plumbing -------------------------------------------------------------------
    def get(self, url: str, **params: Any) -> Any:
        response = self.client.get(url, headers=self.headers, params=params or None)
        assert response.status_code == 200, f"{url}: {response.status_code} {response.text}"
        return response.json()

    def post(self, url: str, json: Any = None, *, expect: int = 200, idem: bool = False) -> Any:
        headers = {**self.headers, **(key() if idem else {})}
        response = self.client.post(url, headers=headers, json=json)
        assert response.status_code == expect, f"{url}: {response.status_code} {response.text}"
        return response.json() if response.content else None

    def post_raw(
        self, url: str, json: Any = None, *, idem: bool = True, headers: Any = None
    ) -> Any:
        merged = {**self.headers, **(key() if idem else {}), **(headers or {})}
        return self.client.post(url, headers=merged, json=json)

    # -- master data -------------------------------------------------------------------
    def supplier(self, name: str | None = None) -> int:
        n = self._next()
        return int(
            self.post("/api/v1/suppliers", {"name": name or f"Company {n}"}, expect=201)["id"]
        )

    def uom_id(self, code: str) -> int:
        return int(next(u["id"] for u in self.get("/api/v1/uoms") if u["code"] == code))

    def product(
        self,
        sku: str | None = None,
        *,
        track_batch: bool = False,
        track_expiry: bool = False,
        carton: int | None = None,
        reorder_level: str = "0",
    ) -> dict[str, Any]:
        n = self._next()
        body: dict[str, Any] = {
            "sku": sku or f"SKU{self.db.info.setdefault('seq', 0)}-{n}-{uuid.uuid4().hex[:6]}",
            "name_en": f"Item {n}",
            "base_uom_id": self.uom_id("PCS"),
            "track_batch": track_batch,
            "track_expiry": track_expiry,
            "reorder_level": reorder_level,
        }
        if carton:
            body["uoms"] = [{"uom_id": self.uom_id("CTN"), "factor_to_base": str(carton)}]
        return dict(self.post("/api/v1/products", body, expect=201))

    # -- purchasing -----------------------------------------------------------------------
    def po(
        self,
        supplier_id: int,
        lines: list[dict[str, Any]],
        *,
        confirm: bool = True,
        business_date: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"supplier_id": supplier_id, "items": lines}
        if business_date:
            body["business_date"] = business_date
        po = self.post("/api/v1/purchase-orders", body, expect=201)
        if confirm:
            po = self.post(f"/api/v1/purchase-orders/{po['id']}/confirm")
        return dict(po)

    def line(self, product: dict[str, Any], qty: str, price: str, **extra: Any) -> dict[str, Any]:
        return {"product_id": product["id"], "qty": qty, "unit_price": price, **extra}

    def grn(
        self,
        po: dict[str, Any],
        lines: list[dict[str, Any]],
        *,
        post: bool = True,
        business_date: str | None = None,
    ) -> dict[str, Any]:
        """lines: dicts with `item` (index into po items) plus received_qty_base etc."""
        items = []
        for spec in lines:
            fields = dict(spec)
            po_item = po["items"][fields.pop("item", 0)]
            items.append({"po_item_id": po_item["id"], **fields})
        body: dict[str, Any] = {"po_id": po["id"], "items": items}
        if business_date:
            body["business_date"] = business_date
        grn = self.post("/api/v1/goods-receipts", body, expect=201)
        if post:
            grn = self.post(f"/api/v1/goods-receipts/{grn['id']}/post", idem=True)
        return dict(grn)

    def pay(
        self,
        supplier_id: int,
        amount: str,
        *,
        allocations: list[dict[str, Any]] | None = None,
        account: str = "Cash in Hand",
        expect: int = 201,
        business_date: str | None = None,
    ) -> Any:
        accounts = self.get("/api/v1/payment-accounts")
        account_id = next(a["id"] for a in accounts if a["name"] == account)
        body: dict[str, Any] = {
            "supplier_id": supplier_id,
            "amount": amount,
            "payment_account_id": account_id,
            "allocations": allocations or [],
        }
        if business_date:
            body["business_date"] = business_date
        return self.post("/api/v1/supplier-payments", body, expect=expect, idem=True)

    def po_detail(self, po_id: int) -> dict[str, Any]:
        return dict(self.get(f"/api/v1/purchase-orders/{po_id}"))

    def stock(self, product: dict[str, Any]) -> dict[str, Any]:
        rows = self.get("/api/v1/inventory/stock", q=product["sku"])["items"]
        return dict(next(r for r in rows if r["product_id"] == product["id"]))

    def supplier_summary(self, supplier_id: int) -> dict[str, Any]:
        return dict(self.get(f"/api/v1/suppliers/{supplier_id}/summary"))

    def reconcile(self) -> dict[str, Any]:
        return dict(self.get("/api/v1/inventory/reconcile"))
