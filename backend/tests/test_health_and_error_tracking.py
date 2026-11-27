"""Liveness and readiness, and what an error report may contain."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from psycopg import errors
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import health
from app.core.observability import init_error_tracking
from app.main import create_app


def test_live_and_ready(client: TestClient) -> None:
    assert client.get("/api/live").json() == {"status": "ok"}
    assert client.get("/api/ready").json() == {"status": "ready"}


def _code_ships(monkeypatch: pytest.MonkeyPatch, db: Session, *, newer: bool) -> None:
    """Pretend the code is one migration ahead of the database, or knows nothing of it."""
    current = str(db.scalar(text("SELECT version_num FROM alembic_version")))
    known = {current, "next"} if newer else {"previous"}
    head = "next" if newer else "previous"
    monkeypatch.setattr(health, "_revisions", lambda: (head, frozenset(known)))


def test_not_ready_while_migrations_are_pending(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _code_ships(monkeypatch, db, newer=True)

    response = client.get("/api/ready")

    assert response.status_code == 503
    assert response.json()["code"] == "migrations_pending"
    assert client.get("/api/live").status_code == 200


def test_still_ready_when_the_schema_is_newer_than_the_code(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _code_ships(monkeypatch, db, newer=False)

    assert client.get("/api/ready").status_code == 200


class _Captured(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        event = envelope.get_event()
        if event is not None:
            self.events.append(dict(event))


@pytest.fixture
def captured() -> Iterator[_Captured]:
    transport = _Captured()
    init_error_tracking("https://public@errors.invalid/1", transport=transport)
    try:
        yield transport
    finally:
        sentry_sdk.init()  # no DSN: switched off again


def test_an_unhandled_error_is_reported_without_business_data(captured: _Captured) -> None:
    app = create_app()

    @app.post("/boom")
    def boom() -> None:
        customer = "PRIVATE-local-variable"
        raise OperationalError(
            "SELECT * FROM customers WHERE name = %(name)s",
            {"name": "PRIVATE-sql-parameter"},
            errors.DiskFull(f"PRIVATE-database-detail {len(customer)}"),
        )

    with TestClient(app, raise_server_exceptions=False) as client:
        client.cookies.set("ts_refresh", "PRIVATE-cookie")
        response = client.post(
            "/boom?q=PRIVATE-query",
            json={"amount": "PRIVATE-body"},
            headers={"Authorization": "Bearer PRIVATE-token"},
        )
    sentry_sdk.flush()

    assert response.status_code == 500
    assert len(captured.events) == 1, captured.events
    event = captured.events[0]
    assert set(event["request"]) <= {"method", "url"}
    assert event["exception"]["values"][-1]["type"] == "OperationalError"
    assert event["tags"]["request_id"]
    # Source lines around each frame are code, not data, and here the code spells out the
    # values: drop them, then none of the values may appear anywhere in the report.
    for value in event["exception"]["values"]:
        for frame in value["stacktrace"]["frames"]:
            assert "vars" not in frame
            for source in ("pre_context", "context_line", "post_context"):
                frame.pop(source, None)
    assert "PRIVATE" not in json.dumps(event, default=str)
    assert "ts_refresh" not in json.dumps(event, default=str)
