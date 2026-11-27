"""reversal status on every posted document; stored reconciliation results

Revision ID: a61000000001
Revises: a51000000001
Create Date: 2026-11-27

Existing rows become 'posted'. No amount, number or journal line changes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a61000000001"
down_revision: str | Sequence[str] | None = "a51000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Documents that were posted the moment they were created.
IMMEDIATE = (
    "expenses",
    "fund_transfers",
    "owner_transactions",
    "commission_payments",
    "claim_settlements",
)
RETURNS = ("sales_returns", "purchase_returns")


def upgrade() -> None:
    for table in IMMEDIATE:
        # A constant default fills existing rows without updating them.
        op.add_column(
            table, sa.Column("status", sa.String(10), server_default="posted", nullable=False)
        )
        op.add_column(table, sa.Column("reversed_at", sa.DateTime(timezone=True)))
        op.create_check_constraint(
            op.f(f"ck_{table}_status_valid"), table, "status IN ('posted','reversed')"
        )
    for table in RETURNS:
        op.add_column(table, sa.Column("reversed_at", sa.DateTime(timezone=True)))
        op.drop_constraint(op.f(f"ck_{table}_status_valid"), table)
        op.create_check_constraint(
            op.f(f"ck_{table}_status_valid"), table, "status IN ('draft','posted','reversed')"
        )

    op.create_table(
        "reconcile_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.BigInteger(),
            server_default=sa.text("app_current_tenant()"),
            nullable=False,
        ),
        sa.Column(
            "ran_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("issues", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_tid_reconcile_runs"),
    )
    op.create_index("ix_reconcile_runs_tenant_ran", "reconcile_runs", ["tenant_id", "ran_at"])
    op.execute("ALTER TABLE reconcile_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE reconcile_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON reconcile_runs "
        "USING (tenant_id = (SELECT app_current_tenant())) "
        "WITH CHECK (tenant_id = (SELECT app_current_tenant()))"
    )


def downgrade() -> None:
    # Refused if anything was reversed: the old code would count it as live.
    bind = op.get_bind()
    for table in (*IMMEDIATE, *RETURNS):
        # Row-level security hides rows from the owner, so ask each dealership in turn.
        for tenant in bind.scalars(sa.text("SELECT id FROM tenants")).all():
            bind.execute(
                sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)}
            )
            if bind.scalar(
                sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE status = 'reversed')")
            ):
                raise RuntimeError(
                    f"{table} holds reversed documents; downgrading would revive them. "
                    "Restore a backup instead."
                )
    op.drop_table("reconcile_runs")
    for table in RETURNS:
        op.drop_constraint(op.f(f"ck_{table}_status_valid"), table)
        op.create_check_constraint(
            op.f(f"ck_{table}_status_valid"), table, "status IN ('draft','posted')"
        )
        op.drop_column(table, "reversed_at")
    for table in IMMEDIATE:
        op.drop_constraint(op.f(f"ck_{table}_status_valid"), table)
        op.drop_column(table, "reversed_at")
        op.drop_column(table, "status")
