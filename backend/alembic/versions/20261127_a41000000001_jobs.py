"""background jobs and outbox

Revision ID: a41000000001
Revises: a31000000003
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a41000000001"
down_revision: str | Sequence[str] | None = "a31000000003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _time(name: str, *, now: bool = False, nullable: bool = True) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()") if now else None,
        nullable=nullable and not now,
    )


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(10), server_default="queued", nullable=False),
        _time("run_at", now=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("locked_by", sa.String(100), nullable=True),
        _time("locked_at"),
        sa.Column("dedupe_key", sa.String(150), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        _time("created_at", now=True),
        _time("finished_at"),
        sa.CheckConstraint(
            "status IN ('queued','running','done','failed')", name=op.f("ck_jobs_status_valid")
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_jobs_tenant_id_tenants"),
    )
    op.create_index("ix_jobs_status_run_at", "jobs", ["status", "run_at"])
    # One schedule slot is enqueued once, even by several workers.
    op.create_index("uq_jobs_dedupe_key", "jobs", ["dedupe_key"], unique=True)
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), nullable=True),
        sa.Column("topic", sa.String(50), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        _time("created_at", now=True),
        _time("dispatched_at"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_outbox_events_tenant_id_tenants"
        ),
    )
    op.create_index(
        "ix_outbox_events_pending",
        "outbox_events",
        ["id"],
        postgresql_where=sa.text("dispatched_at IS NULL"),
    )
    # A dealership's session adds and sees only its own; work with no dealership is added
    # without a context. The platform role claims from all.
    own = (
        "tenant_id IS NOT DISTINCT FROM (SELECT app_current_tenant()) "
        "OR (SELECT app_sees_all_tenants())"
    )
    for table in ("jobs", "outbox_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({own}) WITH CHECK ({own})")
    # A handler marks its own job done inside the transaction that does the work, so a worker
    # killed at any moment either did both or neither.
    op.execute(
        "CREATE FUNCTION app_complete_job(job bigint, worker text) RETURNS boolean "
        "LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS "
        "$$ WITH done AS (UPDATE public.jobs SET status = 'done', finished_at = now(), "
        "     locked_by = NULL, locked_at = NULL, last_error = NULL "
        "   WHERE id = job AND status = 'running' AND locked_by = worker RETURNING 1) "
        "   SELECT EXISTS (SELECT 1 FROM done) $$"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION app_complete_job(bigint, text)")
    op.drop_table("outbox_events")
    op.drop_table("jobs")
