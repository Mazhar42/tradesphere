"""No tokens after a session ends (TS-15), no postings into a period being locked (TS-18)."""

import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.permissions import CurrentUser
from app.modules.auth import service as auth
from app.modules.settings.schemas import PeriodLock
from app.modules.settings.service import ensure_period_open, set_period_lock
from app.modules.users.models import RefreshToken, User
from app.modules.users.permissions_catalog import PERMISSIONS
from app.modules.users.schemas import UserCreate
from app.modules.users.service import create_user, reset_password
from app.shared.dates import business_date
from tests.conftest import DEFAULT_PASSWORD, login
from tests.helpers import wait_until_blocked


def _rotated_cookie(client: TestClient) -> str:
    """Sign in, refresh once, and return the cookie that the refresh replaced."""
    login(client, "grace1")
    old = client.cookies.get("ts_refresh")
    assert client.post("/api/v1/auth/refresh").status_code == 200
    assert old
    return old


def _replay(client: TestClient, cookie: str) -> int:
    client.cookies.clear()
    client.cookies.set("ts_refresh", cookie, path="/api/v1/auth")
    return client.post("/api/v1/auth/refresh").status_code


def test_no_token_for_a_replaced_cookie_after_logout(
    client: TestClient, make_user: Callable[..., User]
) -> None:
    make_user("grace1", ["cashier"])
    old = _rotated_cookie(client)
    assert client.post("/api/v1/auth/logout").status_code == 204

    assert _replay(client, old) == 401


def test_no_token_for_a_replaced_cookie_after_a_password_reset(
    client: TestClient, make_user: Callable[..., User], owner: dict[str, str]
) -> None:
    user = make_user("grace1", ["cashier"])
    old = _rotated_cookie(client)
    response = client.post(
        f"/api/v1/users/{user.id}/reset-password",
        headers=owner,
        json={"new_password": "An0ther-Passw0rd"},
    )
    assert response.status_code == 204, response.text

    assert _replay(client, old) == 401


# --- Races: committed data and real threads, in a throwaway database ----------------------------


def _session(engine: Engine) -> Session:
    return Session(engine, autoflush=False, expire_on_commit=False)


def _owner(engine: Engine) -> CurrentUser:
    with _session(engine) as session:
        user = create_user(
            session,
            UserCreate(
                username="race-owner",
                full_name="Race Owner",
                password=DEFAULT_PASSWORD,
                roles=["owner"],
            ),
            actor=None,
        )
        session.commit()
        return CurrentUser(
            id=user.id,
            username=user.username,
            full_name=user.full_name,
            roles=("owner",),
            permissions=frozenset(PERMISSIONS),
        )


def _in_thread(
    engine: Engine, work: Callable[[Session], Any]
) -> tuple[threading.Thread, list[Any]]:
    outcome: list[Any] = []

    def run() -> None:
        with _session(engine) as session:
            try:
                work(session)
                session.commit()
                outcome.append(None)
            except BaseException as exc:
                outcome.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, outcome


@pytest.mark.parametrize("ending", ["logout", "password reset"])
def test_a_session_ending_during_a_refresh_also_ends_the_new_token(
    scratch_engine: Engine, ending: str
) -> None:
    actor = _owner(scratch_engine)
    with _session(scratch_engine) as session:
        cookie = auth.login(session, actor.username, DEFAULT_PASSWORD, None, None).refresh_token
        session.commit()
    assert cookie

    def end(session: Session) -> None:
        if ending == "logout":
            auth.logout(session, cookie)
        else:
            reset_password(session, actor.id, "An0ther-Passw0rd", actor)

    with _session(scratch_engine) as refreshing:
        auth.refresh(refreshing, cookie, None, None)  # new token written, not yet committed
        thread, outcome = _in_thread(scratch_engine, end)
        try:
            wait_until_blocked(scratch_engine)
        finally:
            refreshing.commit()
            thread.join(timeout=30)

    assert outcome == [None]
    with _session(scratch_engine) as session:
        alive = session.scalar(
            select(func.count()).select_from(RefreshToken).where(RefreshToken.revoked_at.is_(None))
        )
    assert alive == 0


def test_locking_a_period_waits_for_a_posting_in_progress(scratch_engine: Engine) -> None:
    actor = _owner(scratch_engine)
    yesterday = business_date() - timedelta(days=1)

    with _session(scratch_engine) as posting:
        ensure_period_open(posting, yesterday)
        thread, outcome = _in_thread(
            scratch_engine,
            lambda s: set_period_lock(s, PeriodLock(lock_date=yesterday), actor),
        )
        try:
            wait_until_blocked(scratch_engine)
        finally:
            posting.commit()
            thread.join(timeout=30)

    assert outcome == [None]


def test_a_posting_waits_for_a_period_lock_being_saved_and_then_obeys_it(
    scratch_engine: Engine,
) -> None:
    actor = _owner(scratch_engine)
    yesterday = business_date() - timedelta(days=1)

    with _session(scratch_engine) as locking:
        set_period_lock(locking, PeriodLock(lock_date=yesterday), actor)  # flushed, uncommitted
        thread, outcome = _in_thread(scratch_engine, lambda s: ensure_period_open(s, yesterday))
        try:
            wait_until_blocked(scratch_engine)
        finally:
            locking.commit()
            thread.join(timeout=30)

    assert isinstance(outcome[0], ConflictError), outcome
    assert outcome[0].code == "period_locked"
