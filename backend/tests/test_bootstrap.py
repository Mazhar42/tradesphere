"""First-owner bootstrap for hosts without a shell, and database URL normalisation."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.bootstrap import ensure_owner
from app.core.config import Settings
from app.core.errors import ConflictError
from app.modules.users.models import User
from app.modules.users.service import count_active_owners
from tests.conftest import login


def _url(value: str) -> str:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=value,
        jwt_secret="0123456789abcdef0123456789abcdef",
    ).database_url


def test_plain_postgres_urls_are_switched_to_the_psycopg_driver() -> None:
    assert _url("postgresql://u:p@host:5432/db") == "postgresql+psycopg://u:p@host:5432/db"
    assert _url("postgres://u:p@host/db") == "postgresql+psycopg://u:p@host/db"


def test_explicit_driver_urls_are_left_alone() -> None:
    assert _url("postgresql+psycopg://u:p@host/db") == "postgresql+psycopg://u:p@host/db"


def test_bootstrap_creates_one_owner_who_can_sign_in(db: Session, client: TestClient) -> None:
    created = ensure_owner(
        db, username="founder", full_name="Founder Name", password="Founder-Passw0rd"
    )

    assert created is True
    assert count_active_owners(db) == 1
    headers = login(client, "founder", "Founder-Passw0rd")
    assert client.get("/api/v1/auth/me", headers=headers).json()["username"] == "founder"


def test_bootstrap_does_nothing_once_an_owner_exists(db: Session) -> None:
    ensure_owner(db, username="founder", full_name="Founder", password="Founder-Passw0rd")

    again = ensure_owner(db, username="second", full_name="Second", password="Second-Passw0rd")

    assert again is False
    assert count_active_owners(db) == 1


def test_bootstrap_refuses_a_username_that_is_not_an_owner(
    db: Session, make_user: Callable[..., User]
) -> None:
    make_user("cashier9", ["cashier"])

    with pytest.raises(ConflictError):
        ensure_owner(db, username="cashier9", full_name="X", password="Owner-Passw0rd")
