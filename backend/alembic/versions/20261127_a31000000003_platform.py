"""platform operators, platform audit, support access grants

Revision ID: a31000000003
Revises: a31000000002
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a31000000003"
down_revision: str | Sequence[str] | None = "a31000000002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _at(name: str, default: bool = True) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()") if default else None,
        nullable=not default,
    )


def upgrade() -> None:
    op.create_table(
        "platform_operators",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, primary_key=True),
        sa.Column("status", sa.String(10), server_default="active", nullable=False),
        _at("created_at"),
        sa.CheckConstraint(
            "status IN ('active','inactive')", name=op.f("ck_platform_operators_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_platform_operators_user_id_users"
        ),
    )
    op.create_table(
        "platform_audit_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        _at("occurred_at"),
        sa.Column("operator_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["operator_id"], ["users.id"], name="fk_platform_audit_logs_operator_id_users"
        ),
    )
    op.create_index("ix_platform_audit_logs_occurred_at", "platform_audit_logs", ["occurred_at"])
    op.execute(
        "CREATE TRIGGER platform_audit_logs_append_only BEFORE UPDATE OR DELETE "
        "ON platform_audit_logs FOR EACH ROW EXECUTE FUNCTION forbid_modification()"
    )

    op.create_table(
        "support_access_grants",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.BigInteger(),
            server_default=sa.text("app_current_tenant()"),
            nullable=False,
        ),
        sa.Column("operator_id", sa.BigInteger(), nullable=False),
        sa.Column("approved_by_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        _at("created_at"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_tid_support_access_grants"),
        sa.ForeignKeyConstraint(
            ["operator_id"], ["users.id"], name="fk_support_access_grants_operator_id_users"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approved_by_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_support_access_grants_approved_by_id_users",
        ),
    )
    op.execute("ALTER TABLE support_access_grants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE support_access_grants FORCE ROW LEVEL SECURITY")
    # The dealership sees its own grants; the platform sees all, to use the ones given to it.
    op.execute(
        "CREATE POLICY tenant_isolation ON support_access_grants "
        "USING (tenant_id = (SELECT app_current_tenant()) OR (SELECT app_sees_all_tenants())) "
        "WITH CHECK (tenant_id = (SELECT app_current_tenant()))"
    )
    # Whether a person is an operator, without opening the operators table to the application.
    op.execute(
        "CREATE FUNCTION app_operator_id(name text) RETURNS bigint LANGUAGE sql STABLE "
        "SECURITY DEFINER SET search_path = pg_catalog, public AS "
        "$$ SELECT o.user_id FROM public.platform_operators o "
        "   JOIN public.users u ON u.id = o.user_id "
        "   WHERE lower(u.username) = lower(name) AND o.status = 'active' AND u.is_active $$"
    )
    op.execute(
        "CREATE FUNCTION app_is_operator(person bigint) RETURNS boolean LANGUAGE sql STABLE "
        "SECURITY DEFINER SET search_path = pg_catalog, public AS "
        "$$ SELECT EXISTS (SELECT 1 FROM public.platform_operators o "
        "   JOIN public.users u ON u.id = o.user_id "
        "   WHERE o.user_id = person AND o.status = 'active' AND u.is_active) $$"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION app_is_operator(bigint)")
    op.execute("DROP FUNCTION app_operator_id(text)")
    op.drop_table("support_access_grants")
    op.drop_table("platform_audit_logs")
    op.drop_table("platform_operators")
