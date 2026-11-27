"""row-level security: the database shows a session only its own dealership

Revision ID: a21000000005
Revises: a21000000004
Create Date: 2026-11-27

Forced, so that the table owner is restricted too. Fails closed: with no context nothing is
visible and nothing can be written. Later data migrations must loop over dealerships and set
the context, or they will silently change nothing.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a21000000005"
down_revision: str | Sequence[str] | None = "a21000000004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tenant_tables() -> list[str]:
    return list(
        op.get_bind().scalars(
            sa.text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'tenant_id' "
                "AND table_name <> 'tenant_memberships' ORDER BY 1"
            )
        )
    )


def _protect(table: str, using: str, check: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({using}) WITH CHECK ({check})")


def upgrade() -> None:
    # The platform role, and whoever owns the tables, may list every dealership and
    # membership. This concerns only those two tables, never business data.
    op.execute(
        "CREATE FUNCTION app_sees_all_tenants() RETURNS boolean LANGUAGE sql STABLE AS "
        "$$ SELECT current_user = 'tradesphere_platform' OR current_user = ("
        "  SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'public.tenants'::regclass) $$"
    )
    own = "tenant_id = (SELECT app_current_tenant())"
    for table in _tenant_tables():
        _protect(table, own, own)

    everyone = "(SELECT app_sees_all_tenants())"
    _protect(
        "tenant_memberships",
        f"{own} OR user_id = (SELECT app_current_user()) OR {everyone}",
        f"{own} OR {everyone}",
    )
    _protect(
        "tenants",
        "id = (SELECT app_current_tenant()) OR EXISTS ("
        "  SELECT 1 FROM tenant_memberships m "
        "  WHERE m.tenant_id = tenants.id AND m.user_id = (SELECT app_current_user())"
        f") OR {everyone}",
        f"id = (SELECT app_current_tenant()) OR {everyone}",
    )
    # How many dealerships a person belongs to, without revealing which.
    op.execute(
        "CREATE FUNCTION app_active_membership_count(person bigint) RETURNS bigint "
        "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS "
        "$$ SELECT count(*) FROM public.tenant_memberships "
        "   WHERE user_id = person AND status = 'active' $$"
    )


def downgrade() -> None:
    if (op.get_bind().scalar(sa.text("SELECT count(*) FROM tenants")) or 0) > 1:
        raise RuntimeError(
            "More than one dealership exists; removing row-level security would expose each to "
            "the others. Restore a backup instead."
        )
    op.execute("DROP FUNCTION app_active_membership_count(bigint)")
    for table in ["tenants", "tenant_memberships", *_tenant_tables()]:
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION app_sees_all_tenants()")
