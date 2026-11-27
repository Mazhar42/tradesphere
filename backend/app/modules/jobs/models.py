from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, IdMixin


class Job(IdMixin, Base):
    __tablename__ = "jobs"

    tenant_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("tenants.id"))
    kind: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(10), server_default="queued")
    run_at: Mapped[datetime] = mapped_column(server_default=func.now())
    attempts: Mapped[int] = mapped_column(server_default="0")
    max_attempts: Mapped[int] = mapped_column(server_default="5")
    locked_by: Mapped[str | None] = mapped_column(String(100))
    locked_at: Mapped[datetime | None]
    dedupe_key: Mapped[str | None] = mapped_column(String(150))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint("status IN ('queued','running','done','failed')", name="status_valid"),
        Index("ix_jobs_status_run_at", "status", "run_at"),
        Index("uq_jobs_dedupe_key", "dedupe_key", unique=True),
    )


class OutboxEvent(IdMixin, Base):
    """Something that happened in a business transaction and must trigger work."""

    __tablename__ = "outbox_events"

    tenant_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("tenants.id"))
    topic: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    dispatched_at: Mapped[datetime | None]

    __table_args__ = (
        Index("ix_outbox_events_pending", "id", postgresql_where=text("dispatched_at IS NULL")),
    )
