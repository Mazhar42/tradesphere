"""Operators run the service without reading dealerships' records, except under a grant.

Uses a throwaway database, because platform routes connect as a different role.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core import mfa_codes
from app.core.db import get_db
from app.main import app
from app.modules.platform.router import get_platform_db
from app.modules.platform.service import add_operator
from app.modules.users.schemas import UserCreate
from app.modules.users.service import create_user
from app.shared.dates import now_utc
from tests.conftest import DEFAULT_PASSWORD, TEST_TENANT, Databases, login


class World:
    def __init__(self, client: TestClient, databases: Databases, secret: str) -> None:
        self.client = client
        self.databases = databases
        self.secret = secret
        self._step = 0

    def operator_token(self) -> dict[str, str]:
        # Each code works once: step forward through the accepted window.
        code = mfa_codes.code_at(self.secret, mfa_codes.step_of(now_utc()) + self._step - 1)
        self._step += 1
        response = self.client.post(
            "/api/v1/platform/login",
            json={"username": "op1", "password": DEFAULT_PASSWORD, "code": code},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def world(scratch: Databases) -> Iterator[World]:
    def session_for(engine):  # type: ignore[no-untyped-def]
        def provide() -> Iterator[Session]:
            with Session(engine, autoflush=False, expire_on_commit=False) as session:
                yield session

        return provide

    with Session(scratch.app, info={"tenant_id": TEST_TENANT}, autoflush=False) as session:
        for username, roles in (("owner1", ["owner"]), ("op1", ["cashier"])):
            create_user(
                session,
                UserCreate(
                    username=username,
                    full_name=username,
                    password=DEFAULT_PASSWORD,
                    roles=roles,
                ),
                actor=None,
            )
        session.commit()
    with Session(scratch.platform, autoflush=False) as session:
        uri, codes = add_operator(session, "op1")
        session.commit()
    assert uri and len(codes) == 10
    # The operator account holds no membership: operators are not dealership staff.
    with scratch.admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE tenant_memberships SET status = 'inactive' WHERE user_id = "
                "(SELECT id FROM users WHERE username = 'op1')"
            )
        )
    app.dependency_overrides[get_db] = session_for(scratch.app)
    app.dependency_overrides[get_platform_db] = session_for(scratch.platform)
    with TestClient(app) as client:
        yield World(client, scratch, uri.split("secret=")[1].split("&")[0])
    app.dependency_overrides.clear()


def test_an_operator_sees_dealerships_but_none_of_their_records(world: World) -> None:
    operator = world.operator_token()

    rows = world.client.get("/api/v1/platform/tenants", headers=operator).json()

    assert [(r["slug"], r["members"]) for r in rows] == [("test", 1)]
    assert world.client.get("/api/v1/products", headers=operator).status_code == 401
    for table in ("sales_invoices", "customers", "journal_lines"):
        with world.databases.platform.connect() as conn, pytest.raises(DBAPIError):
            conn.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608


def test_a_dealership_token_opens_no_platform_route(world: World) -> None:
    owner = login(world.client, "owner1")
    assert world.client.get("/api/v1/platform/tenants", headers=owner).status_code == 401


def test_platform_sign_in_needs_the_second_factor(world: World) -> None:
    for body in (
        {"username": "op1", "password": DEFAULT_PASSWORD, "code": "000000"},
        {"username": "owner1", "password": DEFAULT_PASSWORD, "code": "000000"},
    ):
        response = world.client.post("/api/v1/platform/login", json=body)
        assert response.status_code == 401, response.text


def test_suspending_closes_the_dealership_and_is_audited(world: World) -> None:
    operator = world.operator_token()
    owner = login(world.client, "owner1")
    path = f"/api/v1/platform/tenants/{TEST_TENANT}"
    body = {"status": "suspended", "reason": "Abuse report 42"}

    assert world.client.patch(path, headers=operator, json=body).status_code == 204

    refused = world.client.get("/api/v1/products", headers=owner)
    assert refused.json()["code"] == "tenant_unavailable"
    log = world.client.get("/api/v1/platform/audit", headers=operator).json()
    assert log[0]["action"] == "tenant_status" and log[0]["reason"] == "Abuse report 42"


def test_support_access_needs_the_owners_grant_reads_only_and_is_logged(world: World) -> None:
    operator = world.operator_token()
    owner = login(world.client, "owner1")
    assert world.client.get("/api/v1/platform/support-grants", headers=operator).json() == []

    grant = world.client.post(
        "/api/v1/support-grants",
        headers=owner,
        json={"operator_username": "op1", "reason": "Totals look wrong", "hours": 1},
    )
    assert grant.status_code == 201, grant.text
    grant_id = grant.json()["id"]
    token = world.client.post(
        f"/api/v1/platform/support-grants/{grant_id}/token", headers=operator
    ).json()["access_token"]
    support = {"Authorization": f"Bearer {token}"}

    assert world.client.get("/api/v1/customers", headers=support).status_code == 200
    write = world.client.post("/api/v1/customers", headers=support, json={"name": "x"})
    assert write.json()["code"] == "support_read_only"
    audit = world.client.get("/api/v1/audit-logs", headers=owner).json()["items"]
    assert any(
        a["action"] == "support_access" and a["after"]["path"] == "/api/v1/customers" for a in audit
    )

    assert (
        world.client.delete(f"/api/v1/support-grants/{grant_id}", headers=owner).status_code == 204
    )
    ended = world.client.get("/api/v1/customers", headers=support)
    assert ended.json()["code"] == "support_ended"


def test_the_database_itself_refuses_writes_in_a_support_session(world: World) -> None:
    with (
        Session(world.databases.app, info={"tenant_id": TEST_TENANT, "read_only": True}) as session,
        pytest.raises(DBAPIError, match="read-only"),
    ):
        session.execute(text("UPDATE brands SET name_en = name_en"))
