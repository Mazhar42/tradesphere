"""Imports (data-import-and-coexistence.md §15): validation, dry run, approval bound to the
preview, idempotency, per-domain opening loads, history without postings, and scenario A."""

import io
import zipfile
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.modules.accounting.models import JournalEntry
from app.modules.imports import service
from app.modules.integrity.reconcile import reconcile
from app.modules.inventory.models import InventoryMovement
from app.shared.dates import business_date
from tests.helpers import Shop

CUTOVER = business_date().isoformat()


class Imports:
    def __init__(self, client: TestClient, headers: dict[str, str], db: Session) -> None:
        self.client, self.headers, self.db = client, headers, db

    def upload(self, entity: str, content: str | bytes, **data: Any) -> Any:
        body = content.encode() if isinstance(content, str) else content
        response = self.client.post(
            "/api/v1/imports",
            headers=self.headers,
            files={"file": ("data.csv", body, "text/csv")},
            data={"entity": entity, **data},
        )
        assert response.status_code == 201, response.text
        return response.json()

    def approve(self, job: Any, *, expect: int = 200) -> Any:
        response = self.client.post(
            f"/api/v1/imports/{job['id']}/approve",
            headers=self.headers,
            json={"dry_run_digest": job["dry_run_digest"]},
        )
        assert response.status_code == expect, response.text
        return response.json()

    def job(self, job_id: int) -> Any:
        return self.client.get(f"/api/v1/imports/{job_id}", headers=self.headers).json()

    def errors(self, job_id: int) -> dict[int, str]:
        rows = self.client.get(
            f"/api/v1/imports/{job_id}/rows", headers=self.headers, params={"only": "error"}
        ).json()["items"]
        return {r["row_no"]: "; ".join(r["errors"]) for r in rows}

    def run(self, entity: str, content: str, **data: Any) -> Any:
        """Upload, approve and commit (as the worker would)."""
        job = self.upload(entity, content, **data)
        self.approve(job)
        service.run(self.db, job["id"])
        self.db.commit()
        return self.job(job["id"])

    def count(self, table: str) -> int:
        self.db.flush()
        return int(self.db.scalar(text(f"SELECT count(*) FROM {table}")) or 0)  # noqa: S608


@pytest.fixture
def imports(client: TestClient, owner: dict[str, str], db: Session) -> Imports:
    return Imports(client, owner, db)


PRODUCTS = (
    "SKU,Name,Unit,Brand,TP,MRP,track_batch,track_expiry,carton_size\n"
    "P-1,Biscuit,PCS,Olympic,10,12,no,no,24\n"
    "P-2,বিস্কুট,PCS,Olympic,২০,২৫,yes,yes,\n"
)


def test_bad_rows_are_explained_and_nothing_is_written_before_approval(
    imports: Imports,
) -> None:
    content = (
        "sku,name_en,unit,tp,barcode\n"
        "A-1,Good,PCS,10,111\n"
        "A-2,,PCS,10,\n"
        "A-3,Bad price,PCS,10.123,\n"
        "A-4,Lost digits,PCS,10,8.94E+12\n"
        "A-5,No such unit,XXX,10,\n"
        "A-1,Repeated,PCS,10,\n"
        "A-7,=cmd(),PCS,-1,\n"
    )

    job = imports.upload("products", content)

    assert job["status"] == "dry_run_ready"
    assert (job["counts"]["create"], job["counts"]["error"]) == (1, 6)
    errors = imports.errors(job["id"])
    assert "is required" in errors[3]
    assert "decimals" in errors[4]
    assert "shortened" in errors[5]
    assert "does not exist" in errors[6]
    assert "repeats row 2" in errors[7]
    assert imports.count("products") == 0
    report = imports.client.get(f"/api/v1/imports/{job['id']}/errors", headers=imports.headers)
    assert "'=cmd()" in report.content.decode("utf-8-sig")  # never a live formula


def test_importing_the_same_file_twice_changes_nothing(imports: Imports) -> None:
    imports.run("brands", "name\nOlympic\n")
    first = imports.run("products", PRODUCTS)
    assert first["status"] == "completed", first
    snapshot = imports.count("products"), imports.count("product_uoms")

    again = imports.upload("products", PRODUCTS)

    assert again["message"] == "This file was imported before."
    assert (again["counts"]["create"], again["counts"]["skip"]) == (0, 2)
    imports.approve(again)
    service.run(imports.db, again["id"])
    assert (imports.count("products"), imports.count("product_uoms")) == snapshot == (2, 1)


def test_updates_touch_only_permitted_fields(imports: Imports, db: Session) -> None:
    imports.run("brands", "name\nOlympic\n")
    imports.run("products", PRODUCTS, source_system="erp")
    changed = "sku,name_en,unit,tp,mrp\nP-1,Biscuit large,PCS,99,120\n"

    imports.run("products", changed, conflict_policy="update")
    row = db.execute(text("SELECT name_en, tp FROM products WHERE sku='P-1'")).one()
    assert (row.name_en, str(row.tp)) == ("Biscuit large", "10.00")  # price needs a tick

    imports.run("products", changed, conflict_policy="update", update_prices="true")
    db.expire_all()
    assert str(db.scalar(text("SELECT tp FROM products WHERE sku='P-1'"))) == "99.00"


def test_approval_is_bound_to_what_was_previewed(imports: Imports) -> None:
    job = imports.upload("brands", "name\nOlympic\nPran\n")
    imports.client.post("/api/v1/brands", headers=imports.headers, json={"name_en": "Pran"})

    refused = imports.approve(job, expect=409)

    assert refused["code"] == "preview_outdated"
    fresh = imports.job(job["id"])
    assert (fresh["counts"]["create"], fresh["counts"]["skip"]) == (1, 1)
    imports.approve(fresh)


def test_a_mapping_is_remembered_for_the_next_file(imports: Imports) -> None:
    content = "Item Code,Company Name,ph\nS-9,Pran,017\n"
    job = imports.upload("suppliers", content, source_system="old-erp")
    assert job["status"] == "needs_mapping"
    response = imports.client.put(
        f"/api/v1/imports/{job['id']}/mapping",
        headers=imports.headers,
        json={"mapping": {"external_id": "Item Code", "name": "Company Name", "phone": "ph"}},
    )
    assert response.json()["status"] == "dry_run_ready", response.text

    again = imports.upload("suppliers", content, source_system="old-erp")

    assert again["status"] == "dry_run_ready"
    assert again["mapping"]["name"] == "Company Name"


def test_scenario_a_excel_master_data_then_opening_positions(
    imports: Imports, client: TestClient, owner: dict[str, str], db: Session
) -> None:
    """A dealer moving from spreadsheets: masters, counted stock, balances, sign-off."""
    imports.run("brands", "name\nOlympic\n")
    imports.run("products", PRODUCTS)
    imports.run("suppliers", "code,name\nOLY,Olympic Ltd\n")
    imports.run("customers", "code,name,credit_limit\nC-1,Rahim Store,5000\n")
    expiry = (business_date() + timedelta(days=200)).strftime("%d/%m/%Y")
    stock = f"sku,qty,unit_cost,batch_no,expiry_date\nP-1,১০০,8.5,,\nP-2,10,15,B1,{expiry}\n"
    day = {"cutover_date": CUTOVER, "date_format": "DD/MM/YYYY"}
    loaded = imports.run("opening_stock", stock, **day)
    assert loaded["status"] == "completed", imports.errors(loaded["id"])
    imports.run("opening_receivables", "customer_code,amount,reference\nC-1,1200,OLD-7\n", **day)
    imports.run(
        "opening_payables",
        "supplier_code,direction,amount,reference\nOLY,payable,700,A\nOLY,advance,50,B\n",
        **day,
    )
    imports.run("opening_claims", "supplier_code,amount\nOLY,300\n", **day)
    imports.run("opening_cash", "account,amount\nCash in Hand,4000\n", **day)
    assert reconcile(db).issues == []

    report = client.get("/api/v1/imports/cutover", headers=owner).json()
    assert report["imported"] == {
        "stock_value": "1000.00",
        "receivables": "1200.00",
        "payables": "700.00",
        "advances": "50.00",
        "claims": "300.00",
        "cash": "4000.00",
    }
    declared = dict(report["imported"], cash="4100.00")
    refused = client.post(
        "/api/v1/imports/cutover/sign-off", headers=owner, json={"declared": declared}
    )
    assert refused.json()["code"] == "unexplained_difference"
    signed = client.post(
        "/api/v1/imports/cutover/sign-off",
        headers=owner,
        json={"declared": declared, "reasons": {"cash": "Petty cash counted later"}},
    )
    assert signed.status_code == 204, signed.text
    lock = client.get("/api/v1/settings", headers=owner).json()["period_lock"]["lock_date"]
    assert lock == (business_date() - timedelta(days=1)).isoformat()

    # Trading starts: the opened stock can be sold, and the books still agree.
    shop = Shop(client, owner, db)
    customer = db.scalar(text("SELECT id FROM customers WHERE code='C-1'"))
    product = db.scalar(text("SELECT id FROM products WHERE sku='P-1'"))
    invoice = shop.invoice(customer, [{"product_id": product, "qty": "5"}])
    shop.post_sale(invoice["id"])
    assert reconcile(db).issues == []


def test_the_same_item_cannot_be_opened_twice_and_a_load_reverses_cleanly(
    imports: Imports, client: TestClient, owner: dict[str, str], db: Session
) -> None:
    imports.run("customers", "code,name\nC-1,One\nC-2,Two\n")
    day = {"cutover_date": CUTOVER}
    imports.run("opening_receivables", "customer_code,amount\nC-1,100\n", **day)

    twice = imports.upload("opening_receivables", "customer_code,amount\nC-1,100\n", **day)
    imports.approve(twice)
    with pytest.raises(Exception, match="Already opened"):
        service.run(db, twice["id"])
    db.rollback()

    imports.run("opening_receivables", "customer_code,amount\nC-2,50\n", **day)  # later, more
    [second, first] = client.get("/api/v1/imports/openings", headers=owner).json()
    reversed_ = client.post(f"/api/v1/imports/openings/{first['id']}/reverse", headers=owner)
    assert reversed_.json()["status"] == "reversed", reversed_.text
    assert client.get("/api/v1/imports/cutover", headers=owner).json()["imported"][
        "receivables"
    ] == format(50, ".2f")
    assert reconcile(db).issues == []
    imports.run("opening_receivables", "customer_code,amount\nC-1,110\n", **day)  # reload
    assert second["total"] == "50.00"


def test_an_opening_load_with_any_error_cannot_be_approved(imports: Imports) -> None:
    imports.run("customers", "code,name\nC-1,One\n")
    job = imports.upload(
        "opening_receivables", "customer_code,amount\nC-1,10\nNOPE,5\n", cutover_date=CUTOVER
    )
    assert imports.approve(job, expect=409)["code"] == "rows_have_errors"


def test_history_posts_nothing(imports: Imports, db: Session) -> None:
    yesterday = (business_date() - timedelta(days=1)).isoformat()
    before = (
        db.scalar(select(func.count(JournalEntry.id))),
        db.scalar(select(func.count(InventoryMovement.id))),
    )
    content = f"external_id,type,date,number,party,amount\n1,sale,{yesterday},INV-9,Rahim,500\n"

    job = imports.run("history", content, cutover_date=CUTOVER)

    assert job["status"] == "completed"
    after = (
        db.scalar(select(func.count(JournalEntry.id))),
        db.scalar(select(func.count(InventoryMovement.id))),
    )
    assert after == before
    assert imports.count("imported_documents") == 1
    late = imports.upload("history", content.replace(yesterday, CUTOVER), cutover_date=CUTOVER)
    assert "before the cutover" in imports.errors(late["id"])[2]


def test_outstanding_orders_arrive_with_only_what_is_still_due(
    imports: Imports, client: TestClient, owner: dict[str, str]
) -> None:
    imports.run("brands", "name\nOlympic\n")
    imports.run("products", PRODUCTS)
    imports.run("suppliers", "code,name\nOLY,Olympic Ltd\n")
    orders = (
        "external_id,supplier_code,sku,qty,unit_price\nPO-77,OLY,P-1,40,8\nPO-77,OLY,P-2,5,15\n"
    )

    job = imports.run("purchase_orders", orders)
    again = imports.run("purchase_orders", orders)

    assert job["status"] == "completed" and again["counts"]["create"] == 0
    [order] = client.get("/api/v1/purchase-orders", headers=owner).json()["items"]
    detail = client.get(f"/api/v1/purchase-orders/{order['id']}", headers=owner).json()
    assert detail["status"] == "confirmed" and len(detail["items"]) == 2


def test_an_interrupted_commit_continues_without_repeating(
    imports: Imports, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "BATCH", 2)
    names = "name\n" + "".join(f"Brand {n}\n" for n in range(6))
    job = imports.upload("brands", names)
    imports.approve(job)
    real = service._apply_one
    calls = []

    def dies_in_the_third_batch(*args: Any) -> None:
        calls.append(1)
        if len(calls) == 5:
            raise KeyboardInterrupt
        real(*args)

    monkeypatch.setattr(service, "_apply_one", dies_in_the_third_batch)
    with pytest.raises(KeyboardInterrupt):
        service.run(db, job["id"])
    db.rollback()
    assert imports.count("brands") == 4  # two batches were committed

    monkeypatch.setattr(service, "_apply_one", real)
    service.run(db, job["id"])

    assert imports.count("brands") == 6
    assert imports.job(job["id"])["status"] == "completed"


def test_xlsx_is_read_and_formulas_are_not_evaluated(imports: Imports) -> None:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["code", "name"])
    sheet.append(["X1", "From Excel"])
    sheet.append(["X2", "=1+1"])
    out = io.BytesIO()
    book.save(out)

    job = imports.upload("suppliers", out.getvalue())

    rows = imports.client.get(f"/api/v1/imports/{job['id']}/rows", headers=imports.headers).json()
    assert [r["raw"]["name"] for r in rows["items"]] == ["From Excel", "=1+1"]


@pytest.mark.parametrize("kind", ["bomb", "binary", "empty", "no rows"])
def test_hostile_files_are_refused(imports: Imports, kind: str) -> None:
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", b"0" * 50_000_000)
    content = {
        "bomb": bomb.getvalue(),
        "binary": b"MZ\x00\x01\x02",
        "empty": b"",
        "no rows": b"name\n",
    }[kind]

    response = imports.client.post(
        "/api/v1/imports",
        headers=imports.headers,
        files={"file": ("x.xlsx", content, "application/octet-stream")},
        data={"entity": "brands"},
    )

    assert response.status_code == 422, response.text


def test_importing_needs_its_own_permission(client: TestClient, cashier: dict[str, str]) -> None:
    assert client.get("/api/v1/imports", headers=cashier).status_code == 403
    assert client.get("/api/v1/imports/templates/products", headers=cashier).status_code == 403
