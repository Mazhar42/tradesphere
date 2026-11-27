"""import staging, external references, per-domain opening loads, history

Revision ID: a71000000001
Revises: a61000000001
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a71000000001"
down_revision: str | Sequence[str] | None = "a61000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = postgresql.JSONB()
MONEY = sa.Numeric(18, 2)


def _c(name: str, type_: sa.types.TypeEngine, null: bool = False, **kw: object) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, type_, nullable=null, **kw)  # type: ignore[arg-type]


def _at(name: str, default: bool = True) -> sa.Column:  # type: ignore[type-arg]
    return _c(
        name,
        sa.DateTime(timezone=True),
        not default,
        server_default=sa.text("now()") if default else None,
    )


def _table(name: str, *items: object, creator: bool = False) -> None:
    """A dealership table: tenant column, composite key target, forced row-level security."""
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


def _ref(table: str, column: str, parent: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{parent}.tenant_id", f"{parent}.id"],
        name=f"fk_{table}_{column}_{parent}",
    )


def upgrade() -> None:
    _table(
        "import_files",
        _c("original_name", sa.String(200)),
        _c("size", sa.Integer()),
        _c("sha256", sa.String(64)),
        _c("content", sa.LargeBinary(), True),
        _at("created_at"),
        creator=True,
    )
    _table(
        "import_jobs",
        _c("entity", sa.String(30)),
        _c("source_system", sa.String(40)),
        _c("file_id", sa.BigInteger(), True),
        _c("status", sa.String(20)),
        _c("headers", JSON),
        _c("mapping", JSON),
        _c("options", JSON),
        _c("counts", JSON),
        _c("dry_run_digest", sa.String(64), True),
        _c("message", sa.String(300), True),
        _c("approved_by_id", sa.BigInteger(), True),
        _at("approved_at", default=False),
        _at("created_at"),
        _ref("import_jobs", "file_id", "import_files"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approved_by_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_import_jobs_approved_by_id_users",
        ),
        creator=True,
    )
    op.create_index("ix_import_jobs_tenant_created", "import_jobs", ["tenant_id", "created_at"])
    _table(
        "import_rows",
        _c("job_id", sa.BigInteger()),
        _c("row_no", sa.Integer()),
        _c("raw", JSON),
        _c("status", sa.String(10)),
        _c("errors", JSON),
        _c("action", sa.String(10), True),
        _c("target_id", sa.BigInteger(), True),
        _ref("import_rows", "job_id", "import_jobs"),
    )
    op.create_index("ix_import_rows_job", "import_rows", ["job_id", "row_no"])
    _table(
        "import_mappings",
        _c("entity", sa.String(30)),
        _c("source_system", sa.String(40)),
        _c("mapping", JSON),
        sa.UniqueConstraint(
            "tenant_id", "entity", "source_system", name="uq_import_mappings_entity"
        ),
    )
    _table(
        "external_refs",
        _c("source_system", sa.String(40)),
        _c("entity", sa.String(30)),
        _c("external_id", sa.String(100)),
        _c("internal_id", sa.BigInteger()),
        _c("source_hash", sa.String(64)),
        _at("last_synced_at"),
        sa.UniqueConstraint(
            "tenant_id", "source_system", "entity", "external_id", name="uq_external_refs_source"
        ),
    )
    _table(
        "opening_loads",
        _c("domain", sa.String(15)),
        _c("doc_no", sa.String(30)),
        _c("business_date", sa.Date()),
        _c("total", MONEY),
        _c("status", sa.String(10), server_default="posted"),
        _c("job_id", sa.BigInteger(), True),
        _at("reversed_at", default=False),
        _at("created_at"),
        _ref("opening_loads", "job_id", "import_jobs"),
        sa.UniqueConstraint("tenant_id", "doc_no", name="uq_opening_loads_doc_no"),
        sa.CheckConstraint(
            "status IN ('posted','reversed')", name=op.f("ck_opening_loads_status_valid")
        ),
        creator=True,
    )
    _table(
        "opening_lines",
        _c("load_id", sa.BigInteger()),
        _c("domain", sa.String(15)),
        _c("item_key", sa.String(200)),
        _c("party_id", sa.BigInteger(), True),
        _c("amount", MONEY),
        _c("active", sa.Boolean(), server_default="true"),
        _ref("opening_lines", "load_id", "opening_loads"),
    )
    op.create_index(
        "uq_opening_lines_active",
        "opening_lines",
        ["tenant_id", "domain", "item_key"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_index("ix_opening_lines_load", "opening_lines", ["load_id"])
    _table(
        "imported_documents",
        _c("source_system", sa.String(40)),
        _c("external_id", sa.String(100)),
        _c("doc_type", sa.String(20)),
        _c("doc_date", sa.Date()),
        _c("number", sa.String(60), True),
        _c("party", sa.String(150), True),
        _c("amount", MONEY),
        _c("note", sa.String(300), True),
        sa.UniqueConstraint(
            "tenant_id", "source_system", "external_id", name="uq_imported_documents_source"
        ),
    )
    op.create_index(
        "ix_imported_documents_date", "imported_documents", ["tenant_id", "doc_date", "id"]
    )
    _table(
        "cutover_signoffs",
        _c("cutover_date", sa.Date()),
        _c("report", JSON),
        _at("signed_at"),
        creator=True,
    )


def downgrade() -> None:
    for table in (
        "cutover_signoffs",
        "imported_documents",
        "opening_lines",
        "opening_loads",
        "external_refs",
        "import_mappings",
        "import_rows",
        "import_jobs",
        "import_files",
    ):
        op.drop_table(table)
