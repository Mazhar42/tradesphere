"""The queue: one worker per job, nothing lost when one dies, no effect twice, and tenant jobs
run inside their dealership. Committed data and real connections, in a throwaway database."""

import threading
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import worker as worker_module
from app.core.config import settings
from app.core.tenant_context import set_context
from app.modules.catalog.models import Brand
from app.modules.jobs import service as jobs
from app.modules.jobs.models import Job
from app.modules.users.schemas import UserCreate
from app.modules.users.service import create_user
from app.shared import email
from app.shared.dates import now_utc
from app.shared.idempotency import IdempotencyKey
from app.worker import Registration, Worker
from tests.conftest import DEFAULT_PASSWORD, TEST_TENANT, Clock, Databases


class Queue:
    def __init__(self, databases: Databases) -> None:
        self.databases = databases

    def app(self) -> Session:
        return Session(self.databases.app, autoflush=False, expire_on_commit=False)

    def platform(self) -> Session:
        return Session(self.databases.platform, autoflush=False, expire_on_commit=False)

    def worker(self, name: str = "w1") -> Worker:
        return Worker(platform=self.platform, app=self.app, name=name)

    def add(self, kind: str, tenant: int | None = TEST_TENANT, **payload: Any) -> None:
        with self.app() as session:
            if tenant is not None:
                set_context(session, tenant_id=tenant)
            jobs.enqueue(session, kind, payload, tenant_id=tenant)
            session.commit()

    def jobs(self) -> list[Job]:
        with self.platform() as session:
            return list(session.scalars(select(Job).order_by(Job.id)))

    def brands(self) -> list[str]:
        with self.app() as session:
            set_context(session, tenant_id=TEST_TENANT)
            return [b.name_en for b in session.scalars(select(Brand).order_by(Brand.id))]


@pytest.fixture
def queue(scratch: Databases) -> Queue:
    return Queue(scratch)


@pytest.fixture
def handler(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`test.brand` adds a brand; with `fail` it adds one and then raises."""

    def add_brand(session: Session, payload: dict[str, Any]) -> None:
        session.add(Brand(name_en=payload["name"]))
        session.flush()
        if payload.get("fail"):
            raise RuntimeError("secret detail that must not be stored")

    monkeypatch.setitem(worker_module.HANDLERS, "test.brand", Registration(add_brand, True, False))
    yield


def test_two_workers_never_run_the_same_job(queue: Queue, handler: None) -> None:
    for n in range(30):
        queue.add("test.brand", name=f"b{n:02}")
    threads = [
        threading.Thread(target=queue.worker(name).run_pending) for name in ("w1", "w2", "w3")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert sorted(queue.brands()) == [f"b{n:02}" for n in range(30)]
    assert {j.status for j in queue.jobs()} == {"done"}
    assert {j.attempts for j in queue.jobs()} == {1}


def test_a_failure_leaves_no_effect_and_is_retried_then_gives_up(
    queue: Queue, handler: None, clock: Clock
) -> None:
    queue.add("test.brand", name="x", fail=True)
    for _ in range(6):
        queue.worker().run_pending()
        clock.advance(hours=1)

    [job] = queue.jobs()
    assert (job.status, job.attempts, job.last_error) == ("failed", 5, "RuntimeError")
    assert queue.brands() == []


def test_a_job_abandoned_by_a_dead_worker_is_done_exactly_once(
    queue: Queue, handler: None, clock: Clock
) -> None:
    queue.add("test.brand", name="once")
    dead = queue.worker("dead")
    claimed = dead.claim()  # then the process is killed
    assert claimed is not None
    clock.set(now_utc() + timedelta(minutes=10))
    other = queue.worker("alive")
    other.reclaim_abandoned()
    other.run_pending()

    dead.run(*claimed)  # the "dead" worker wakes up late: its work must be discarded

    assert queue.brands() == ["once"]
    assert [j.status for j in queue.jobs()] == ["done"]


def test_a_job_that_needs_a_dealership_fails_without_one(queue: Queue, handler: None) -> None:
    queue.add("test.brand", tenant=None, name="orphan")

    queue.worker().run_pending()

    [job] = queue.jobs()
    assert (job.status, job.last_error) == ("failed", "PermissionError")


def test_the_application_cannot_queue_work_for_another_dealership(queue: Queue) -> None:
    with queue.app() as session:
        set_context(session, tenant_id=TEST_TENANT)
        with pytest.raises(Exception, match="row-level security"):
            jobs.enqueue(session, "test.brand", tenant_id=TEST_TENANT + 1)


def test_email_is_stored_sealed_and_sent_after_commit(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[email.Message] = []
    monkeypatch.setattr(settings, "email_backend", "log")
    monkeypatch.setattr(email, "deliver", sent.append)
    message = email.Message("a@example.com", "Hello", "https://x/#token=SECRETVALUE")
    with queue.app() as session:
        jobs.queue_email(session, message)
        session.commit()
    with queue.platform() as session:
        stored = session.scalar(text("SELECT payload::text FROM outbox_events"))
    assert "SECRETVALUE" not in str(stored) and "a@example.com" not in str(stored)

    queue.worker().run_pending()

    assert sent == [message]
    assert [j.status for j in queue.jobs()] == ["done"]


def test_daily_housekeeping_is_queued_once_and_purges_old_keys(queue: Queue) -> None:
    with queue.app() as session:
        set_context(session, tenant_id=TEST_TENANT)
        user = create_user(
            session,
            UserCreate(
                username="keeper", full_name="Keeper", password=DEFAULT_PASSWORD, roles=["owner"]
            ),
            actor=None,
        )
        for name, age in (("old-key-0001", 30), ("new-key-0001", 1)):
            session.add(
                IdempotencyKey(
                    user_id=user.id,
                    key=name,
                    endpoint="x",
                    request_hash="x",
                    created_at=now_utc() - timedelta(days=age),
                )
            )
        session.commit()
    first, second = queue.worker("w1"), queue.worker("w2")
    first.schedule()
    second.schedule()
    assert sorted(j.kind for j in queue.jobs()) == ["purge.idempotency", "purge.platform"]

    first.run_pending()

    assert {j.status for j in queue.jobs()} == {"done"}
    with queue.app() as session:
        set_context(session, tenant_id=TEST_TENANT)
        assert [k.key for k in session.scalars(select(IdempotencyKey))] == ["new-key-0001"]
