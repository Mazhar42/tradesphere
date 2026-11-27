"""Working beside another system: ownership, API keys, scheduled fetching, snapshots, exports.
The last test rehearses scenario C of data-import-and-coexistence.md."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import BusinessRuleError
from app.modules.accounting.models import JournalEntry
from app.modules.billing import entitlements
from app.modules.imports import service as imports
from app.modules.imports.parsing import to_csv
from app.modules.integrations import fetch, keys, service
from app.modules.integrations.models import ApiKey, IntegrationConnection, SyncRun
from app.modules.inventory.models import InventoryMovement
from app.modules.jobs.models import Job
from tests.conftest import login
from tests.helpers import key

API = "/api/v1"
PRODUCTS = "sku,name_en,unit,tp\nE-1,Mango Bar,PCS,10\nE-2,Orange Bar,PCS,12\n"
STOCK = "sku,quantity,as_of\nE-1,40,2026-11-27T18:00:00+06:00\nZ-9,5,2026-11-27T18:00:00+06:00\n"


@pytest.fixture()
def auth(client: TestClient, owner: dict[str, str]) -> dict[str, str]:
    return owner


def _post(client: TestClient, auth: dict[str, str], path: str, body: Any, code: int = 201) -> Any:
    response = client.post(f"{API}{path}", headers={**auth, **key()}, json=body)
    assert response.status_code == code, response.text
    return response.json()


def _set(client: TestClient, auth: dict[str, str], domain: str, owner: str) -> Any:
    return client.put(
        f"{API}/integrations/domains", headers=auth, json={"domain": domain, "owner": owner}
    )


def _api_key(client: TestClient, auth: dict[str, str], *scopes: str) -> dict[str, str]:
    made = _post(
        client,
        auth,
        "/integrations/api-keys",
        {"name": "ERP", "source_system": "erp", "scopes": list(scopes)},
    )
    return {"Authorization": f"Bearer {made['key']}"}


def _push(client: TestClient, headers: dict[str, str], data: str, content: str) -> Any:
    return client.post(
        f"{API}/ingest/files",
        headers=headers,
        data={"data": data},
        files={"file": ("f.csv", content.encode(), "text/csv")},
    )


# --- Ownership -------------------------------------------------------------------------------


def test_a_domain_run_elsewhere_takes_no_new_documents(
    client: TestClient, auth: dict[str, str]
) -> None:
    refused = _set(client, auth, "stock", "external")
    assert refused.status_code == 409 and refused.json()["code"] == "domain_dependency"
    for domain in ("sales", "purchasing", "stock"):
        assert _set(client, auth, domain, "external").status_code == 200, domain
    response = client.post(f"{API}/sales-invoices", headers={**auth, **key()}, json={})
    assert response.status_code == 409 and response.json()["code"] == "domain_external"
    assert client.get(f"{API}/sales-invoices", headers=auth).status_code == 200
    # Undoing stays possible.
    undo = client.post(f"{API}/sales-invoices/999/reverse", headers={**auth, **key()}, json={})
    assert undo.json()["code"] != "domain_external"
    # Taking over needs signed opening figures.
    back = _set(client, auth, "stock", "tradesphere")
    assert back.status_code == 409 and back.json()["code"] == "cutover_not_signed"
    assert _set(client, auth, "sales", "tradesphere").status_code == 409  # stock first


def test_only_the_owner_connects_systems_and_sets_ownership(
    client: TestClient, make_user: Callable[..., Any]
) -> None:
    make_user("manager1", ["manager"])
    manager = login(client, "manager1")
    assert client.get(f"{API}/integrations/connections", headers=manager).status_code == 403
    assert client.get(f"{API}/integrations/api-keys", headers=manager).status_code == 403
    assert _set(client, manager, "catalog", "external").status_code == 403


# --- API keys ----------------------------------------------------------------------------------


def test_a_pushed_file_waits_for_approval(
    client: TestClient, auth: dict[str, str], db: Session
) -> None:
    pushed_by = _api_key(client, auth, "files:write")
    first = _push(client, pushed_by, "products", PRODUCTS)
    assert first.status_code == 201, first.text
    assert first.json()["result"]["create"] == 2
    assert client.get(f"{API}/products", headers=auth).json()["total"] == 0
    second = _push(client, pushed_by, "products", PRODUCTS)
    old = client.get(f"{API}/imports/{first.json()['result']['import_job_id']}", headers=auth)
    assert old.json()["status"] == "cancelled"
    assert second.json()["result"]["status"] == "dry_run_ready"

    assert _push(client, pushed_by, "opening_cash", "x\n1\n").status_code == 422
    assert _push(client, pushed_by, "stock_snapshot", STOCK).status_code == 403  # scope
    assert client.get(f"{API}/products", headers=pushed_by).status_code == 401


def test_keys_are_stored_hashed_revocable_and_rate_limited(
    client: TestClient, auth: dict[str, str], db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    pushed_by = _api_key(client, auth, "files:write")
    token = pushed_by["Authorization"].split()[1]
    stored = db.scalar(select(ApiKey))
    assert stored is not None and token not in stored.key_hash
    assert token not in str(client.get(f"{API}/integrations/api-keys", headers=auth).json())
    # Naming another dealership in the key finds nothing.
    parts = token.split("_", 2)
    forged = {"Authorization": f"Bearer tsk_{int(parts[1]) + 1}_{parts[2]}"}
    assert _push(client, forged, "products", PRODUCTS).status_code in (401, 403)

    monkeypatch.setattr(keys, "PER_MINUTE", 2)
    codes = [_push(client, pushed_by, "products", PRODUCTS).status_code for _ in range(3)]
    assert codes == [201, 201, 429]

    assert (
        client.delete(f"{API}/integrations/api-keys/{stored.id}", headers=auth).status_code == 204
    )
    refused = _push(client, pushed_by, "products", PRODUCTS)
    assert refused.json()["code"] == "invalid_api_key"


def test_a_lapsed_subscription_refuses_pushes(
    client: TestClient, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    pushed_by = _api_key(client, auth, "files:write")

    def lapsed(_: Session) -> None:
        raise entitlements.SubscriptionRequiredError("lapsed")

    monkeypatch.setattr(entitlements, "ensure_writable", lapsed)
    assert _push(client, pushed_by, "products", PRODUCTS).status_code == 402
    assert settings.billing_enforcement is False


# --- Fetching ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/a.csv",
        "https://user:pw@example.com/a.csv",
        "https://example.com:8443/a.csv",
        "file:///etc/passwd",
        "https:///a.csv",
    ],
)
def test_unacceptable_addresses(url: str) -> None:
    with pytest.raises(BusinessRuleError):
        fetch.check_url(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",  # noqa: S104
        "::1",
        "fd00::1",
        "fe80::1",
        "::ffff:10.0.0.1",
        "64:ff9b::a00:1",
        "2002:0a00:0001::1",
    ],
)
def test_private_addresses_are_not_public(address: str) -> None:
    assert not fetch.is_public(address)


def test_a_name_that_resolves_inward_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def inward(*_: Any, **__: Any) -> list[Any]:
        return [(0, 0, 0, "", ("93.184.216.34", 443)), (0, 0, 0, "", ("10.0.0.1", 443))]

    monkeypatch.setattr(fetch.socket, "getaddrinfo", inward)
    with pytest.raises(BusinessRuleError, match="public internet"):
        fetch.fetch("https://files.example.com/a.csv")


class _Answer:
    def __init__(self, status: int, body: bytes = b"", location: str = "") -> None:
        self.status = status
        self.headers = {"Location": location}
        self._chunks = [body]

    def read(self, _: int) -> bytes:
        return self._chunks.pop() if self._chunks else b""


def _server(monkeypatch: pytest.MonkeyPatch, answers: list[_Answer]) -> list[tuple[str, Any]]:
    seen: list[tuple[str, Any]] = []

    class Fake:
        def __init__(self, host: str, address: str) -> None:
            self.host = host

        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            seen.append((self.host, headers.get("Authorization")))

        def getresponse(self) -> _Answer:
            return answers.pop(0)

        def close(self) -> None:
            pass

    monkeypatch.setattr(fetch, "_Pinned", Fake)
    monkeypatch.setattr(fetch, "resolve", lambda host: "93.184.216.34")
    return seen


def test_redirects_are_checked_and_do_not_carry_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _server(
        monkeypatch,
        [_Answer(302, location="https://cdn.example.net/a.csv"), _Answer(200, b"ok")],
    )
    assert fetch.fetch("https://a.example.com/x", {"Authorization": "secret"}) == b"ok"
    assert seen == [("a.example.com", "secret"), ("cdn.example.net", None)]

    _server(monkeypatch, [_Answer(302, location="http://169.254.169.254/")])
    with pytest.raises(BusinessRuleError):
        fetch.fetch("https://a.example.com/x")
    _server(monkeypatch, [_Answer(200, b"x" * (fetch.MAX_BYTES + 1))])
    with pytest.raises(BusinessRuleError, match="10 MB"):
        fetch.fetch("https://a.example.com/x")


# --- Scheduled sync ------------------------------------------------------------------------------


def _connect(client: TestClient, auth: dict[str, str], **extra: Any) -> dict[str, Any]:
    body = {
        "name": "Old ERP",
        "source_system": "erp",
        "feeds": [
            {"data": "products", "url": "https://erp.example.com/products.csv"},
            {"data": "stock_snapshot", "url": "https://erp.example.com/stock.csv"},
        ],
        "every_minutes": 60,
        "header_value": "Bearer very-secret",
        **extra,
    }
    return dict(_post(client, auth, "/integrations/connections", body))


def test_the_password_is_sealed_and_dropped_if_the_address_moves(
    client: TestClient, auth: dict[str, str], db: Session
) -> None:
    made = _connect(client, auth)
    assert made["has_credentials"] and "very-secret" not in str(made)
    assert "very-secret" not in str(db.scalar(select(IntegrationConnection.secret)))
    moved = client.put(
        f"{API}/integrations/connections/{made['id']}",
        headers=auth,
        json={
            "name": "Old ERP",
            "source_system": "erp",
            "feeds": [{"data": "products", "url": "https://elsewhere.example.org/p.csv"}],
        },
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["has_credentials"] is False


def test_failures_are_counted_and_the_schedule_goes_on(
    client: TestClient, auth: dict[str, str], db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = _connect(client, auth)

    def down(*_: Any) -> bytes:
        raise BusinessRuleError("The server answered 500.", code="fetch_failed")

    monkeypatch.setattr(fetch, "fetch", down)
    due = db.scalar(select(IntegrationConnection.next_run_at))
    assert due is not None
    for _ in range(3):
        service.sync(db, made["id"], None)
    service.sync(db, made["id"], (due - timedelta(minutes=1)).isoformat())  # stale: ignored
    db.commit()
    connection = db.get(IntegrationConnection, made["id"], populate_existing=True)
    assert connection is not None and connection.failures == 3
    assert connection.last_success_at is None
    assert db.scalar(select(func.count(SyncRun.id))) == 3
    queued = db.scalar(select(func.count(Job.id)).where(Job.kind == "integration.sync"))
    assert queued == 1


# --- Scenario C ----------------------------------------------------------------------------------


def test_scenario_c_a_system_with_an_api(
    client: TestClient, auth: dict[str, str], db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {"products.csv": PRODUCTS, "stock.csv": STOCK}
    sent: list[Any] = []

    def serve(url: str, headers: dict[str, str] | None = None) -> bytes:
        sent.append(headers)
        return files[url.rsplit("/", 1)[1]].encode()

    monkeypatch.setattr(fetch, "fetch", serve)
    for domain in ("sales", "purchasing", "stock"):
        assert _set(client, auth, domain, "external").status_code == 200
    made = _connect(client, auth)

    # Initial pull: staged, approved once.
    service.sync(db, made["id"], None)
    db.commit()
    assert sent[0] == {"Authorization": "Bearer very-secret"}
    run = client.get(f"{API}/integrations/connections/{made['id']}/runs", headers=auth).json()[0]
    assert run["status"] == "ok", run["results"]
    job_id = run["results"][0]["import_job_id"]
    assert run["results"][1] == {"data": "stock_snapshot", "rows": 2, "unmatched": 2}
    job = client.get(f"{API}/imports/{job_id}", headers=auth).json()
    approved = client.post(
        f"{API}/imports/{job_id}/approve",
        headers=auth,
        json={"dry_run_digest": job["dry_run_digest"]},
    )
    assert approved.status_code == 200, approved.text
    imports.run(db, job_id)
    db.commit()
    assert client.get(f"{API}/products", headers=auth).json()["total"] == 2

    # Unchanged files do nothing; a changed stock file is compared, never posted.
    files["stock.csv"] = "sku,quantity,as_of\nE-1,35,2026-11-28T18:00:00+06:00\n"
    service.sync(db, made["id"], None)
    db.commit()
    latest = client.get(f"{API}/integrations/connections/{made['id']}/runs", headers=auth).json()[0]
    assert latest["results"][0] == {"data": "products", "unchanged": True}
    shown = client.get(f"{API}/integrations/snapshots/stock", headers=auth).json()["items"]
    mine = next(r for r in shown if r["code"] == "E-1")
    assert (float(mine["value"]), float(mine["ours"]), mine["matched"]) == (35, 0, True)

    # An older figure does not overwrite a newer one.
    service.save_snapshot(
        db, "stock", "erp", [{"sku": "E-1", "quantity": "99", "as_of": "2026-11-01T00:00:00Z"}]
    )
    db.commit()
    value = db.scalar(text("SELECT value FROM external_snapshots WHERE code = 'E-1'"))
    assert value == 35

    # Nothing was posted.
    assert db.scalar(select(func.count(JournalEntry.id))) == 0
    assert db.scalar(select(func.count(InventoryMovement.id))) == 0


def test_exports_are_safe_to_open_in_a_spreadsheet(
    client: TestClient, auth: dict[str, str]
) -> None:
    assert b"'=HYPERLINK" in to_csv(["a"], [["=HYPERLINK(1)"]])
    journal = client.get(
        f"{API}/exports/journal.csv",
        headers=auth,
        params={"date_from": "2026-01-01", "date_to": "2026-12-31"},
    )
    assert journal.status_code == 200
    assert journal.content.decode("utf-8-sig").startswith("date,entry_no")
    too_long = client.get(
        f"{API}/exports/journal.csv",
        headers=auth,
        params={"date_from": "2020-01-01", "date_to": "2026-12-31"},
    )
    assert too_long.status_code == 422
    assert client.get(f"{API}/exports/stock.csv", headers=auth).status_code == 200
