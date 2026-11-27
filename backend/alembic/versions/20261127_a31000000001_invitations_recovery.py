"""email, one-time tokens and invitations

Revision ID: a31000000001
Revises: a21000000005
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a31000000001"
down_revision: str | Sequence[str] | None = "a21000000005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _now() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(150), nullable=True))
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True)))
    op.create_index("uq_users_email", "users", [sa.text("lower(email::text)")], unique=True)

    op.create_table(
        "user_tokens",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("email", sa.String(150), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        _now(),
        sa.CheckConstraint(
            "purpose IN ('verify_email','reset_password')",
            name=op.f("ck_user_tokens_purpose_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_tokens_user_id_users"), ondelete="CASCADE"
        ),
        sa.UniqueConstraint("token_hash", name=op.f("uq_user_tokens_token_hash")),
    )
    op.create_index("ix_user_tokens_user_id", "user_tokens", ["user_id"])

    op.create_table(
        "tenant_invitations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.BigInteger(),
            server_default=sa.text("app_current_tenant()"),
            nullable=False,
        ),
        sa.Column("email", sa.String(150), nullable=True),
        sa.Column("note", sa.String(120), nullable=True),
        sa.Column("roles", postgresql.JSONB(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_id", sa.BigInteger(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        _now(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_tid_tenant_invitations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_tenant_invitations_created_by_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "accepted_by_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_tenant_invitations_accepted_by_id_users",
        ),
        sa.UniqueConstraint("tenant_id", "token_hash", name="uq_tenant_invitations_token_hash"),
    )
    op.execute("ALTER TABLE tenant_invitations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_invitations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON tenant_invitations "
        "USING (tenant_id = (SELECT app_current_tenant())) "
        "WITH CHECK (tenant_id = (SELECT app_current_tenant()))"
    )
    op.execute(
        "CREATE FUNCTION app_membership_count(person bigint) RETURNS bigint "
        "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS "
        "$$ SELECT count(*) FROM public.tenant_memberships WHERE user_id = person $$"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION app_membership_count(bigint)")
    op.drop_table("tenant_invitations")
    op.drop_table("user_tokens")
    op.drop_index("uq_users_email", "users")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "email")
