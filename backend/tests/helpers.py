"""Test helpers that drive the real HTTP API the way the frontend does."""

import time
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.modules.accounting.models import Account, JournalEntry, JournalLine
from app.modules.catalog.models import Product, Uom
from app.modules.inventory.models import Location, StockBalance
from app.shared.dates import business_date

D = Decimal


def wait_until_blocked(engine: Engine, timeout: float = 15) -> None:
    """Return once some transaction in this database is waiting for a lock."""
    query = text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
    )
    deadline = time.monotonic() + timeout
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        while time.monotonic() < deadline:
            if conn.execute(query).scalar_one():
                return
            time.sleep(0.02)
    raise AssertionError("The second transaction never had to wait; the test proves nothing.")


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
        dp: str | None = None,
        tp: str | None = None,
        mrp: str | None = None,
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
        for field, value in (("dp", dp), ("tp", tp), ("mrp", mrp)):
            if value is not None:
                body[field] = value
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
        return dict(self.get("/api/v1/inventory/reconcile", fresh=True))

    def receive(
        self,
        product: dict[str, Any],
        qty: str,
        *,
        price: str = "10",
        batch: str | None = None,
        expiry_days: int | None = None,
    ) -> dict[str, Any]:
        """Buy and receive stock in one go (PO + posted GRN). Returns the GRN."""
        po = self.po(self.supplier(), [self.line(product, qty, price)])
        fields: dict[str, Any] = {"received_qty_base": qty}
        if batch is not None:
            fields["batch_no"] = batch
        if expiry_days is not None:
            fields["expiry_date"] = in_days(expiry_days)
        return self.grn(po, [fields])

    # -- sales ----------------------------------------------------------------------------
    def customer(self, *, credit_limit: str = "0", name: str | None = None) -> dict[str, Any]:
        n = self._next()
        body = {"name": name or f"Shop {n}", "credit_limit": credit_limit}
        return dict(self.post("/api/v1/customers", body, expect=201))

    def sale_line(self, product: dict[str, Any], qty: str, **extra: Any) -> dict[str, Any]:
        return {"product_id": product["id"], **extra, "qty": qty}

    def invoice(
        self,
        customer_id: int,
        lines: list[dict[str, Any]],
        *,
        paid: str = "0",
        account: str = "Cash in Hand",
        business_date: str | None = None,
        expect: int = 201,
    ) -> Any:
        body: dict[str, Any] = {"customer_id": customer_id, "items": lines, "paid_amount": paid}
        if paid != "0":
            body["payment_account_id"] = self.payment_account_id(account)
        if business_date:
            body["business_date"] = business_date
        return self.post("/api/v1/sales-invoices", body, expect=expect)

    def payment_account_id(self, name: str) -> int:
        options = self.get("/api/v1/sales-payment-accounts")
        return int(next(a["id"] for a in options if a["name"] == name))

    def post_sale(self, invoice_id: int, *, expect: int = 200, idem_key: str | None = None) -> Any:
        """Post an invoice. Pass `idem_key` to replay a request; otherwise a fresh key is sent."""
        headers = {"Idempotency-Key": idem_key} if idem_key else key()
        response = self.client.post(
            f"/api/v1/sales-invoices/{invoice_id}/post", headers={**self.headers, **headers}
        )
        assert response.status_code == expect, f"post: {response.status_code} {response.text}"
        return response.json()

    def reverse_sale(self, invoice_id: int, *, expect: int = 200) -> Any:
        return self.post(f"/api/v1/sales-invoices/{invoice_id}/reverse", expect=expect, idem=True)

    def receipt(
        self,
        customer_id: int,
        amount: str,
        *,
        allocations: list[dict[str, Any]] | None = None,
        account: str = "Cash in Hand",
        expect: int = 201,
    ) -> Any:
        body: dict[str, Any] = {
            "customer_id": customer_id,
            "amount": amount,
            "payment_account_id": self.payment_account_id(account),
            "allocations": allocations or [],
        }
        return self.post("/api/v1/customer-receipts", body, expect=expect, idem=True)

    def customer_summary(self, customer_id: int) -> dict[str, Any]:
        return dict(self.get(f"/api/v1/customers/{customer_id}/summary"))

    def put_preferences(self, **changes: Any) -> dict[str, Any]:
        current = self.get("/api/v1/settings")["preferences"]
        response = self.client.put(
            "/api/v1/settings/preferences", headers=self.headers, json={**current, **changes}
        )
        assert response.status_code == 200, response.text
        return dict(response.json())

    def put_period_lock(self, lock_date: str) -> None:
        response = self.client.put(
            "/api/v1/settings/period-lock", headers=self.headers, json={"lock_date": lock_date}
        )
        assert response.status_code == 200, response.text


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


def party_balance(db: Session, account_code: str, party_type: str, party_id: int) -> Decimal:
    """Σ(debit - credit) of one customer or supplier on a control account."""
    value = db.scalar(
        select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
        .select_from(JournalLine)
        .join(Account, Account.id == JournalLine.account_id)
        .where(
            Account.code == account_code,
            JournalLine.party_type == party_type,
            JournalLine.party_id == party_id,
        )
    )
    return D(value or 0)


def stock_at(db: Session, product_id: int, location_code: str) -> Decimal:
    """Base units of a product held in one location (all batches)."""
    value = db.scalar(
        select(func.coalesce(func.sum(StockBalance.qty_base), 0))
        .join(Location, Location.id == StockBalance.location_id)
        .where(StockBalance.product_id == product_id, Location.code == location_code)
    )
    return D(value or 0)
