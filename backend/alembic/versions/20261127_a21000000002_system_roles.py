"""mark the roles the application maintains

Revision ID: a21000000002
Revises: a21000000001
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a21000000002"
down_revision: str | Sequence[str] | None = "a21000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Answers "is there any dealership" even once row-level security hides other people's.
    op.execute(
        "CREATE FUNCTION app_has_tenants() RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER "
        "SET search_path = pg_catalog, public AS "
        "$$ SELECT EXISTS (SELECT 1 FROM public.tenants) $$"
    )
    op.add_column(
        "roles", sa.Column("is_system", sa.Boolean(), server_default="false", nullable=False)
    )
    op.execute(
        "UPDATE roles SET is_system = true "
        "WHERE code IN ('owner','manager','cashier','storekeeper')"
    )


def downgrade() -> None:
    op.drop_column("roles", "is_system")
    op.execute("DROP FUNCTION app_has_tenants()")
