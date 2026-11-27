"""Structured logging: one JSON object per line in production, readable text in development.

Every record carries the request id (when it was written during a request), so a line in the
access log can be matched with the application errors and the audit rows of the same request.

Unhandled errors can also be reported to an error tracker. A report carries the stack trace,
the method and path and the request id. It must not carry business or personal data: no
request body, query, headers or cookies, no local variables, and no database messages (they
quote SQL parameters and row values).
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.transport import Transport
from sentry_sdk.types import Event, Hint

from app.core.config import settings
from app.core.context import request_id_var

ACCESS_LOGGER = "app.access"
_EXTRA_FIELDS = ("method", "path", "status", "duration_ms", "ip", "username")
_TEXT_FORMAT = "%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s"


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for field in _EXTRA_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_configured = False


def configure_logging() -> None:
    """Attach one stderr handler to the `app` logger tree. Safe to call more than once."""
    global _configured  # noqa: PLW0603 - one-time setup guard
    if _configured:
        return
    _configured = True
    logger = logging.getLogger("app")
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(_RequestIdFilter())
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FORMAT))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _scrub(event: Event, _: Hint) -> Event:
    request = event.get("request")
    if request:
        event["request"] = {k: v for k, v in request.items() if k in ("method", "url")}
    event.pop("user", None)
    event.pop("breadcrumbs", None)
    event.pop("extra", None)
    exception: Any = event.get("exception") or {}
    for value in exception.get("values", []):
        if str(value.get("module")).split(".")[0] in ("sqlalchemy", "psycopg"):
            value["value"] = "Database error (the message is in the server log)."
    return event


def init_error_tracking(dsn: str, *, transport: Transport | None = None) -> None:
    sentry_sdk.init(
        dsn=dsn,
        transport=transport,
        environment=settings.environment,
        # Personal data stays off by default (`send_default_pii` unset); `_scrub` removes the rest.
        include_local_variables=False,
        max_request_body_size="never",
        max_breadcrumbs=0,
        auto_enabling_integrations=False,
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
            LoggingIntegration(level=None, event_level=None),
        ],
        before_send=_scrub,
    )


def access_log(*, method: str, path: str, status: int, duration_ms: float, ip: str | None) -> None:
    logging.getLogger(ACCESS_LOGGER).info(
        "request",
        extra={
            "method": method,
            "path": path,
            "status": status,
            "duration_ms": round(duration_ms, 1),
            "ip": ip,
        },
    )
