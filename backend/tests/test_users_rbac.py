from fastapi.testclient import TestClient

from app.modules.users.permissions_catalog import PERMISSIONS, ROLES
from tests.conftest import login


def test_roles_are_seeded_with_expected_permissions(client: TestClient, owner) -> None:
    response = client.get("/api/v1/roles", headers=owner)
    assert response.status_code == 200
    roles = {r["code"]: set(r["permissions"]) for r in response.json()}
    assert set(roles) == set(ROLES)
    assert roles["owner"] == set(PERMISSIONS)
    assert "users:write" not in roles["manager"]
    assert "settings:write" not in roles["manager"]


def test_cashier_is_forbidden_from_user_management(client: TestClient, cashier) -> None:
    response = client.get("/api/v1/users", headers=cashier)
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "permission_denied"
    assert "users:read" in body["detail"]


def test_cashier_can_read_but_not_write_products(client: TestClient, cashier) -> None:
    assert client.get("/api/v1/products", headers=cashier).status_code == 200
    response = client.post(
        "/api/v1/products",
        headers=cashier,
        json={"sku": "X1", "name_en": "X", "base_uom_id": 1},
    )
    assert response.status_code == 403


def test_storekeeper_cannot_see_customers(client: TestClient, storekeeper) -> None:
    assert client.get("/api/v1/customers", headers=storekeeper).status_code == 403
    assert client.get("/api/v1/suppliers", headers=storekeeper).status_code == 200


def test_owner_creates_user_who_can_login(client: TestClient, owner) -> None:
    response = client.post(
        "/api/v1/users",
        headers=owner,
        json={
            "username": "NewCashier",
            "full_name": "New Cashier",
            "password": "An0ther-Passw0rd",
            "roles": ["cashier"],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["username"] == "newcashier"
    login(client, "newcashier", "An0ther-Passw0rd")


def test_duplicate_username_is_conflict(client: TestClient, owner, make_user) -> None:
    make_user("taken", ["cashier"])
    response = client.post(
        "/api/v1/users",
        headers=owner,
        json={
            "username": "TAKEN",
            "full_name": "X",
            "password": "Passw0rd-123",
            "roles": ["cashier"],
        },
    )
    assert response.status_code == 409
    assert response.json()["errors"] == [{"field": "username", "message": "Already exists"}]


def test_unknown_role_rejected(client: TestClient, owner) -> None:
    response = client.post(
        "/api/v1/users",
        headers=owner,
        json={"username": "bob", "full_name": "Bob", "password": "Passw0rd-123", "roles": ["god"]},
    )
    assert response.status_code == 422


def test_owner_cannot_deactivate_self_or_remove_last_owner(client: TestClient, owner) -> None:
    me = client.get("/api/v1/auth/me", headers=owner).json()
    response = client.patch(f"/api/v1/users/{me['id']}", headers=owner, json={"is_active": False})
    assert response.status_code == 422
    response = client.patch(f"/api/v1/users/{me['id']}", headers=owner, json={"roles": ["manager"]})
    assert response.status_code == 422
    assert response.json()["code"] == "last_owner"


def test_deactivated_user_loses_access_immediately(client: TestClient, owner, make_user) -> None:
    user = make_user("temp", ["cashier"])
    temp_headers = login(client, "temp")
    assert client.get("/api/v1/products", headers=temp_headers).status_code == 200
    response = client.patch(f"/api/v1/users/{user.id}", headers=owner, json={"is_active": False})
    assert response.status_code == 200
    assert client.get("/api/v1/products", headers=temp_headers).status_code == 401


def test_password_reset(client: TestClient, owner, make_user) -> None:
    user = make_user("forgetful", ["cashier"])
    response = client.post(
        f"/api/v1/users/{user.id}/reset-password",
        headers=owner,
        json={"new_password": "Brand-New-Pass1"},
    )
    assert response.status_code == 204
    login(client, "forgetful", "Brand-New-Pass1")


def test_validation_error_shape(client: TestClient, owner) -> None:
    response = client.post(
        "/api/v1/users",
        headers=owner,
        json={"username": "a", "full_name": "", "password": "short", "roles": []},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    fields = {e["field"] for e in body["errors"]}
    assert {"username", "full_name", "password", "roles"} <= fields
