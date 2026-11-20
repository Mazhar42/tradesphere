from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError
from app.modules.catalog.models import Uom
from app.modules.catalog.units import QtyPart, UnitFactor, split_qty, to_base
from app.shared.audit import AuditLog


def uom_id(db: Session, code: str) -> int:
    return db.scalars(select(Uom.id).where(Uom.code == code)).one()


@pytest.fixture
def product_payload(db: Session) -> dict:
    return {
        "sku": "frt-250ml",
        "name_en": "Fruit Drink 250ml",
        "name_bn": "ফ্রুট ড্রিংক ২৫০ মিলি",
        "base_uom_id": uom_id(db, "PCS"),
        "dp": "21.50",
        "tp": "23.00",
        "mrp": "25.00",
        "reorder_level": "48",
        "uoms": [
            {"uom_id": uom_id(db, "CTN"), "factor_to_base": "24", "is_purchase_default": True},
            {"uom_id": uom_id(db, "BOX"), "factor_to_base": "12", "is_sales_default": True},
        ],
    }


# --- Unit conversion ----------------------------------------------------------
UNITS = [UnitFactor("CTN", Decimal(24)), UnitFactor("BOX", Decimal(12))]


def test_to_base() -> None:
    assert to_base([QtyPart("CTN", Decimal(2)), QtyPart("PCS", Decimal(5))], UNITS, "PCS") == 53


def test_split_qty() -> None:
    assert split_qty(Decimal(53), UNITS, "PCS") == [
        QtyPart("CTN", Decimal(2)),
        QtyPart("PCS", Decimal(5)),
    ]
    assert split_qty(Decimal(36), UNITS, "PCS") == [
        QtyPart("CTN", Decimal(1)),
        QtyPart("BOX", Decimal(1)),
    ]
    assert split_qty(Decimal(0), UNITS, "PCS") == [QtyPart("PCS", Decimal(0))]
    assert split_qty(Decimal(-30), UNITS, "PCS") == [
        QtyPart("CTN", Decimal(-1)),
        QtyPart("PCS", Decimal(-6)),
    ]


def test_to_base_rejects_unknown_unit() -> None:
    with pytest.raises(BusinessRuleError):
        to_base([QtyPart("DZN", Decimal(1))], UNITS, "PCS")


# --- Products API ---------------------------------------------------------------
def test_create_product_with_units_and_bangla_name(
    client: TestClient, owner, product_payload, db: Session
) -> None:
    response = client.post("/api/v1/products", headers=owner, json=product_payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["sku"] == "FRT-250ML"
    assert body["name_bn"] == "ফ্রুট ড্রিংক ২৫০ মিলি"
    assert body["base_uom_code"] == "PCS"
    # Money and quantities travel as strings.
    assert body["dp"] == "21.50"
    assert body["tp"] == "23.00"
    assert body["reorder_level"] == "48.000"
    assert [(u["uom_code"], u["factor_to_base"]) for u in body["uoms"]] == [
        ("CTN", "24.000"),
        ("BOX", "12.000"),
    ]
    audit = db.scalars(
        select(AuditLog).where(AuditLog.entity_type == "product", AuditLog.action == "create")
    ).one()
    assert audit.entity_id == str(body["id"])
    assert audit.after is not None and audit.after["sku"] == "FRT-250ML"


def test_duplicate_sku_conflict(client: TestClient, owner, product_payload) -> None:
    assert client.post("/api/v1/products", headers=owner, json=product_payload).status_code == 201
    response = client.post("/api/v1/products", headers=owner, json=product_payload)
    assert response.status_code == 409
    assert response.json()["errors"] == [{"field": "sku", "message": "Already exists"}]


def test_tp_above_mrp_rejected(client: TestClient, owner, product_payload) -> None:
    product_payload["tp"] = "26.00"
    response = client.post("/api/v1/products", headers=owner, json=product_payload)
    assert response.status_code == 422


def test_base_unit_cannot_be_alternative_unit(
    client: TestClient, owner, product_payload, db: Session
) -> None:
    product_payload["uoms"] = [{"uom_id": uom_id(db, "PCS"), "factor_to_base": "24"}]
    response = client.post("/api/v1/products", headers=owner, json=product_payload)
    assert response.status_code == 422


def test_factor_must_exceed_one(client: TestClient, owner, product_payload) -> None:
    product_payload["uoms"][0]["factor_to_base"] = "1"
    assert client.post("/api/v1/products", headers=owner, json=product_payload).status_code == 422


def test_money_precision_enforced(client: TestClient, owner, product_payload) -> None:
    product_payload["dp"] = "21.555"
    assert client.post("/api/v1/products", headers=owner, json=product_payload).status_code == 422


def test_update_product_replaces_units_and_audits_diff(
    client: TestClient, owner, product_payload, db: Session
) -> None:
    created = client.post("/api/v1/products", headers=owner, json=product_payload).json()
    response = client.patch(
        f"/api/v1/products/{created['id']}",
        headers=owner,
        json={
            "tp": "22.50",
            "uoms": [{"uom_id": uom_id(db, "CTN"), "factor_to_base": "20"}],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tp"] == "22.50"
    assert [(u["uom_code"], u["factor_to_base"]) for u in body["uoms"]] == [("CTN", "20.000")]

    update = db.scalars(
        select(AuditLog).where(AuditLog.entity_type == "product", AuditLog.action == "update")
    ).one()
    assert update.before is not None and update.after is not None
    assert update.before["tp"] == "23.00"
    assert update.after["tp"] == "22.50"
    assert "name_en" not in update.after  # only changed fields are stored


def test_search_products_by_bangla_name_and_barcode(
    client: TestClient, owner, product_payload
) -> None:
    product_payload["barcode"] = "8901234567890"
    client.post("/api/v1/products", headers=owner, json=product_payload)
    by_bn = client.get("/api/v1/products", headers=owner, params={"q": "ড্রিংক"}).json()
    assert by_bn["total"] == 1
    by_barcode = client.get("/api/v1/products", headers=owner, params={"q": "8901234567890"}).json()
    assert by_barcode["items"][0]["sku"] == "FRT-250ML"


def test_categories_and_brands(client: TestClient, owner) -> None:
    cat = client.post(
        "/api/v1/categories", headers=owner, json={"name_en": "Beverages", "name_bn": "পানীয়"}
    )
    assert cat.status_code == 201
    dup = client.post("/api/v1/categories", headers=owner, json={"name_en": "beverages"})
    assert dup.status_code == 409
    sub = client.post(
        "/api/v1/categories",
        headers=owner,
        json={"name_en": "Juice", "parent_id": cat.json()["id"]},
    )
    assert sub.status_code == 201
    brand = client.post("/api/v1/brands", headers=owner, json={"name_en": "Pran"})
    assert brand.status_code == 201
    deactivated = client.patch(
        f"/api/v1/brands/{brand.json()['id']}", headers=owner, json={"is_active": False}
    )
    assert deactivated.json()["is_active"] is False
    active = client.get("/api/v1/brands", headers=owner, params={"is_active": True}).json()
    assert all(b["name_en"] != "Pran" for b in active)


def test_seeded_uoms(client: TestClient, owner) -> None:
    codes = {u["code"] for u in client.get("/api/v1/uoms", headers=owner).json()}
    assert {"PCS", "CTN", "BOX", "KG"} <= codes
