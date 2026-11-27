"""Sequences, dates, settings, seed and audit invariants."""

import threading
import uuid
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.accounting.models import Account
from app.modules.users.models import Permission, Role
from app.seed import ACCOUNTS, run_seed
from app.shared.audit import AuditLog
from app.shared.dates import business_date, fiscal_year_code
from app.shared.sequences import DocumentSequence, next_doc_number


# --- Dates & fiscal year ----------------------------------------------------------
@pytest.mark.parametrize(
    ("d", "code"),
    [
        (date(2025, 7, 1), "2526"),
        (date(2026, 6, 30), "2526"),
        (date(2026, 7, 1), "2627"),
        (date(2026, 1, 15), "2526"),
    ],
)
def test_fiscal_year_code(d: date, code: str) -> None:
    assert fiscal_year_code(d) == code


def test_business_date_uses_dhaka_time() -> None:
    # 20:00 UTC on 31 Dec is already 1 Jan in Dhaka (UTC+6).
    assert business_date(datetime(2025, 12, 31, 20, 0, tzinfo=UTC)) == date(2026, 1, 1)


# --- Document numbers ----------------------------------------------------------------
def test_doc_number_format_and_increment(db: Session) -> None:
    doc_type = f"T{uuid.uuid4().hex[:6].upper()}"
    assert next_doc_number(db, doc_type, date(2025, 8, 1)) == f"{doc_type}-2526-000001"
    assert next_doc_number(db, doc_type, date(2025, 8, 2)) == f"{doc_type}-2526-000002"
    # New fiscal year restarts the counter.
    assert next_doc_number(db, doc_type, date(2026, 7, 1)) == f"{doc_type}-2627-000001"


def test_doc_numbers_are_gapless_under_concurrency(engine: Engine) -> None:
    """Concurrent transactions, some rolling back: committed numbers must be 1..N with no gaps."""
    doc_type = f"C{uuid.uuid4().hex[:6].upper()}"
    committed: list[int] = []
    lock = threading.Lock()
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            with Session(engine) as session:
                number = next_doc_number(session, doc_type, date(2025, 8, 1))
                if i % 3 == 0:
                    session.rollback()  # e.g. a failed posting
                    return
                session.commit()
                with lock:
                    committed.append(int(number.rsplit("-", 1)[1]))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert not errors
        assert sorted(committed) == list(range(1, len(committed) + 1))
        assert len(committed) == 20
    finally:
        with Session(engine) as session:
            session.execute(delete(DocumentSequence).where(DocumentSequence.doc_type == doc_type))
            session.commit()


# --- Seed --------------------------------------------------------------------------
def test_seed_is_idempotent(db: Session) -> None:
    def counts() -> tuple[int, int, int]:
        return (
            db.scalar(select(func.count()).select_from(Permission)) or 0,
            db.scalar(select(func.count()).select_from(Role)) or 0,
            db.scalar(select(func.count()).select_from(Account)) or 0,
        )

    before = counts()
    run_seed(db)
    db.flush()
    assert counts() == before
    assert before[2] == len(ACCOUNTS)


def test_chart_of_accounts_structure(client: TestClient, owner) -> None:
    accounts = {a["code"]: a for a in client.get("/api/v1/accounts", headers=owner).json()}
    assert accounts["1100"]["party_type"] == "customer"
    assert accounts["2000"]["party_type"] == "supplier"
    assert accounts["1300"]["party_type"] == "supplier"
    assert accounts["6000"]["is_postable"] is False
    assert accounts["6010"]["parent_id"] == accounts["6000"]["id"]


def test_locations_seeded(client: TestClient, owner) -> None:
    locations = client.get("/api/v1/locations", headers=owner).json()
    assert {(loc["code"], loc["is_sellable"]) for loc in locations} == {
        ("SELLABLE", True),
        ("DAMAGED", False),
        ("EXPIRED", False),
    }


# --- Audit -------------------------------------------------------------------------
def test_audit_log_is_append_only(db: Session) -> None:
    db.add(AuditLog(action="test", entity_type="test"))
    db.flush()
    with pytest.raises(DBAPIError, match=r"append-only|permission denied"), db.begin_nested():
        db.execute(text("UPDATE audit_logs SET action = 'tampered'"))
    with pytest.raises(DBAPIError, match=r"append-only|permission denied"), db.begin_nested():
        db.execute(text("DELETE FROM audit_logs"))


def test_audit_log_endpoint(client: TestClient, owner, cashier) -> None:
    client.post("/api/v1/brands", headers=owner, json={"name_en": "Audited Brand"})
    response = client.get(
        "/api/v1/audit-logs", headers=owner, params={"entity_type": "brand"}
    ).json()
    assert response["total"] == 1
    entry = response["items"][0]
    assert entry["action"] == "create"
    assert entry["actor_username"] == "owner1"
    assert entry["request_id"]
    assert client.get("/api/v1/audit-logs", headers=cashier).status_code == 403


# --- Settings -------------------------------------------------------------------------
def test_settings_read_and_update(client: TestClient, owner, cashier) -> None:
    current = client.get("/api/v1/settings", headers=cashier)
    assert current.status_code == 200
    assert current.json()["period_lock"]["lock_date"] is None

    profile = {"name": "Rahman Distribution", "name_bn": "রহমান ডিস্ট্রিবিউশন", "phone": "01700000000"}
    updated = client.put("/api/v1/settings/business-profile", headers=owner, json=profile)
    assert updated.status_code == 200
    assert updated.json()["business_profile"]["name_bn"] == "রহমান ডিস্ট্রিবিউশন"

    forbidden = client.put("/api/v1/settings/business-profile", headers=cashier, json=profile)
    assert forbidden.status_code == 403


def test_period_lock_cannot_be_future(client: TestClient, owner) -> None:
    response = client.put(
        "/api/v1/settings/period-lock", headers=owner, json={"lock_date": "2999-01-01"}
    )
    assert response.status_code == 422
    ok = client.put("/api/v1/settings/period-lock", headers=owner, json={"lock_date": "2025-06-30"})
    assert ok.status_code == 200
    assert ok.json()["period_lock"]["lock_date"] == "2025-06-30"
