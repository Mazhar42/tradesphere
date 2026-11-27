"""Putting work on the queue (ADR-006). Both calls add rows in the caller's transaction, so
work exists exactly when the change that asked for it was committed."""

import json
from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import mfa_codes
from app.modules.jobs.models import Job, OutboxEvent
from app.shared import email

EMAIL = "email.send"


def enqueue(
    session: Session,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    tenant_id: int | None = None,
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
) -> None:
    values: dict[str, Any] = {
        "kind": kind,
        "payload": payload or {},
        "tenant_id": tenant_id,
        "dedupe_key": dedupe_key,
    }
    if run_at is not None:
        values["run_at"] = run_at
    statement = insert(Job).values(**values)
    if dedupe_key is not None:
        statement = statement.on_conflict_do_nothing(index_elements=["dedupe_key"])
    session.execute(statement)


def publish(
    session: Session, topic: str, payload: dict[str, Any], *, tenant_id: int | None = None
) -> None:
    session.add(OutboxEvent(topic=topic, payload=payload, tenant_id=tenant_id))


def queue_email(session: Session, message: email.Message) -> None:
    """Send after the current transaction commits. Messages carry one-time links, so they are
    stored encrypted."""
    body = json.dumps({"to": message.to, "subject": message.subject, "body": message.body})
    publish(
        session,
        EMAIL,
        {"sealed": mfa_codes.encrypt(body)},
        tenant_id=session.info.get("tenant_id"),
    )


def open_email(payload: dict[str, Any]) -> email.Message:
    return email.Message(**json.loads(mfa_codes.decrypt(payload["sealed"])))
