"""Domain exceptions rendered as RFC 9457 problem+json responses."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings
from app.core.db import commit_log, is_transient_conflict

logger = logging.getLogger("app.errors")

PROBLEM_JSON = "application/problem+json"


class AppError(Exception):
    status: int = 400
    code: str = "bad_request"
    title: str = "Bad request"

    def __init__(
        self,
        detail: str,
        *,
        code: str | None = None,
        errors: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code
        self.errors = errors


class NotFoundError(AppError):
    status = 404
    code = "not_found"
    title = "Not found"


class ConflictError(AppError):
    status = 409
    code = "conflict"
    title = "Conflict"


class BusinessRuleError(AppError):
    status = 422
    code = "business_rule_violation"
    title = "Business rule violation"


class UnauthorizedError(AppError):
    status = 401
    code = "unauthorized"
    title = "Unauthorized"


class ForbiddenError(AppError):
    status = 403
    code = "forbidden"
    title = "Forbidden"


class RateLimitedError(AppError):
    status = 429
    code = "rate_limited"
    title = "Too many requests"


def problem(
    status: int,
    title: str,
    detail: str,
    code: str,
    errors: list[dict[str, str]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
    }
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


class RetryTransientConflicts:
    """Run a request a second time when PostgreSQL aborted it for a deadlock or a
    serialisation failure; if that fails too, answer 409 so the client can try again.

    Only whole, uncommitted operations are repeated: once the request has committed anything,
    or started answering, the error is passed on unchanged.
    """

    ATTEMPTS = 2

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        body: list[Message] = []  # what has been read, so a second attempt can read it again
        answering = False

        async def tracked_send(message: Message) -> None:
            nonlocal answering
            answering = True
            await send(message)

        for attempt in range(1, self.ATTEMPTS + 1):
            position = 0

            async def replay() -> Message:
                nonlocal position
                if position == len(body):
                    body.append(await receive())
                position += 1
                return body[position - 1]

            commits: list[bool] = []
            token = commit_log.set(commits)
            try:
                await self.app(dict(scope), replay, tracked_send)
                return
            except OperationalError as exc:
                if not is_transient_conflict(exc) or commits or answering:
                    raise
                logger.warning(
                    "Transaction conflict on %s %s (attempt %d of %d): %s",
                    scope["method"],
                    scope["path"],
                    attempt,
                    self.ATTEMPTS,
                    getattr(exc.orig, "sqlstate", None),
                )
            finally:
                commit_log.reset(token)

        response = problem(
            409,
            "Conflict",
            "Another change was being saved at the same moment. Please try again.",
            "concurrent_update",
        )
        await response(scope, receive, send)


def _field_from_constraint(constraint: str | None) -> str | None:
    """`uq_products_sku` -> `sku` (naming convention: uq_<table>_<column>)."""
    if not constraint or not constraint.startswith("uq_"):
        return None
    from app.shared.models import Base  # noqa: PLC0415 (local import avoids a cycle)

    rest = constraint[3:]
    for table in sorted(Base.metadata.tables, key=len, reverse=True):
        if rest.startswith(table + "_"):
            return rest[len(table) + 1 :]
    return None


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return problem(exc.status, exc.title, exc.detail, exc.code, exc.errors, headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "field": ".".join(str(p) for p in err["loc"] if p not in ("body", "query", "path")),
                "message": err["msg"],
            }
            for err in exc.errors()
        ]
        return problem(
            422, "Validation failed", "One or more fields are invalid.", "validation_error", errors
        )

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError) -> JSONResponse:
        diag = getattr(exc.orig, "diag", None)
        constraint = getattr(diag, "constraint_name", None)
        sqlstate = getattr(exc.orig, "sqlstate", None)
        if sqlstate == "23505":  # unique_violation
            field = _field_from_constraint(constraint)
            errors = [{"field": field, "message": "Already exists"}] if field else None
            return problem(
                409, "Conflict", "A record with this value already exists.", "duplicate", errors
            )
        if sqlstate == "23503":  # foreign_key_violation
            return problem(
                409,
                "Conflict",
                "A referenced record does not exist or is in use.",
                "reference_error",
            )
        logger.warning("Integrity error (%s): %s", sqlstate, exc.orig)
        detail = "The data violates a database constraint."
        if not settings.is_production:
            culprit = constraint or getattr(diag, "column_name", None)
            if culprit:
                detail += f" [{culprit}]"
        return problem(409, "Conflict", detail, "constraint_violation")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem(exc.status_code, str(exc.detail), str(exc.detail), "http_error")
