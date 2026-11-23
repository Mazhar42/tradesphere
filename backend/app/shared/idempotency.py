"""Idempotency keys for posting endpoints.

A client sends `Idempotency-Key: <uuid>` with every request that changes books. The key
row is inserted in the *same transaction* as the business operation:

- first request: the row is inserted, the operation runs, the response is stored, all
  committed together;
- a concurrent duplicate blocks on the unique index until the first request commits (or
  rolls back), then sees the stored response;
- a later duplicate returns the stored response without running the operation again;
- a failed request rolls back, releasing the key so the client can retry.

Re-using a key with a different request is a conflict (409).
"""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.errors import ConflictError
from app.shared.models import Base, IdMixin

IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=100,
        description="Unique key (e.g. a UUID) that makes retries of this request safe.",
    ),
]


class IdempotencyKey(IdMixin, Base):
    __tablename__ = "idempotency_keys"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    key: Mapped[str] = mapped_column(String(100))
    endpoint: Mapped[str] = mapped_column(String(150))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None]
    response_body: Mapped[Any] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "key"),)


def _hash(endpoint: str, payload: Any) -> str:
    raw = json.dumps([endpoint, payload], sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def run_idempotent(
    session: Session,
    *,
    user_id: int,
    key: str,
    endpoint: str,
    payload: Any,
    handler: Callable[[], BaseModel],
    status_code: int = 200,
) -> BaseModel | JSONResponse:
    request_hash = _hash(endpoint, payload)
    inserted_id = session.execute(
        insert(IdempotencyKey)
        .values(user_id=user_id, key=key, endpoint=endpoint, request_hash=request_hash)
        .on_conflict_do_nothing(index_elements=["user_id", "key"])
        .returning(IdempotencyKey.id)
    ).scalar_one_or_none()

    if inserted_id is None:
        existing = session.scalar(
            select(IdempotencyKey)
            .where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
            .execution_options(populate_existing=True)
        )
        if existing is None or existing.request_hash != request_hash:
            raise ConflictError(
                "This Idempotency-Key was already used for a different request.",
                code="idempotency_key_reuse",
            )
        if existing.response_body is None:
            raise ConflictError(
                "A request with this Idempotency-Key is still being processed.",
                code="idempotency_in_progress",
            )
        return JSONResponse(
            content=existing.response_body,
            status_code=existing.response_status or 200,
            headers={"Idempotent-Replayed": "true"},
        )

    result = handler()
    session.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.id == inserted_id)
        .values(response_status=status_code, response_body=result.model_dump(mode="json"))
    )
    return result
