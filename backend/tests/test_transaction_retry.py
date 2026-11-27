"""A request aborted by a deadlock or serialisation failure is run once more, then answers 409."""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from psycopg import errors
from sqlalchemy.exc import OperationalError

from app.modules.settings import service


def _failing(
    monkeypatch: pytest.MonkeyPatch, name: str, *, times: int, error: type[Exception]
) -> list[int]:
    """Make `settings.service.<name>` raise `error` for its first `times` calls."""
    original: Callable[..., Any] = getattr(service, name)
    calls: list[int] = []

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) <= times:
            raise OperationalError("UPDATE ...", {}, error("simulated"))
        return original(*args, **kwargs)

    monkeypatch.setattr(service, name, flaky)
    return calls


def _preferences(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    return dict(client.get("/api/v1/settings", headers=headers).json()["preferences"])


@pytest.mark.parametrize("error", [errors.DeadlockDetected, errors.SerializationFailure])
def test_one_conflict_is_retried_with_the_same_body(
    client: TestClient,
    owner: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    error: type[Exception],
) -> None:
    wanted = {**_preferences(client, owner), "near_expiry_days": 45}
    calls = _failing(monkeypatch, "put_setting", times=1, error=error)

    response = client.put("/api/v1/settings/preferences", headers=owner, json=wanted)

    assert response.status_code == 200, response.text
    assert len(calls) == 2
    assert _preferences(client, owner)["near_expiry_days"] == 45


def test_a_repeated_conflict_answers_409(
    client: TestClient, owner: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _failing(monkeypatch, "get_all", times=2, error=errors.DeadlockDetected)

    response = client.get("/api/v1/settings", headers=owner)

    assert response.status_code == 409
    assert response.json()["code"] == "concurrent_update"
    assert response.headers["X-Request-ID"]
    assert len(calls) == 2


def test_other_database_errors_are_not_retried(
    client: TestClient, owner: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _failing(monkeypatch, "get_all", times=1, error=errors.DiskFull)

    with pytest.raises(OperationalError):
        client.get("/api/v1/settings", headers=owner)
    assert len(calls) == 1


def test_a_request_that_already_committed_is_not_run_again(
    client: TestClient, owner: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    wanted = {**_preferences(client, owner), "near_expiry_days": 60}
    writes = _failing(monkeypatch, "put_setting", times=0, error=errors.DeadlockDetected)
    # The router commits and then reads back; fail that read.
    _failing(monkeypatch, "get_all", times=1, error=errors.DeadlockDetected)

    with pytest.raises(OperationalError):
        client.put("/api/v1/settings/preferences", headers=owner, json=wanted)
    assert len(writes) == 1
