"""Structured logging: one JSON object per line in production, readable text in development.

Every record carries the request id (when it was written during a request), so a line in the
access log can be matched with the application errors and the audit rows of the same request.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

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
