"""One person, two dealerships, two tabs: each tab stays where it is (ADR-004)."""

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.tenant_context import clear_context, set_context
from app.modules.tenancy import service as tenancy
from app.modules.users.models import User
from app.modules.users.service import _resolve_roles
from app.seed import provision_tenant
from tests.conftest import DEFAULT_PASSWORD, TEST_TENANT

REFRESH = "/api/v1/auth/refresh"


def add_second_dealership(db: Session, user: User, role: str = "cashier") -> int:
    """Make `user` also a member of a new dealership; the test context is restored."""
    db.flush()
    other = provision_tenant(db, slug="second-dealer", legal_name="Second Dealer").id
    tenancy.add_member(db, user.id, managed=False)
    db.expire(user, ["roles"])  # as a new request would load them
    user.roles = _resolve_roles(db, [role])
    db.flush()
    db.expire(user)
    clear_context(db)
    set_context(db, tenant_id=TEST_TENANT)
    db.commit()
    return other


def _bearer(response: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_each_tab_keeps_its_own_dealership(
    client: TestClient, db: Session, make_user: Callable[..., User]
) -> None:
    other = add_second_dealership(db, make_user("shared1", ["owner"]))
    login = client.post(
        "/api/v1/auth/login", json={"username": "shared1", "password": DEFAULT_PASSWORD}
    )
    assert [t["id"] for t in login.json()["user"]["tenants"]] == [TEST_TENANT, other]
    assert login.json()["user"]["tenant"]["id"] == TEST_TENANT

    first_tab = client.post(REFRESH, json={"tenant_id": TEST_TENANT})
    second_tab = client.post(REFRESH, json={"tenant_id": other})

    assert second_tab.json()["user"]["tenant"]["id"] == other
    assert second_tab.json()["user"]["roles"] == ["cashier"]
    assert first_tab.json()["user"]["roles"] == ["owner"]
    # The same request through each tab lands in that tab's books.
    made = client.post("/api/v1/customers", headers=_bearer(second_tab), json={"name": "In second"})
    assert made.status_code == 201, made.text
    names = {
        c["name"]
        for c in client.get("/api/v1/customers", headers=_bearer(first_tab)).json()["items"]
    }
    assert "In second" not in names
    # A role held in one dealership grants nothing in the other.
    assert client.get("/api/v1/users", headers=_bearer(second_tab)).status_code == 403
    assert client.get("/api/v1/users", headers=_bearer(first_tab)).status_code == 200


def test_a_tab_cannot_refresh_into_a_dealership_one_does_not_belong_to(
    client: TestClient, db: Session, make_user: Callable[..., User]
) -> None:
    make_user("single1", ["owner"])
    db.commit()
    client.post("/api/v1/auth/login", json={"username": "single1", "password": DEFAULT_PASSWORD})

    refused = client.post(REFRESH, json={"tenant_id": 999})

    assert (refused.status_code, refused.json()["code"]) == (403, "membership_inactive")
    assert client.post(REFRESH).status_code == 200  # the session itself is intact
