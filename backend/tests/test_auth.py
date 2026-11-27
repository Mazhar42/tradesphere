import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.tenancy.models import AuthEvent
from app.modules.users.models import RefreshToken
from tests.conftest import DEFAULT_PASSWORD, login

REFRESH = "/api/v1/auth/refresh"


def use_cookie(client: TestClient, token: str | None) -> None:
    client.cookies.clear()
    if token:
        client.cookies.set("ts_refresh", token, path="/api/v1/auth")


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


def test_login_returns_token_cookie_and_profile(client: TestClient, make_user) -> None:
    make_user("Rahim", ["cashier"])
    response = client.post(
        "/api/v1/auth/login", json={"username": "RAHIM", "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["username"] == "rahim"
    assert body["user"]["roles"] == ["cashier"]
    assert "sales:write" in body["user"]["permissions"]
    assert "users:write" not in body["user"]["permissions"]
    cookie = response.headers["set-cookie"]
    assert "ts_refresh=" in cookie
    assert "HttpOnly" in cookie
    assert "Path=/api/v1/auth" in cookie
    assert "SameSite=strict" in cookie


def test_wrong_password_is_401_problem_and_audited(
    client: TestClient, make_user, db: Session
) -> None:
    make_user("karim", ["cashier"])
    response = client.post(
        "/api/v1/auth/login", json={"username": "karim", "password": "nope-nope"}
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "invalid_credentials"
    failed = db.scalars(select(AuthEvent).where(AuthEvent.action == "login_failed")).all()
    assert any(a.identifier == "karim" for a in failed)


def test_unknown_user_gets_same_error(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"username": "ghost", "password": "whatever1"}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_inactive_user_cannot_login(client: TestClient, make_user, db: Session) -> None:
    user = make_user("sleepy", ["cashier"])
    user.is_active = False
    db.flush()
    response = client.post(
        "/api/v1/auth/login", json={"username": "sleepy", "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 401


def test_protected_route_requires_token(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"
    bad = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert bad.status_code == 401
    assert bad.json()["code"] == "invalid_token"


def test_me(client: TestClient, owner: dict[str, str]) -> None:
    response = client.get("/api/v1/auth/me", headers=owner)
    assert response.status_code == 200
    assert response.json()["roles"] == ["owner"]


def test_refresh_rotates_token(client: TestClient, make_user, db: Session) -> None:
    make_user("rotator", ["cashier"])
    login(client, "rotator")
    first_cookie = client.cookies.get("ts_refresh")

    response = client.post(REFRESH)
    assert response.status_code == 200
    assert response.json()["access_token"]
    second_cookie = client.cookies.get("ts_refresh")
    assert second_cookie and second_cookie != first_cookie

    tokens = db.scalars(select(RefreshToken).order_by(RefreshToken.id)).all()
    assert len(tokens) == 2
    assert tokens[0].revoked_at is not None
    assert tokens[0].replaced_by_id == tokens[1].id


def test_concurrent_refresh_within_grace_does_not_log_out(client: TestClient, make_user) -> None:
    make_user("twotabs", ["cashier"])
    login(client, "twotabs")
    old_cookie = client.cookies.get("ts_refresh")
    assert client.post(REFRESH).status_code == 200
    # Second tab still sends the old cookie a moment later.
    use_cookie(client, old_cookie)
    response = client.post(REFRESH)
    assert response.status_code == 200
    assert "set-cookie" not in response.headers


def test_refresh_token_reuse_revokes_family(
    client: TestClient, make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "refresh_reuse_grace_seconds", -1)
    make_user("victim", ["cashier"])
    login(client, "victim")
    stolen = client.cookies.get("ts_refresh")
    assert client.post(REFRESH).status_code == 200
    current = client.cookies.get("ts_refresh")

    use_cookie(client, stolen)
    assert client.post(REFRESH).status_code == 401
    # The legitimate (newer) token is revoked too.
    use_cookie(client, current)
    assert client.post(REFRESH).status_code == 401


def test_logout_revokes_refresh_token(client: TestClient, make_user) -> None:
    make_user("leaver", ["cashier"])
    login(client, "leaver")
    token = client.cookies.get("ts_refresh")
    assert client.post("/api/v1/auth/logout").status_code == 204
    use_cookie(client, token)
    assert client.post(REFRESH).status_code == 401


def test_refresh_without_cookie(client: TestClient) -> None:
    response = client.post(REFRESH)
    assert response.status_code == 401
    assert response.json()["code"] == "no_refresh_token"
