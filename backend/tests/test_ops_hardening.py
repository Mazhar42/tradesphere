"""Production hardening: sign-in rate limit, production config guard, structured logs."""

import json
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import DEV_JWT_SECRET, Settings
from app.core.context import request_id_var
from app.core.observability import JsonFormatter
from tests.conftest import DEFAULT_PASSWORD


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"database_url": "postgresql+psycopg://x:y@localhost/z"}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


# --- Production configuration guard ------------------------------------------------------------
def test_production_refuses_the_development_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _settings(environment="production", jwt_secret=DEV_JWT_SECRET)


def test_production_refuses_a_short_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _settings(environment="production", jwt_secret="too-short")


def test_production_refuses_a_wildcard_cors_origin() -> None:
    with pytest.raises(ValidationError, match="CORS"):
        _settings(
            environment="production",
            jwt_secret="0123456789abcdef0123456789abcdef",
            cors_origins=["*"],
        )


def test_production_accepts_a_strong_secret_and_a_named_origin() -> None:
    config = _settings(
        environment="production",
        jwt_secret="0123456789abcdef0123456789abcdef",
        cors_origins=["https://tradesphere.example"],
        mfa_encryption_key="fedcba9876543210fedcba9876543210",
    )
    assert config.is_production
    assert config.owner_mfa_required


def test_production_requires_its_own_mfa_key() -> None:
    with pytest.raises(ValidationError, match="MFA_ENCRYPTION_KEY"):
        _settings(
            environment="production",
            jwt_secret="0123456789abcdef0123456789abcdef",
            cors_origins=["https://tradesphere.example"],
        )


def test_development_keeps_the_convenient_defaults() -> None:
    assert (
        _settings(environment="development", jwt_secret=DEV_JWT_SECRET).environment == "development"
    )


# --- Sign-in rate limit ---------------------------------------------------------------------
def test_repeated_failures_block_the_user_even_with_the_right_password(
    client: TestClient, make_user: Any
) -> None:
    make_user("locked1", ["owner"])
    for _ in range(5):
        bad = client.post(
            "/api/v1/auth/login", json={"username": "locked1", "password": "wrong-password-1"}
        )
        assert bad.status_code == 401

    blocked = client.post(
        "/api/v1/auth/login", json={"username": "locked1", "password": DEFAULT_PASSWORD}
    )

    assert blocked.status_code == 429
    assert blocked.json()["code"] == "login_rate_limited"


def test_the_limit_is_per_username(client: TestClient, make_user: Any) -> None:
    make_user("locked2", ["owner"])
    make_user("bystander", ["owner"])
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"username": "locked2", "password": "nope-nope-1"})

    response = client.post(
        "/api/v1/auth/login", json={"username": "bystander", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 200


# --- Structured logs -----------------------------------------------------------------------------
def test_json_formatter_writes_one_object_per_line_with_the_request_id() -> None:
    record = logging.LogRecord("app.access", logging.INFO, __file__, 1, "request", None, None)
    record.method = "GET"  # type: ignore[attr-defined]
    record.path = "/api/health"  # type: ignore[attr-defined]
    record.status = 200  # type: ignore[attr-defined]
    token = request_id_var.set("abc123")
    try:
        line = JsonFormatter().format(record)
    finally:
        request_id_var.reset(token)

    payload = json.loads(line)
    assert "\n" not in line
    assert payload["logger"] == "app.access"
    assert payload["request_id"] == "abc123"
    assert payload["path"] == "/api/health"
    assert payload["status"] == 200


def test_every_request_is_access_logged(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.access")

    client.get("/api/health", headers={"X-Request-ID": "req-42"})

    records = [r for r in caplog.records if r.name == "app.access"]
    assert records, "no access log line was written"
    last = records[-1]
    assert last.path == "/api/health"  # type: ignore[attr-defined]
    assert last.status == 200  # type: ignore[attr-defined]
    assert client.get("/api/health").headers["X-Request-ID"]
