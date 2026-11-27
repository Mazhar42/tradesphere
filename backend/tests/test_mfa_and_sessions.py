"""Two-step sign-in, step-up for sensitive changes, and the session list."""

import time
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import mfa_codes
from app.core.config import settings
from app.modules.users.models import MfaFactor, User
from app.shared.dates import now_utc
from tests.conftest import DEFAULT_PASSWORD, Clock, login

LOGIN = "/api/v1/auth/login"


@pytest.fixture(autouse=True)
def steady_time(clock: Clock) -> None:
    """The middle of a recent 30-second step, so a code cannot expire during a test."""
    step = int(time.time()) // 30 - 1
    clock.set(datetime.fromtimestamp(step * 30 + 15, UTC))


def code_now(secret: str, offset: int = 0) -> str:
    return mfa_codes.code_at(secret, mfa_codes.step_of(now_utc()) + offset)


def enrol(client: TestClient, headers: dict[str, str]) -> tuple[str, list[str]]:
    setup = client.post(
        "/api/v1/auth/mfa/setup", headers=headers, json={"password": DEFAULT_PASSWORD}
    ).json()
    confirmed = client.post(
        "/api/v1/auth/mfa/confirm",
        headers=headers,
        json={"code": code_now(setup["secret"], -1)},  # an earlier step stays free for later
    )
    assert confirmed.status_code == 200, confirmed.text
    return setup["secret"], confirmed.json()["recovery_codes"]


def _sign_in(client: TestClient, username: str, code: str | None = None) -> tuple[int, str]:
    response = client.post(
        LOGIN, json={"username": username, "password": DEFAULT_PASSWORD, "code": code}
    )
    return response.status_code, response.json().get("code", "")


def test_known_code_from_the_standard() -> None:
    # RFC 6238 appendix B, SHA-1, time 59 s (truncated to six digits).
    assert mfa_codes.code_at("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", 59 // 30) == "287082"


def test_enrolled_people_need_a_code_and_each_works_once(
    client: TestClient, db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user("secure1", ["cashier"])
    secret, recovery = enrol(client, login(client, "secure1"))
    stored = db.get(MfaFactor, user.id)
    assert stored is not None and secret not in stored.secret_encrypted

    assert _sign_in(client, "secure1") == (401, "mfa_required")
    assert _sign_in(client, "secure1", "000000") == (401, "invalid_code")
    code = code_now(secret)
    assert _sign_in(client, "secure1", code)[0] == 200
    assert _sign_in(client, "secure1", code) == (401, "invalid_code")  # replay
    assert _sign_in(client, "secure1", recovery[0])[0] == 200
    assert _sign_in(client, "secure1", recovery[0]) == (401, "invalid_code")


def test_sensitive_changes_need_a_recent_code(
    client: TestClient, make_user: Callable[..., User], clock: Clock
) -> None:
    make_user("boss1", ["owner"])
    secret, _ = enrol(client, login(client, "boss1"))
    body = client.post(
        LOGIN,
        json={"username": "boss1", "password": DEFAULT_PASSWORD, "code": code_now(secret)},
    ).json()
    fresh = {"Authorization": f"Bearer {body['access_token']}"}
    lock = {"lock_date": None}
    assert client.put("/api/v1/settings/period-lock", headers=fresh, json=lock).status_code == 200

    refreshed = client.post("/api/v1/auth/refresh").json()["access_token"]
    stale = {"Authorization": f"Bearer {refreshed}"}
    refused = client.put("/api/v1/settings/period-lock", headers=stale, json=lock)
    assert refused.json()["code"] == "step_up_required"

    stepped = client.post("/api/v1/auth/step-up", headers=stale, json={"code": code_now(secret, 1)})
    again = {"Authorization": f"Bearer {stepped.json()['access_token']}"}
    assert client.put("/api/v1/settings/period-lock", headers=again, json=lock).status_code == 200


def test_owners_must_enrol_when_it_is_required(
    client: TestClient, make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "require_owner_mfa", True)
    make_user("newboss", ["owner"])
    make_user("till1", ["cashier"])
    owner = login(client, "newboss")

    blocked = client.get("/api/v1/products", headers=owner)

    assert blocked.json()["code"] == "mfa_enrolment_required"
    assert client.get("/api/v1/auth/me", headers=owner).json()["mfa_enrolment_required"]
    enrol(client, owner)
    assert client.get("/api/v1/products", headers=owner).status_code == 200
    assert client.get("/api/v1/products", headers=login(client, "till1")).status_code == 200


def test_sessions_can_be_listed_and_ended_by_their_owner_only(
    client: TestClient, make_user: Callable[..., User]
) -> None:
    make_user("roamer", ["cashier"])
    make_user("nosy", ["cashier"])
    login(client, "roamer")
    first_cookie = client.cookies.get("ts_refresh")
    client.cookies.clear()
    headers = login(client, "roamer")

    sessions = client.get("/api/v1/auth/sessions", headers=headers).json()

    assert [s["current"] for s in sessions] == [False, True]
    other = sessions[0]["id"]
    nosy = login(client, "nosy")
    assert client.delete(f"/api/v1/auth/sessions/{other}", headers=nosy).status_code == 404
    assert client.delete(f"/api/v1/auth/sessions/{other}", headers=headers).status_code == 204
    client.cookies.clear()
    client.cookies.set("ts_refresh", str(first_cookie), path="/api/v1/auth")
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_a_sign_in_does_not_last_for_ever(
    client: TestClient, make_user: Callable[..., User], clock: Clock
) -> None:
    make_user("patient", ["cashier"])
    login(client, "patient")
    for _ in range(8):
        clock.advance(days=11)  # within the 14-day idle limit each time
        assert client.post("/api/v1/auth/refresh").status_code == 200
    clock.advance(days=3)  # 91 days after signing in
    assert client.post("/api/v1/auth/refresh").status_code == 401
