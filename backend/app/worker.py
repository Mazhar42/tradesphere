"""The job worker. One or many can run; each job is claimed by one of them.

`python -m app.cli worker` runs it as its own process. The single-process demo host runs the
same loop on a thread (`RUN_WORKER_IN_PROCESS`).

Queue bookkeeping uses the platform role. The work itself runs as the application role inside
the job's dealership, so row-level security applies to jobs exactly as to requests. A handler
and its job's completion commit together. Effects outside the database (email) happen at
least once: a worker killed between sending and committing sends again.
"""

import logging
import os
import socket
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from app.core.db import PlatformSessionLocal, SessionLocal
from app.core.tenant_context import set_context
from app.modules.billing import service as billing
from app.modules.billing.models import Subscription
from app.modules.integrity.reconcile import run_and_store
from app.modules.jobs import service as jobs
from app.modules.jobs.models import Job, OutboxEvent
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.service import business_config
from app.modules.users.models import RefreshToken, UserToken
from app.shared import email
from app.shared.business import business_scope, use_business
from app.shared.dates import now_utc
from app.shared.idempotency import IdempotencyKey

logger = logging.getLogger("app.worker")

LOCK_TIMEOUT = timedelta(minutes=5)  # a job running longer is taken to be abandoned
KEEP_IDEMPOTENCY_DAYS = 7
KEEP_FINISHED_DAYS = 14
KEEP_EXPIRED_TOKEN_DAYS = 30

Handler = Callable[[Session, dict[str, Any]], None]
SessionFactory = Callable[[], Session]


@dataclass(frozen=True)
class Registration:
    handler: Handler
    needs_tenant: bool
    platform: bool  # runs on the platform connection (platform tables only)


HANDLERS: dict[str, Registration] = {}


def handles(
    kind: str, *, needs_tenant: bool = False, platform: bool = False
) -> Callable[[Handler], Handler]:
    def register(function: Handler) -> Handler:
        HANDLERS[kind] = Registration(function, needs_tenant, platform)
        return function

    return register


@handles(jobs.EMAIL)
def _send_email(_: Session, payload: dict[str, Any]) -> None:
    email.deliver(jobs.open_email(payload))


@handles("purge.idempotency", needs_tenant=True)
def _purge_idempotency(session: Session, _: dict[str, Any]) -> None:
    before = now_utc() - timedelta(days=KEEP_IDEMPOTENCY_DAYS)
    session.execute(delete(IdempotencyKey).where(IdempotencyKey.created_at < before))


@handles("reconcile", needs_tenant=True)
def _reconcile(session: Session, _: dict[str, Any]) -> None:
    result = run_and_store(session)
    if not result.ok:
        logger.error("Reconcile found %d differences", len(result.issues))


@handles("purge.platform", platform=True)
def _purge_platform(session: Session, _: dict[str, Any]) -> None:
    now = now_utc()
    token_cutoff = now - timedelta(days=KEEP_EXPIRED_TOKEN_DAYS)
    done_cutoff = now - timedelta(days=KEEP_FINISHED_DAYS)
    expired = select(RefreshToken.id).where(RefreshToken.expires_at < token_cutoff)
    session.execute(
        update(RefreshToken)
        .where(RefreshToken.replaced_by_id.in_(expired))
        .values(replaced_by_id=None)
    )
    session.execute(delete(RefreshToken).where(RefreshToken.expires_at < token_cutoff))
    session.execute(delete(UserToken).where(UserToken.expires_at < token_cutoff))
    session.execute(delete(Job).where(Job.status == "done", Job.finished_at < done_cutoff))
    session.execute(delete(OutboxEvent).where(OutboxEvent.dispatched_at < done_cutoff))


@handles("billing.advance", platform=True)
def _advance_subscription(session: Session, payload: dict[str, Any]) -> None:
    billing.advance(session, int(payload["tenant_id"]))


class Worker:
    def __init__(
        self,
        platform: SessionFactory = PlatformSessionLocal,
        app: SessionFactory = SessionLocal,
        name: str | None = None,
    ) -> None:
        self.platform = platform
        self.app = app
        self.name = name or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"

    # --- one step of each kind -------------------------------------------------------------

    def dispatch_outbox(self) -> int:
        """Turn published events into jobs, atomically."""
        with self.platform() as session:
            events = session.scalars(
                select(OutboxEvent)
                .where(OutboxEvent.dispatched_at.is_(None))
                .order_by(OutboxEvent.id)
                .limit(100)
                .with_for_update(skip_locked=True)
            ).all()
            for event in events:
                jobs.enqueue(session, event.topic, event.payload, tenant_id=event.tenant_id)
                event.dispatched_at = now_utc()
            session.commit()
            return len(events)

    def schedule(self) -> None:
        """Once a day each, however many workers are running (the dedupe key sees to it)."""
        today = now_utc().date().isoformat()
        with self.platform() as session:
            jobs.enqueue(session, "purge.platform", dedupe_key=f"purge.platform:{today}")
            # Twice a day, so that a change of date in any timezone is noticed the same day.
            half = f"{today}:{now_utc().hour // 12}"
            for tenant_id in session.scalars(select(Subscription.tenant_id)):
                jobs.enqueue(
                    session,
                    "billing.advance",
                    {"tenant_id": tenant_id},
                    dedupe_key=f"billing.advance:{tenant_id}:{half}",
                )
            for tenant_id in session.scalars(select(Tenant.id).where(Tenant.status == "active")):
                jobs.enqueue(
                    session,
                    "reconcile",
                    tenant_id=tenant_id,
                    dedupe_key=f"reconcile:{tenant_id}:{today}",
                )
                jobs.enqueue(
                    session,
                    "purge.idempotency",
                    tenant_id=tenant_id,
                    dedupe_key=f"purge.idempotency:{tenant_id}:{today}",
                )
            session.commit()

    def reclaim_abandoned(self) -> None:
        """Jobs whose worker disappeared go back on the queue (or fail, if out of attempts)."""
        with self.platform() as session:
            stale = session.scalars(
                select(Job)
                .where(Job.status == "running", Job.locked_at < now_utc() - LOCK_TIMEOUT)
                .with_for_update(skip_locked=True)
            ).all()
            for job in stale:
                job.status = "queued" if job.attempts < job.max_attempts else "failed"
                job.locked_by = job.locked_at = None
                job.last_error = "The worker stopped before finishing."
            session.commit()

    def claim(self) -> tuple[int, str, int | None, dict[str, Any]] | None:
        with self.platform() as session:
            job = session.scalar(
                select(Job)
                .where(Job.status == "queued", Job.run_at <= now_utc())
                .order_by(Job.run_at, Job.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            job.status = "running"
            job.attempts += 1
            job.locked_by = self.name
            job.locked_at = now_utc()
            claimed = (job.id, job.kind, job.tenant_id, dict(job.payload))
            session.commit()
            return claimed

    def run(self, job_id: int, kind: str, tenant_id: int | None, payload: dict[str, Any]) -> None:
        registration = HANDLERS.get(kind)
        try:
            if registration is None:
                raise LookupError(f"No handler for '{kind}'.")
            if registration.needs_tenant and tenant_id is None:
                raise PermissionError(f"'{kind}' needs a dealership and has none.")
            factory = self.platform if registration.platform else self.app
            with business_scope(), factory() as session:
                if tenant_id is not None and not registration.platform:
                    set_context(session, tenant_id=tenant_id)
                    tenant = session.get(Tenant, tenant_id)
                    if tenant is None:
                        raise LookupError("The dealership does not exist.")
                    use_business(business_config(tenant))
                registration.handler(session, payload)
                completed = session.scalar(
                    text("SELECT app_complete_job(:job, :worker)")
                    if not registration.platform
                    else text(
                        "UPDATE jobs SET status = 'done', finished_at = now(), locked_by = NULL,"
                        " locked_at = NULL, last_error = NULL WHERE id = :job AND"
                        " status = 'running' AND locked_by = :worker RETURNING true"
                    ),
                    {"job": job_id, "worker": self.name},
                )
                if not completed:
                    session.rollback()  # another worker took it over: discard this attempt
                    return
                session.commit()
        except Exception as exc:
            logger.exception("Job %s (%s) failed", job_id, kind)
            self._record_failure(job_id, exc)

    def _record_failure(self, job_id: int, exc: Exception) -> None:
        with self.platform() as session:
            job = session.get(Job, job_id, with_for_update=True)
            if job is None or job.locked_by != self.name:
                return
            final = job.attempts >= job.max_attempts or isinstance(
                exc, LookupError | PermissionError
            )
            job.status = "failed" if final else "queued"
            job.run_at = now_utc() + timedelta(seconds=30 * 2**job.attempts)
            job.locked_by = job.locked_at = None
            # The type only: messages can quote data.
            job.last_error = type(exc).__name__
            if final:
                job.finished_at = now_utc()
            session.commit()

    # --- the loop --------------------------------------------------------------------------

    def run_pending(self, limit: int = 1000) -> int:
        """Do everything due now. Returns how many jobs ran."""
        self.dispatch_outbox()
        done = 0
        while done < limit and (claimed := self.claim()) is not None:
            self.run(*claimed)
            done += 1
        return done

    def forever(self, stop: threading.Event, *, idle_seconds: float = 2.0) -> None:
        logger.info("Worker %s started", self.name)
        last_housekeeping = None
        while not stop.is_set():
            try:
                minute = now_utc().replace(second=0, microsecond=0)
                if minute != last_housekeeping:
                    self.reclaim_abandoned()
                    self.schedule()
                    last_housekeeping = minute
                if self.run_pending(limit=50) == 0:
                    stop.wait(idle_seconds)
            except Exception:
                logger.exception("Worker loop failed; retrying")
                stop.wait(10)
        logger.info("Worker %s stopped", self.name)
