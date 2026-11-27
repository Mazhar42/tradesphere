"""Invitations, verification, recovery, and who may reset whose password (ADR-004, TS-09)."""

from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.auth import accounts
from app.modules.users.models import User
from app.shared import email
from app.shared.dates import now_utc
from tests.conftest import DEFAULT_PASSWORD, Clock, login
from tests.test_tenant_switching import add_second_dealership

NEW_PASSWORD = "Fresh-Passw0rd-1"


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[email.Message]:
    sent: list[email.Message] = []
    monkeypatch.setattr(settings, "email_backend", "log")
    monkeypatch.setattr(accounts, "queue_email", lambda _, message: sent.append(message))
    return sent


def _token(text: str) -> str:
    return text.split("#token=", 1)[1].split(maxsplit=1)[0]


def _invite(client: TestClient, owner: dict[str, str], **body: object) -> dict[str, object]:
    response = client.post(
        "/api/v1/invitations", headers=owner, json={"roles": ["cashier"], **body}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _accept(client: TestClient, token: str, **body: object) -> int:
    payload = {
        "token": token,
        "username": "newhire",
        "full_name": "New Hire",
        "password": NEW_PASSWORD,
        **body,
    }
    return client.post("/api/v1/auth/invitations/accept", json=payload).status_code


def test_a_shared_link_creates_one_account_once(client: TestClient, owner: dict[str, str]) -> None:
    link = str(_invite(client, owner, note="Counter staff")["link"])
    token = _token(link)
    preview = client.post("/api/v1/auth/invitations/inspect", json={"token": token})
    assert preview.json() == {"dealership": "Test Dealership", "email": None}

    assert _accept(client, token) == 204
    assert _accept(client, token, username="second") == 404  # single use
    me = client.get("/api/v1/auth/me", headers=login(client, "newhire", NEW_PASSWORD)).json()
    assert me["roles"] == ["cashier"]


def test_an_emailed_link_goes_only_to_the_address(
    client: TestClient, owner: dict[str, str], outbox: list[email.Message]
) -> None:
    created = _invite(client, owner, email="hire@example.com")

    assert created["link"] is None
    assert [m.to for m in outbox] == ["hire@example.com"]
    assert _accept(client, _token(outbox[0].body)) == 204


def test_without_email_set_up_an_address_is_refused(
    client: TestClient, owner: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/invitations",
        headers=owner,
        json={"email": "a@b.example", "roles": ["cashier"]},
    )
    assert response.json()["code"] == "email_not_configured"


def test_revoked_and_expired_invitations_do_not_work(
    client: TestClient, owner: dict[str, str], clock: Clock
) -> None:
    revoked = _invite(client, owner)
    path = f"/api/v1/invitations/{revoked['invitation']['id']}"  # type: ignore[index]
    assert client.delete(path, headers=owner).status_code == 204
    assert _accept(client, _token(str(revoked["link"]))) == 404

    old = _token(str(_invite(client, owner)["link"]))
    clock.set(now_utc() + timedelta(hours=73))
    assert _accept(client, old) == 404


def test_no_one_can_grant_what_they_do_not_have(
    client: TestClient, make_user: Callable[..., User], owner: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/roles",
        headers=owner,
        json={"code": "clerk", "name": "Clerk", "permissions": ["users:write", "users:read"]},
    )
    assert response.status_code == 201, response.text
    make_user("clerk1", ["clerk"])
    clerk = login(client, "clerk1")
    refused = client.post("/api/v1/invitations", headers=clerk, json={"roles": ["owner"]})
    assert refused.json()["code"] == "cannot_grant"


def test_an_existing_account_joins_only_with_its_password(
    client: TestClient, db: Session, make_user: Callable[..., User], owner: dict[str, str]
) -> None:
    add_second_dealership(db, make_user("visitor", ["cashier"]))
    # visitor is already a member here; make another who is not
    token = _token(str(_invite(client, owner)["link"]))
    assert _accept(client, token, username="visitor", password="wrong-password") == 401
    assert _accept(client, token, username="visitor", password=DEFAULT_PASSWORD) == 409


def test_password_recovery_is_silent_single_use_and_ends_sessions(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    outbox: list[email.Message],
) -> None:
    user = make_user("forgot1", ["cashier"])
    user.email, user.email_verified_at = "forgot@example.com", now_utc()
    db.commit()
    login(client, "forgot1")
    cookie = client.cookies.get("ts_refresh")

    for identifier in ("nobody-here", "forgot@example.com"):
        response = client.post("/api/v1/auth/password-reset", json={"identifier": identifier})
        assert (response.status_code, response.content) == (202, b"null")
    assert [m.to for m in outbox] == ["forgot@example.com"]

    body = {"token": _token(outbox[0].body), "new_password": NEW_PASSWORD}
    assert client.post("/api/v1/auth/password-reset/confirm", json=body).status_code == 204
    assert client.post("/api/v1/auth/password-reset/confirm", json=body).status_code == 404
    client.cookies.set("ts_refresh", str(cookie), path="/api/v1/auth")
    assert client.post("/api/v1/auth/refresh").status_code == 401
    login(client, "forgot1", NEW_PASSWORD)


def test_an_address_is_used_only_after_it_is_confirmed(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    outbox: list[email.Message],
) -> None:
    user = make_user("mailer1", ["cashier"])
    db.commit()
    headers = login(client, "mailer1")
    client.post(
        "/api/v1/auth/me/email",
        headers=headers,
        json={"email": "Mine@Example.com", "password": DEFAULT_PASSWORD},
    )
    db.refresh(user)
    assert user.email is None

    response = client.post("/api/v1/auth/verify-email", json={"token": _token(outbox[0].body)})

    assert response.status_code == 204, response.text
    db.refresh(user)
    assert (user.email, user.email_verified_at is not None) == ("mine@example.com", True)


def test_a_dealership_cannot_reset_the_password_of_a_shared_account(
    client: TestClient, db: Session, make_user: Callable[..., User], owner: dict[str, str]
) -> None:
    shared = make_user("shared2", ["cashier"])
    own = make_user("local1", ["cashier"])
    add_second_dealership(db, shared)
    body = {"new_password": NEW_PASSWORD}

    refused = client.post(f"/api/v1/users/{shared.id}/reset-password", headers=owner, json=body)
    allowed = client.post(f"/api/v1/users/{own.id}/reset-password", headers=owner, json=body)

    assert (refused.status_code, refused.json()["code"]) == (403, "reset_not_allowed")
    assert allowed.status_code == 204, allowed.text
    login(client, "shared2")  # unchanged


def test_common_and_short_owner_passwords_are_refused(
    client: TestClient, owner: dict[str, str]
) -> None:
    for password, roles in (("password123", ["cashier"]), ("Ab1-defg9", ["owner"])):
        response = client.post(
            "/api/v1/users",
            headers=owner,
            json={"username": "weakone", "full_name": "W", "password": password, "roles": roles},
        )
        assert response.json()["code"] == "weak_password", response.text
