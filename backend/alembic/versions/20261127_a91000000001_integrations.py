"""domain ownership, external snapshots, connections, sync runs, API keys

Revision ID: a91000000001
Revises: a81000000001
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a91000000001"
down_revision: str | Sequence[str] | None = "a81000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = postgresql.JSONB()
TZ = sa.DateTime(timezone=True)
TABLES = (
    "domain_ownership",
    "external_snapshots",
    "integration_connections",
    "sync_runs",
    "api_keys",
)


def _c(name: str, type_: object, null: bool = False, **kw: object) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, type_, nullable=null, **kw)  # type: ignore[arg-type]


def _now(name: str) -> sa.Column:  # type: ignore[type-arg]
    return _c(name, TZ, server_default=sa.text("now()"))


def _table(name: str, *items: object, creator: bool = False) -> None:
    extra: list[object] = []
    if creator:
        extra = [
            _c("created_by_id", sa.BigInteger(), True),
            sa.ForeignKeyConstraint(
                ["tenant_id", "created_by_id"],
                ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
                name=f"fk_{name}_created_by_id_users",
            ),
        ]
    op.create_table(
        name,
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        _c("tenant_id", sa.BigInteger(), server_default=sa.text("app_current_tenant()")),
        *items,  # type: ignore[arg-type]
        *extra,  # type: ignore[arg-type]
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=f"fk_tid_{name}"),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_tid_{name}"),
    )
    op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {name} "
        "USING (tenant_id = (SELECT app_current_tenant())) "
        "WITH CHECK (tenant_id = (SELECT app_current_tenant()))"
    )


def upgrade() -> None:
    _table(
        "domain_ownership",
        _c("domain", sa.String(15)),
        _c("owner", sa.String(12)),
        _now("changed_at"),
        sa.UniqueConstraint("tenant_id", "domain", name="uq_domain_ownership_domain"),
        sa.CheckConstraint(
            "owner IN ('external','tradesphere')",
            name=op.f("ck_domain_ownership_owner_valid"),
        ),
        creator=True,
    )
    _table(
        "external_snapshots",
        _c("kind", sa.String(10)),
        _c("source_system", sa.String(40)),
        _c("item_key", sa.String(200)),
        _c("code", sa.String(60)),
        _c("detail", sa.String(60)),
        _c("internal_id", sa.BigInteger(), True),
        _c("value", sa.Numeric(18, 3)),
        _c("as_of", TZ),
        _now("received_at"),
        sa.UniqueConstraint(
            "tenant_id", "kind", "source_system", "item_key", name="uq_external_snapshots_item"
        ),
        sa.CheckConstraint(
            "kind IN ('stock','balance')", name=op.f("ck_external_snapshots_kind_valid")
        ),
    )
    _table(
        "integration_connections",
        _c("name", sa.String(80)),
        _c("source_system", sa.String(40)),
        _c("feeds", JSON),
        _c("secret", sa.String(2000), True),
        _c("every_minutes", sa.Integer(), True),
        _c("enabled", sa.Boolean(), server_default=sa.text("true")),
        _c("state", JSON),
        _c("next_run_at", TZ, True),
        _c("last_attempt_at", TZ, True),
        _c("last_success_at", TZ, True),
        _c("failures", sa.Integer(), server_default="0"),
        _now("created_at"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_integration_connections_name"),
        creator=True,
    )
    _table(
        "sync_runs",
        _c("connection_id", sa.BigInteger()),
        _c("started_at", TZ),
        _c("finished_at", TZ),
        _c("status", sa.String(10)),
        _c("results", JSON),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            ["integration_connections.tenant_id", "integration_connections.id"],
            name="fk_sync_runs_connection_id_integration_connections",
        ),
    )
    op.create_index("ix_sync_runs_connection", "sync_runs", ["tenant_id", "connection_id", "id"])
    _table(
        "api_keys",
        _c("name", sa.String(80)),
        _c("source_system", sa.String(40)),
        _c("hint", sa.String(8)),
        _c("key_hash", sa.String(64)),
        _c("scopes", JSON),
        _c("window_start", TZ, True),
        _c("window_count", sa.Integer(), server_default="0"),
        _c("last_used_at", TZ, True),
        _c("revoked_at", TZ, True),
        _now("created_at"),
        sa.UniqueConstraint("tenant_id", "key_hash", name="uq_api_keys_hash"),
        creator=True,
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
