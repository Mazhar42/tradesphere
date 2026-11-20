from fastapi.testclient import TestClient


def test_customer_code_is_auto_generated(client: TestClient, owner) -> None:
    first = client.post(
        "/api/v1/customers",
        headers=owner,
        json={
            "name": "Abdul Karim",
            "shop_name": "Karim Store",
            "phone": "01711000000",
            "area": "Mirpur",
            "credit_limit": "50000.00",
            "payment_terms_days": 15,
        },
    )
    assert first.status_code == 201, first.text
    second = client.post("/api/v1/customers", headers=owner, json={"name": "Second Shop"})
    first_no = int(first.json()["code"].split("-")[1])
    second_no = int(second.json()["code"].split("-")[1])
    assert first.json()["code"].startswith("CUS-")
    assert second_no == first_no + 1
    assert first.json()["credit_limit"] == "50000.00"


def test_manual_customer_code_and_duplicate(client: TestClient, owner) -> None:
    ok = client.post("/api/v1/customers", headers=owner, json={"code": "r-001", "name": "Retail 1"})
    assert ok.status_code == 201
    assert ok.json()["code"] == "R-001"
    dup = client.post("/api/v1/customers", headers=owner, json={"code": "R-001", "name": "Other"})
    assert dup.status_code == 409


def test_negative_credit_limit_rejected(client: TestClient, owner) -> None:
    response = client.post(
        "/api/v1/customers", headers=owner, json={"name": "Bad", "credit_limit": "-1"}
    )
    assert response.status_code == 422


def test_walk_in_customer_is_seeded_and_protected(client: TestClient, owner) -> None:
    listing = client.get("/api/v1/customers", headers=owner, params={"q": "WALK-IN"}).json()
    walk_in = next(c for c in listing["items"] if c["is_walk_in"])
    response = client.patch(
        f"/api/v1/customers/{walk_in['id']}", headers=owner, json={"is_active": False}
    )
    assert response.status_code == 422


def test_customer_search_and_update(client: TestClient, owner) -> None:
    created = client.post(
        "/api/v1/customers",
        headers=owner,
        json={"name": "Jamal", "shop_name": "Jamal Traders", "phone": "01822222222"},
    ).json()
    found = client.get("/api/v1/customers", headers=owner, params={"q": "traders"}).json()
    assert [c["id"] for c in found["items"]] == [created["id"]]
    updated = client.patch(
        f"/api/v1/customers/{created['id']}", headers=owner, json={"area": "Uttara"}
    )
    assert updated.json()["area"] == "Uttara"


def test_cashier_can_manage_customers(client: TestClient, cashier) -> None:
    response = client.post("/api/v1/customers", headers=cashier, json={"name": "Counter Sale"})
    assert response.status_code == 201


def test_supplier_crud(client: TestClient, owner) -> None:
    created = client.post(
        "/api/v1/suppliers",
        headers=owner,
        json={
            "name": "PRAN-RFL Group",
            "contact_person": "Area Manager",
            "email": "orders@example.com",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["code"].startswith("SUP-")
    bad_email = client.post(
        "/api/v1/suppliers", headers=owner, json={"name": "X", "email": "not-an-email"}
    )
    assert bad_email.status_code == 422
    fetched = client.get(f"/api/v1/suppliers/{created.json()['id']}", headers=owner)
    assert fetched.json()["name"] == "PRAN-RFL Group"
