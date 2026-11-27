"""tenancy tables: tenants, memberships, sign-in events

Revision ID: a21000000001
Revises: f93ec8729359
Create Date: 2026-11-27

A database that already holds a business gets one default tenant, with every user as a member.
An empty database gets none: tenants are then created by `app.cli provision-tenant`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.config import settings

revision: str = "a21000000001"
down_revision: str | Sequence[str] | None = "f93ec8729359"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("slug", sa.String(50), nullable=False),
        sa.Column("legal_name", sa.String(150), nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column("timezone", sa.String(50), nullable=False),
        sa.Column("currency_code", sa.String(3), server_default="BDT", nullable=False),
        sa.Column("fiscal_year_start_month", sa.Integer(), nullable=False),
        sa.Column("cutover_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending','active','suspended','deleted')",
            name=op.f("ck_tenants_status_valid"),
        ),
        sa.CheckConstraint(
            "fiscal_year_start_month BETWEEN 1 AND 12", name=op.f("ck_tenants_fy_month_valid")
        ),
        sa.UniqueConstraint("slug", name=op.f("uq_tenants_slug")),
    )
    op.create_table(
        "tenant_memberships",
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(10), server_default="active", nullable=False),
        sa.Column("managed_by_tenant", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("invited_by_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('active','inactive')", name=op.f("ck_tenant_memberships_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_tenant_memberships_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_tenant_memberships_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_id"], ["users.id"], name=op.f("fk_tenant_memberships_invited_by_id_users")
        ),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", name=op.f("pk_tenant_memberships")),
    )
    op.create_index("ix_tenant_memberships_user_id", "tenant_memberships", ["user_id"])
    op.create_table(
        "auth_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("identifier", sa.String(150), nullable=True),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_auth_events_user_id_users")
        ),
    )
    op.create_index(
        "ix_auth_events_identifier_occurred", "auth_events", ["identifier", "occurred_at"]
    )
    op.create_index("ix_auth_events_ip_occurred", "auth_events", ["ip", "occurred_at"])
    op.execute(
        "CREATE TRIGGER auth_events_append_only BEFORE UPDATE OR DELETE ON auth_events "
        "FOR EACH ROW EXECUTE FUNCTION forbid_modification()"
    )

    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM accounts)")):
        bind.execute(
            sa.text(
                "INSERT INTO tenants (slug, legal_name, timezone, fiscal_year_start_month) "
                "VALUES ('default', COALESCE("
                "  (SELECT value->>'name' FROM app_settings WHERE key = 'business_profile'),"
                "  'My Dealership'), :tz, :fy)"
            ),
            {"tz": settings.business_timezone, "fy": settings.fiscal_year_start_month},
        )
        bind.execute(
            sa.text(
                "INSERT INTO tenant_memberships (tenant_id, user_id, status, managed_by_tenant) "
                "SELECT t.id, u.id, CASE WHEN u.is_active THEN 'active' ELSE 'inactive' END, true "
                "FROM users u CROSS JOIN tenants t"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if (bind.scalar(sa.text("SELECT count(*) FROM tenants")) or 0) > 1:
        raise RuntimeError(
            "More than one dealership exists; downgrading would merge their books. "
            "Restore a backup instead."
        )
    op.drop_table("auth_events")
    op.drop_table("tenant_memberships")
    op.drop_table("tenants")
