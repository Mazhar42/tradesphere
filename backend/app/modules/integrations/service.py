"""Receiving from another system. Everything that arrives as records is **staged** for a person
to approve; no integration posts stock or money. Snapshots are kept apart from the books."""

import hashlib
import json
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import secret_box
from app.core.errors import AppError, BusinessRuleError, ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.core.tenant_context import tenant_id_of
from app.modules.billing import entitlements
from app.modules.catalog.models import Product
from app.modules.imports import fields, parsing
from app.modules.imports import service as imports
from app.modules.imports.entities import ENTITIES
from app.modules.imports.models import CutoverSignoff, ImportJob
from app.modules.integrations import fetch as fetcher
from app.modules.integrations.models import (
    DomainOwnership,
    ExternalSnapshot,
    IntegrationConnection,
    SyncRun,
)
from app.modules.integrations.ownership import DOMAINS, NEEDS, owners
from app.modules.jobs.service import enqueue, queue_email
from app.modules.parties.models import Customer, Supplier
from app.modules.users.models import Role, User, user_roles
from app.shared import email
from app.shared.audit import record_audit
from app.shared.dates import now_utc

logger = logging.getLogger("app.integrations")

SEALED_FOR = "integration"
SNAPSHOTS = ("stock_snapshot", "balance_snapshot")
ALERT_AFTER = 3
SHORTEST_MINUTES = 15
NEEDS_SIGNOFF = ("stock", "sales", "receivables", "accounting")


def feed_kinds() -> list[str]:
    """What another system may send. Opening positions are loaded by a person, once."""
    return [*(e.name for e in ENTITIES.values() if e.opening_domain is None), *SNAPSHOTS]


# --- Ownership -------------------------------------------------------------------------------


def set_owner(session: Session, domain: str, owner: str, actor: CurrentUser) -> None:
    if domain not in DOMAINS or owner not in ("external", "tradesphere"):
        raise BusinessRuleError("Unknown domain or owner.", code="bad_domain")
    current = owners(session)
    before = current[domain]
    current[domain] = owner
    for dependent, needed in NEEDS.items():
        missing = [n for n in needed if current[n] == "external"]
        if current[dependent] == "tradesphere" and missing:
            raise ConflictError(
                f"{dependent.capitalize()} cannot be here while {missing[0]} is not.",
                code="domain_dependency",
            )
    if (
        before == "external"
        and owner == "tradesphere"
        and domain in NEEDS_SIGNOFF
        and session.scalar(select(CutoverSignoff.id).limit(1)) is None
    ):
        raise ConflictError(
            "Load the opening figures and sign them off first.", code="cutover_not_signed"
        )
    statement = insert(DomainOwnership).values(domain=domain, owner=owner, created_by_id=actor.id)
    session.execute(
        statement.on_conflict_do_update(
            constraint="uq_domain_ownership_domain",
            set_={"owner": owner, "created_by_id": actor.id, "changed_at": now_utc()},
        )
    )
    record_audit(
        session,
        actor_id=actor.id,
        action="set_owner",
        entity_type="domain",
        entity_id=domain,
        before={"owner": before},
        after={"owner": owner},
    )


# --- Receiving ----------------------------------------------------------------------------------


def _when(value: str) -> datetime:
    moment = datetime.fromisoformat(value.strip())
    if moment.tzinfo is None:
        raise ValueError("give the time zone, for example 2026-11-27T18:00:00+06:00")
    return moment


def _known(session: Session, model: Any, column: Any, code: str) -> int | None:
    found: int | None = session.scalar(select(model.id).where(column == code))
    return found


def save_snapshot(
    session: Session, kind: str, source: str, rows: Sequence[dict[str, str]]
) -> dict[str, int]:
    """Stock rows: sku, quantity, as_of, [location]. Balance rows: party_type, code, amount,
    as_of. Older figures never overwrite newer ones."""
    stored = unmatched = 0
    for number, row in enumerate(rows, start=1):
        try:
            if kind == "stock":
                code = fields.code(row["sku"])
                detail = row.get("location", "").strip()[:60]
                value = fields.number(row["quantity"], places=3)
                internal = _known(session, Product, Product.sku, code)
            else:
                code = fields.code(row["code"])
                detail = row["party_type"].strip().lower()
                if detail not in ("customer", "supplier"):
                    raise ValueError("party_type is customer or supplier")
                # A balance may be negative: an advance.
                text = row["amount"].strip()
                value = fields.number(text.removeprefix("-"), places=2)
                value = -value if text.startswith("-") else value
                model, column = (
                    (Customer, Customer.code) if detail == "customer" else (Supplier, Supplier.code)
                )
                internal = _known(session, model, column, code)
            as_of = _when(row["as_of"])
        except (KeyError, ValueError, ArithmeticError) as error:
            raise BusinessRuleError(
                f"Row {number}: {type(error).__name__}: {str(error)[:100]}", code="bad_snapshot"
            ) from error
        statement = insert(ExternalSnapshot).values(
            kind=kind,
            source_system=source,
            item_key=f"{detail}|{code}".lower(),
            code=code,
            detail=detail,
            internal_id=internal,
            value=value,
            as_of=as_of,
        )
        session.execute(
            statement.on_conflict_do_update(
                constraint="uq_external_snapshots_item",
                set_={
                    "value": value,
                    "internal_id": internal,
                    "as_of": as_of,
                    "received_at": now_utc(),
                },
                where=ExternalSnapshot.as_of <= as_of,
            )
        )
        stored += 1
        unmatched += internal is None
    return {"rows": stored, "unmatched": unmatched}


def stage_file(
    session: Session, *, data_kind: str, source: str, content: bytes, actor: CurrentUser
) -> dict[str, Any]:
    """One file from another system: a snapshot is stored, anything else waits for approval."""
    if data_kind not in feed_kinds():
        raise BusinessRuleError("This kind of data cannot be sent.", code="unknown_entity")
    if data_kind in SNAPSHOTS:
        _, rows = parsing.parse(content)
        return save_snapshot(session, data_kind.removesuffix("_snapshot"), source, rows)
    # A newer file makes an unapproved older one from the same source pointless.
    for old in session.scalars(
        select(ImportJob)
        .where(
            ImportJob.entity == data_kind,
            ImportJob.source_system == source,
            ImportJob.status.in_(imports.OPEN_STATUSES),
        )
        .with_for_update()
    ):
        old.status = "cancelled"
        old.message = "Replaced by a newer file."
    job = imports.upload(
        session,
        entity_name=data_kind,
        source_system=source,
        file_name=f"{source}-{data_kind}.csv",
        data=content,
        options={"conflict_policy": "update" if ENTITIES[data_kind].update else "skip"},
        actor=actor,
    )
    return {"import_job_id": job.id, "status": job.status, **job.counts}


# --- Connections ------------------------------------------------------------------------------


def get_connection(session: Session, connection_id: int, *, lock: bool = False) -> Any:
    statement = select(IntegrationConnection).where(IntegrationConnection.id == connection_id)
    if lock:
        statement = statement.with_for_update()
    found = session.scalar(statement.execution_options(populate_existing=True))
    if found is None:
        raise NotFoundError("Connection not found.")
    return found


def save_connection(
    session: Session,
    connection: IntegrationConnection | None,
    *,
    values: dict[str, Any],
    header_name: str | None,
    header_value: str | None,
    actor: CurrentUser,
) -> IntegrationConnection:
    entitlements.require_feature(session, "api_access")
    for feed in values["feeds"]:
        if feed["data"] not in feed_kinds():
            raise BusinessRuleError("This kind of data cannot be fetched.", code="unknown_entity")
        fetcher.check_url(feed["url"])
    every = values["every_minutes"]
    if every is not None and every < SHORTEST_MINUTES:
        raise BusinessRuleError(
            f"The shortest interval is {SHORTEST_MINUTES} minutes.", code="bad_schedule"
        )
    created = connection is None
    previous_urls = [] if connection is None else [f["url"] for f in connection.feeds]
    if connection is None:
        connection = IntegrationConnection(state={}, created_by_id=actor.id, **values)
        session.add(connection)
    else:
        # A stored password must not be sent to an address it was not given for.
        moved = {fetcher.check_url(f["url"])[0] for f in values["feeds"]} - {
            fetcher.check_url(u)[0] for u in previous_urls
        }
        if moved and not header_value:
            connection.secret = None
        for name, value in values.items():
            setattr(connection, name, value)
    if header_value:
        sealed = json.dumps({(header_name or "Authorization"): header_value})
        if "\r" in header_value or "\n" in header_value:
            raise BusinessRuleError("The credential has a line break.", code="bad_credential")
        connection.secret = secret_box.seal(SEALED_FOR, sealed)
    connection.failures = 0
    connection.next_run_at = None
    session.flush()
    schedule(session, connection)
    record_audit(
        session,
        actor_id=actor.id,
        action="create" if created else "update",
        entity_type="integration_connection",
        entity_id=connection.id,
        after={
            "name": connection.name,
            "feeds": connection.feeds,
            "every_minutes": connection.every_minutes,
            "enabled": connection.enabled,
        },
    )
    return connection


def schedule(session: Session, connection: IntegrationConnection) -> None:
    """Queue the next timed run. A job whose time no longer matches does nothing."""
    if not connection.enabled or connection.every_minutes is None:
        connection.next_run_at = None
        return
    moment = connection.next_run_at
    if moment is None or moment <= now_utc():
        moment = now_utc() + timedelta(minutes=connection.every_minutes)
        connection.next_run_at = moment
    session.flush()
    enqueue(
        session,
        "integration.sync",
        {"connection_id": connection.id, "due": moment.isoformat()},
        tenant_id=tenant_id_of(session),
        run_at=moment,
        dedupe_key=f"integration:{connection.id}:{moment.isoformat()}",
    )


def reschedule_overdue(session: Session) -> None:
    """Daily safety net for a chain of timed runs that was broken."""
    overdue = select(IntegrationConnection).where(
        IntegrationConnection.enabled.is_(True),
        IntegrationConnection.every_minutes.is_not(None),
        IntegrationConnection.next_run_at < now_utc() - timedelta(hours=1),
    )
    for connection in session.scalars(overdue):
        connection.next_run_at = None
        schedule(session, connection)


def sync(session: Session, connection_id: int, due: str | None) -> None:
    """Fetch every feed once. A failure is recorded, not raised: the schedule goes on."""
    connection = session.get(IntegrationConnection, connection_id, populate_existing=True)
    if connection is None or not connection.enabled:
        return
    if due is not None and (
        connection.next_run_at is None or connection.next_run_at != datetime.fromisoformat(due)
    ):
        return
    entitlements.ensure_writable(session)
    entitlements.require_feature(session, "api_access")
    started = now_utc()
    actor = CurrentUser(
        id=connection.created_by_id or 0,
        username="",
        full_name="",
        roles=(),
        permissions=frozenset(),
    )
    headers = (
        json.loads(secret_box.unseal(SEALED_FOR, connection.secret)) if connection.secret else None
    )
    state = dict(connection.state)
    results: list[dict[str, Any]] = []
    for feed in connection.feeds:
        kind = feed["data"]
        try:
            content = fetcher.fetch(feed["url"], headers)
            digest = hashlib.sha256(content).hexdigest()
            if state.get(kind) == digest:
                results.append({"data": kind, "unchanged": True})
                continue
            with session.begin_nested():
                outcome = stage_file(
                    session,
                    data_kind=kind,
                    source=connection.source_system,
                    content=content,
                    actor=actor,
                )
            state[kind] = digest
            results.append({"data": kind, **outcome})
        except AppError as error:
            results.append({"data": kind, "problem": error.detail[:300]})
    failed = any("problem" in r for r in results)
    connection.state = state
    connection.last_attempt_at = started
    if failed:
        connection.failures += 1
        if connection.failures == ALERT_AFTER:
            _alert(session, connection)
    else:
        connection.failures = 0
        connection.last_success_at = started
    session.add(
        SyncRun(
            connection_id=connection.id,
            started_at=started,
            finished_at=now_utc(),
            status="failed" if failed else "ok",
            results=results,
        )
    )
    if due is not None:
        connection.next_run_at = None
        schedule(session, connection)
    session.flush()


def _alert(session: Session, connection: IntegrationConnection) -> None:
    logger.error("Connection %s failed %d times in a row", connection.id, ALERT_AFTER)
    owners_emails = session.scalars(
        select(User.email)
        .join(user_roles, user_roles.c.user_id == User.id)
        .join(Role, Role.id == user_roles.c.role_id)
        .where(Role.code == "owner", User.email.is_not(None), User.is_active.is_(True))
    ).all()
    if not email.can_send():
        return
    for address in owners_emails:
        assert address is not None
        queue_email(
            session,
            email.Message(
                to=address,
                subject="TradeSphere: a data connection keeps failing",
                body=(
                    f"The connection '{connection.name}' has failed {ALERT_AFTER} times in a "
                    "row. Open Integrations in TradeSphere to see why."
                ),
            ),
        )
