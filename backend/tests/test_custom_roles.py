"""Roles a dealership defines from the fixed permission catalogue."""

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from app.modules.users.models import User
from tests.conftest import login


def _role(client: TestClient, headers: dict[str, str], code: str, permissions: list[str]) -> Any:
    return client.post(
        "/api/v1/roles",
        headers=headers,
        json={"code": code, "name": code.title(), "permissions": permissions},
    )


def _id_of(client: TestClient, headers: dict[str, str], code: str) -> int:
    roles = client.get("/api/v1/roles", headers=headers).json()
    return int(next(r["id"] for r in roles if r["code"] == code))


def test_a_custom_role_grants_exactly_its_permissions_and_changes_apply_at_once(
    client: TestClient, owner: dict[str, str], make_user: Callable[..., User]
) -> None:
    assert _role(client, owner, "viewer", ["catalog:read"]).status_code == 201
    make_user("viewer1", ["viewer"])
    viewer = login(client, "viewer1")
    assert client.get("/api/v1/products", headers=viewer).status_code == 200
    assert client.get("/api/v1/customers", headers=viewer).status_code == 403

    path = f"/api/v1/roles/{_id_of(client, owner, 'viewer')}"
    changed = client.patch(path, headers=owner, json={"permissions": ["customers:read"]})

    assert changed.status_code == 200, changed.text
    assert client.get("/api/v1/products", headers=viewer).status_code == 403
    assert client.get("/api/v1/customers", headers=viewer).status_code == 200
    assert client.delete(path, headers=owner).json()["code"] == "role_in_use"


def test_built_in_roles_cannot_be_changed(client: TestClient, owner: dict[str, str]) -> None:
    path = f"/api/v1/roles/{_id_of(client, owner, 'cashier')}"
    assert client.patch(path, headers=owner, json={"name": "x"}).json()["code"] == "system_role"
    assert client.delete(path, headers=owner).json()["code"] == "system_role"


def test_a_role_manager_cannot_create_a_role_stronger_than_themself(
    client: TestClient, owner: dict[str, str], make_user: Callable[..., User]
) -> None:
    _role(client, owner, "hr", ["users:read", "users:write"])
    make_user("hr1", ["hr"])
    hr = login(client, "hr1")

    refused = _role(client, hr, "sneaky", ["users:read", "finance:write"])

    assert refused.json()["code"] == "cannot_grant"
    assert _role(client, hr, "reader", ["users:read"]).status_code == 201
