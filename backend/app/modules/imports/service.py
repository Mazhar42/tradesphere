"""The import job: upload → parse → validate (dry run) → approve → commit.

Approval is bound to the preview: if the rows or the records they match changed since, the
job goes back for another look. Master data commits in batches, so a retry continues where it
stopped; opening loads are all or nothing.
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import AppError, BusinessRuleError, ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.billing import entitlements
from app.modules.imports import openings, parsing
from app.modules.imports.entities import (
    ENTITIES,
    Checked,
    Context,
    Entity,
    Row,
    auto_mapping,
    create_order,
)
from app.modules.imports.fields import Invalid
from app.modules.imports.models import (
    CutoverSignoff,
    ExternalRef,
    ImportFile,
    ImportJob,
    ImportMapping,
    ImportRow,
)
from app.modules.integrity.reconcile import reconcile
from app.modules.settings.schemas import PeriodLock
from app.modules.settings.service import set_period_lock
from app.shared.audit import record_audit
from app.shared.dates import now_utc
from app.shared.money import ZERO

BATCH = 500
POLICIES = ("skip", "update", "reject")
KEEP_FILES_DAYS = 90
OPEN_STATUSES = ("needs_mapping", "dry_run_ready")


def entity_of(name: str) -> Entity:
    entity = ENTITIES.get(name)
    if entity is None:
        raise BusinessRuleError("This kind of data cannot be imported.", code="unknown_entity")
    return entity


def get_job(session: Session, job_id: int, *, lock: bool = False) -> ImportJob:
    statement = select(ImportJob).where(ImportJob.id == job_id)
    if lock:
        statement = statement.with_for_update()
    job = session.scalar(statement.execution_options(populate_existing=True))
    if job is None:
        raise NotFoundError("Import not found.")
    return job


def rows_of(session: Session, job_id: int) -> Sequence[ImportRow]:
    return session.scalars(
        select(ImportRow).where(ImportRow.job_id == job_id).order_by(ImportRow.row_no)
    ).all()


# --- Upload ------------------------------------------------------------------------------------


def upload(
    session: Session,
    *,
    entity_name: str,
    source_system: str,
    file_name: str,
    data: bytes,
    options: dict[str, Any],
    actor: CurrentUser,
) -> ImportJob:
    entity = entity_of(entity_name)
    headers, rows = parsing.parse(data)
    limit = entitlements.value(session, "max_import_rows").limit
    if limit is not None and len(rows) > limit:
        raise entitlements.SubscriptionRequiredError(
            f"Your plan allows {limit} rows per import.", code="plan_limit_reached"
        )
    digest = hashlib.sha256(data).hexdigest()
    seen_before = session.scalar(
        select(func.count(ImportJob.id))
        .join(ImportFile, ImportFile.id == ImportJob.file_id)
        .where(ImportFile.sha256 == digest, ImportJob.status == "completed")
    )
    file = ImportFile(
        original_name=file_name[:200],
        size=len(data),
        sha256=digest,
        content=data,
        created_by_id=actor.id,
    )
    session.add(file)
    session.flush()
    remembered = session.scalar(
        select(ImportMapping.mapping).where(
            ImportMapping.entity == entity.name, ImportMapping.source_system == source_system
        )
    )
    mapping = auto_mapping(entity, headers)
    if remembered and all(h in headers for h in remembered.values()):
        mapping = remembered
    job = ImportJob(
        entity=entity.name,
        source_system=source_system,
        file_id=file.id,
        status="needs_mapping",
        headers=headers,
        mapping=mapping,
        options=_clean_options(options),
        counts={},
        message="This file was imported before." if seen_before else None,
        created_by_id=actor.id,
    )
    session.add(job)
    session.flush()
    session.add_all(
        ImportRow(job_id=job.id, row_no=n, raw=row, status="new", errors=[])
        for n, row in enumerate(rows, start=2)  # row 1 is the heading
    )
    session.flush()
    validate(session, job)
    return job


def _clean_options(options: dict[str, Any]) -> dict[str, Any]:
    policy = options.get("conflict_policy", "skip")
    if policy not in POLICIES:
        raise BusinessRuleError("Unknown conflict policy.", code="bad_options")
    cutover = options.get("cutover_date")
    if cutover:
        date.fromisoformat(str(cutover))
    return {
        "conflict_policy": policy,
        "date_format": options.get("date_format", "YYYY-MM-DD"),
        "update_prices": bool(options.get("update_prices")),
        "update_credit_limit": bool(options.get("update_credit_limit")),
        "cutover_date": str(cutover) if cutover else None,
    }


def set_mapping(
    session: Session, job_id: int, mapping: dict[str, str], options: dict[str, Any] | None
) -> ImportJob:
    job = get_job(session, job_id, lock=True)
    if job.status not in OPEN_STATUSES:
        raise ConflictError("This import can no longer be changed.", code="not_open")
    names = {c.name for c in entity_of(job.entity).columns}
    if set(mapping) - names or set(mapping.values()) - set(job.headers):
        raise BusinessRuleError("The mapping names an unknown column.", code="bad_mapping")
    job.mapping = mapping
    if options is not None:
        job.options = _clean_options(options)
    validate(session, job)
    remember = insert(ImportMapping).values(
        entity=job.entity, source_system=job.source_system, mapping=mapping
    )
    session.execute(
        remember.on_conflict_do_update(
            constraint="uq_import_mappings_entity", set_={"mapping": mapping}
        )
    )
    return job


# --- Validate (the dry run) -------------------------------------------------------------------


def _mapped(job: ImportJob, raw: dict[str, str]) -> Row:
    return {name: raw.get(header, "") for name, header in job.mapping.items()}


def _context(session: Session, job: ImportJob) -> Context:
    options = dict(job.options)
    cutover = options.get("cutover_date")
    options["tenant_cutover"] = openings.tenant_cutover(session) or (
        date.fromisoformat(cutover) if cutover else None
    )
    return Context(session, options, job.source_system)


def _plan(
    session: Session, job: ImportJob
) -> tuple[list[tuple[ImportRow, Checked]], str, dict[str, Any]]:
    """What committing would do now: per row, with a digest of it all."""
    entity = entity_of(job.entity)
    context = _context(session, job)
    policy = job.options["conflict_policy"]
    seen: dict[str, int] = {}
    planned: list[tuple[ImportRow, Checked]] = []
    counts: dict[str, Any] = {"create": 0, "update": 0, "skip": 0, "error": 0, "committed": 0}
    total = ZERO
    for row in rows_of(session, job.id):
        if row.status == "committed":
            counts["committed"] += 1
            continue
        values = _mapped(job, row.raw)
        checked = entity.check(context, values)
        key = entity.key(values)
        if key.strip("|"):
            if key in seen:
                checked.errors.append(f"repeats row {seen[key]} of this file")
            seen.setdefault(key, row.row_no)
        action: str | None = "create"
        if checked.target_id is not None:
            if policy == "update" and entity.update:
                action = "update"
            elif policy == "reject":
                checked.errors.append("already exists")
            else:
                action = "skip"
        row.errors = checked.errors
        row.status = "error" if checked.errors else "valid"
        row.action = None if checked.errors else action
        row.target_id = checked.target_id
        counts["error" if checked.errors else str(action)] += 1
        if checked.item is not None and action == "create":
            total += checked.item.amount
        planned.append((row, checked))
    if entity.opening_domain:
        counts["total"] = format(total, "f")
    snapshot = [
        [
            r.row_no,
            r.action,
            r.target_id,
            r.errors,
            sorted((k, str(v)) for k, v in c.values.items()),
        ]
        for r, c in planned
    ]
    digest = hashlib.sha256(
        # JSONB does not keep key order, so the digest must not depend on it.
        json.dumps([job.mapping, job.options, snapshot], default=str, sort_keys=True).encode()
    ).hexdigest()
    return planned, digest, counts


def validate(session: Session, job: ImportJob) -> None:
    entity = entity_of(job.entity)
    missing = [c.name for c in entity.columns if c.required and c.name not in job.mapping]
    if missing:
        job.status = "needs_mapping"
        job.message = f"Choose a column for: {', '.join(missing)}"
        job.dry_run_digest = None
        return
    if entity.opening_domain and not job.options.get("cutover_date"):
        job.status = "needs_mapping"
        job.message = "Choose the cutover date."
        job.dry_run_digest = None
        return
    _, digest, counts = _plan(session, job)
    job.status = "dry_run_ready"
    job.dry_run_digest = digest
    job.counts = counts
    if job.message and job.message.startswith("Choose"):
        job.message = None
    session.flush()


# --- Approve and commit -------------------------------------------------------------------------


def approve(session: Session, job_id: int, digest: str, actor: CurrentUser) -> ImportJob:
    job = get_job(session, job_id, lock=True)
    if job.status != "dry_run_ready":
        raise ConflictError("There is no preview to approve.", code="not_ready")
    entity = entity_of(job.entity)
    if entity.opening_domain:
        if not actor.has("finance:opening"):
            raise BusinessRuleError(
                "Opening balances need the opening-balances permission.",
                code="permission_denied",
            )
        if job.counts.get("error"):
            raise ConflictError(
                "Opening balances load completely or not at all: fix the errors first.",
                code="rows_have_errors",
            )
    _, current, counts = _plan(session, job)
    if current != digest:
        job.dry_run_digest = current
        job.counts = counts
        session.commit()
        raise ConflictError(
            "The data changed since the preview. Look again, then approve.",
            code="preview_outdated",
        )
    job.status = "approved"
    job.approved_by_id = actor.id
    job.approved_at = now_utc()
    record_audit(
        session,
        actor_id=actor.id,
        action="approve",
        entity_type="import_job",
        entity_id=job.id,
        after={"entity": job.entity, "counts": counts},
    )
    session.flush()
    return job


def cancel(session: Session, job_id: int) -> None:
    job = get_job(session, job_id, lock=True)
    if job.status not in (*OPEN_STATUSES, "approved"):
        raise ConflictError("This import has already run.", code="not_open")
    job.status = "cancelled"


def _actor(job: ImportJob) -> CurrentUser:
    """The person who approved, for the audit trail of what the worker writes."""
    assert job.approved_by_id is not None
    return CurrentUser(
        id=job.approved_by_id, username="", full_name="", roles=(), permissions=frozenset()
    )


def _remember(context: Context, entity: str, row: ImportRow, values: Row, target: int) -> None:
    external = values.get("external_id")
    if not external:
        return
    digest = hashlib.sha256(json.dumps(row.raw, sort_keys=True).encode()).hexdigest()
    statement = insert(ExternalRef).values(
        source_system=context.source,
        entity=entity,
        external_id=external[:100],
        internal_id=target,
        source_hash=digest,
    )
    context.session.execute(
        statement.on_conflict_do_update(
            constraint="uq_external_refs_source",
            set_={"internal_id": target, "source_hash": digest, "last_synced_at": now_utc()},
        )
    )


def _apply_one(
    session: Session, job: ImportJob, row: ImportRow, checked: Checked, context: Context
) -> None:
    entity = entity_of(job.entity)
    actor = _actor(job)
    try:
        # A savepoint, so one refused row does not undo its batch.
        with session.begin_nested():
            target = checked.target_id
            if row.action == "create" and entity.create:
                target = entity.create(context, checked, actor)
            elif row.action == "update" and entity.update:
                entity.update(context, checked, actor)
            if target is not None:
                _remember(context, entity.name, row, _mapped(job, row.raw), target)
        row.status = "committed"
        row.target_id = target
    except (AppError, Invalid, ValueError) as exc:
        row.status = "error"
        row.errors = [getattr(exc, "detail", str(exc))[:300]]


Planned = list[tuple[ImportRow, Checked]]


def _commit_opening(session: Session, job: ImportJob, ready: Planned) -> None:
    """One load, one journal entry, in one transaction."""
    domain = entity_of(job.entity).opening_domain
    assert domain is not None
    load = openings.post_load(
        session,
        domain,
        [c.item for r, c in ready if r.action == "create" and c.item is not None],
        cutover=date.fromisoformat(job.options["cutover_date"]),
        actor=_actor(job),
        job_id=job.id,
    )
    for row, _ in ready:
        row.status = "committed"
        row.target_id = load.id


def _commit_orders(session: Session, job: ImportJob, ready: Planned, context: Context) -> None:
    """Several rows make one purchase order; an order is created whole or not at all."""
    groups: dict[str, Planned] = {}
    for row, checked in ready:
        groups.setdefault(checked.values["external_id"], []).append((row, checked))
    for external, lines in groups.items():
        if any(r.action == "skip" for r, _ in lines):
            for row, _ in lines:
                row.status = "committed"
            continue
        try:
            with session.begin_nested():
                target = create_order(context, [c for _, c in lines], _actor(job))
                _remember(context, job.entity, lines[0][0], {"external_id": external}, target)
            for row, _ in lines:
                row.status, row.target_id = "committed", target
        except (AppError, Invalid, ValueError) as exc:
            for row, _ in lines:
                row.status = "error"
                row.errors = [getattr(exc, "detail", str(exc))[:300]]


def run(session: Session, job_id: int) -> None:
    """The commit, called by the worker. Safe to run again after an interruption."""
    job = get_job(session, job_id, lock=True)
    if job.status not in ("approved", "committing", "partially_completed"):
        return
    entitlements.ensure_writable(session)
    entity = entity_of(job.entity)
    job.status = "committing"
    planned, _, _ = _plan(session, job)
    # Rows that were fine when approved but not now keep their new error.
    ready = [(r, c) for r, c in planned if r.status == "valid"]
    context = _context(session, job)

    if entity.opening_domain:
        if len(ready) != len(planned):
            job.status = "failed"
            job.message = "Some rows are no longer valid; nothing was loaded."
            session.commit()
            return
        _commit_opening(session, job, ready)
    elif entity.grouped:
        _commit_orders(session, job, ready, context)
    else:
        for first in range(0, len(ready), BATCH):
            for row, checked in ready[first : first + BATCH]:
                _apply_one(session, job, row, checked, context)
            session.commit()  # the rows' status goes with the records they created
            job = get_job(session, job_id, lock=True)

    session.flush()
    failed = session.scalar(
        select(func.count(ImportRow.id)).where(
            ImportRow.job_id == job.id, ImportRow.status == "error"
        )
    )
    job.status = "partially_completed" if failed else "completed"
    job.counts = {**job.counts, "error": failed or 0}
    record_audit(
        session,
        actor_id=job.approved_by_id,
        action="commit",
        entity_type="import_job",
        entity_id=job.id,
        after={"status": job.status},
    )


def error_report(session: Session, job_id: int) -> bytes:
    job = get_job(session, job_id)
    rows = [r for r in rows_of(session, job.id) if r.status == "error"]
    return parsing.to_csv(
        ["row", "error", *job.headers],
        [
            [str(r.row_no), "; ".join(r.errors), *(r.raw.get(h, "") for h in job.headers)]
            for r in rows
        ],
    )


def template(entity_name: str, lang: str) -> bytes:
    entity = entity_of(entity_name)
    return parsing.to_csv([c.bangla if lang == "bn" else c.name for c in entity.columns], [])


def purge_files(session: Session) -> None:
    cutoff = now_utc() - timedelta(days=KEEP_FILES_DAYS)
    session.execute(update(ImportFile).where(ImportFile.created_at < cutoff).values(content=None))
    finished = select(ImportJob.id).where(
        ImportJob.status.in_(("completed", "cancelled", "failed")), ImportJob.created_at < cutoff
    )
    session.execute(delete(ImportRow).where(ImportRow.job_id.in_(finished)))


# --- Cutover report and sign-off (§9) ------------------------------------------------------------


def cutover_report(session: Session) -> dict[str, Any]:
    checks = reconcile(session)
    return {
        "cutover_date": openings.tenant_cutover(session),
        "imported": {k: format(v, "f") for k, v in openings.imported_totals(session).items()},
        "reconcile_ok": checks.ok,
        "reconcile_issues": [i.detail for i in checks.issues],
        "signed_at": session.scalar(select(func.max(CutoverSignoff.signed_at))),
    }


def sign_off(
    session: Session, declared: dict[str, Any], reasons: dict[str, str], actor: CurrentUser
) -> None:
    """The dealer's own figures against what was loaded. Every difference needs a reason.
    Signing locks everything before the cutover date."""
    report = cutover_report(session)
    cutover = report["cutover_date"]
    if cutover is None:
        raise ConflictError("Nothing has been loaded yet.", code="no_cutover")
    if not report["reconcile_ok"]:
        raise ConflictError("The books do not reconcile; resolve that first.", code="reconcile")
    imported = openings.imported_totals(session)
    unexplained = [
        name
        for name, value in imported.items()
        if declared.get(name, ZERO) != value and not reasons.get(name, "").strip()
    ]
    if unexplained:
        raise BusinessRuleError(
            f"Explain the difference in: {', '.join(unexplained)}.",
            code="unexplained_difference",
            errors=[{"field": n, "message": "Differs"} for n in unexplained],
        )
    session.add(
        CutoverSignoff(
            cutover_date=cutover,
            report={
                "declared": {k: str(v) for k, v in declared.items()},
                "imported": report["imported"],
                "reasons": reasons,
            },
            created_by_id=actor.id,
        )
    )
    set_period_lock(session, PeriodLock(lock_date=cutover - timedelta(days=1)), actor)
    record_audit(
        session,
        actor_id=actor.id,
        action="sign_off",
        entity_type="cutover",
        entity_id=str(cutover),
        after={"reasons": reasons},
    )
