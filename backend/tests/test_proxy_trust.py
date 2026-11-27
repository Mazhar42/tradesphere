"""X-Forwarded-For is believed only from the proxies named in TRUSTED_PROXIES."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.core.db import get_db
from app.main import create_app
from app.modules.tenancy.models import AuthEvent

PROXY = "10.1.2.3"


def _recorded_ip(
    db: Session, monkeypatch: pytest.MonkeyPatch, *, peer: str, forwarded: str
) -> str | None:
    """The address stored for a failed sign-in arriving from `peer` with that header."""
    monkeypatch.setattr(settings, "trusted_proxies", ["10.0.0.0/8"])
    app = create_app()

    def _override() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app, client=(peer, 50000)) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "nobody", "password": "wrong-password"},
            headers={"X-Forwarded-For": forwarded},
        )
    assert response.status_code == 401
    return db.scalar(
        select(AuthEvent.ip).where(AuthEvent.action == "login_failed").order_by(AuthEvent.id.desc())
    )


def test_a_client_cannot_choose_its_own_address(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    ip = _recorded_ip(db, monkeypatch, peer="203.0.113.9", forwarded="6.6.6.6")
    assert ip == "203.0.113.9"


def test_the_address_added_by_a_trusted_proxy_wins_over_a_forged_one(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    ip = _recorded_ip(db, monkeypatch, peer=PROXY, forwarded="6.6.6.6, 203.0.113.9")
    assert ip == "203.0.113.9"


@pytest.mark.parametrize("value", [["*"], ["0.0.0.0/0"], ["::/0"], ["caddy"]])
def test_trusting_everything_or_nonsense_is_refused(value: list[str]) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://x@y/z", jwt_secret="s" * 40, trusted_proxies=value)
