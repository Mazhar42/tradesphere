"""composite foreign keys, per-dealership uniqueness and indexes

Revision ID: a21000000004
Revises: a21000000003
Create Date: 2026-11-27

PostgreSQL checks foreign keys without applying row-level security, so a single-column key
would accept a reference to another dealership's row. Every key between business tables
therefore includes `tenant_id`; keys naming a person point at the dealership's membership.
Constraint and index names are kept, so error handling and downgrades stay simple.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a21000000004"
down_revision: str | Sequence[str] | None = "a21000000003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Plain unique constraints: name, table, column.
UNIQUES = (
    ("uq_accounts_code", "accounts", "code"),
    ("uq_locations_code", "locations", "code"),
    ("uq_roles_code", "roles", "code"),
    ("uq_uoms_code", "uoms", "code"),
    ("uq_customers_code", "customers", "code"),
    ("uq_suppliers_code", "suppliers", "code"),
    ("uq_products_sku", "products", "sku"),
    ("uq_journal_entries_entry_no", "journal_entries", "entry_no"),
    ("uq_opening_balances_doc_no", "opening_balances", "doc_no"),
    ("uq_supplier_payments_doc_no", "supplier_payments", "doc_no"),
    ("uq_customer_receipts_doc_no", "customer_receipts", "doc_no"),
    ("uq_claim_settlements_doc_no", "claim_settlements", "doc_no"),
    ("uq_commission_payments_doc_no", "commission_payments", "doc_no"),
    ("uq_expenses_doc_no", "expenses", "doc_no"),
    ("uq_fund_transfers_doc_no", "fund_transfers", "doc_no"),
    ("uq_owner_transactions_doc_no", "owner_transactions", "doc_no"),
)

DOC_NO_PARTIAL = (
    "company_claims",
    "purchase_orders",
    "goods_receipts",
    "purchase_returns",
    "sales_invoices",
    "sales_returns",
    "stock_adjustments",
    "stock_transfers",
)

# Indexes: name, table, new columns, old columns, unique, where.
INDEXES: tuple[tuple[str, str, str, str, bool, str | None], ...] = (
    *(
        (
            f"uq_{table}_doc_no",
            table,
            "tenant_id, doc_no",
            "doc_no",
            True,
            "doc_no IS NOT NULL",
        )
        for table in DOC_NO_PARTIAL
    ),
    ("uq_products_barcode", "products", "tenant_id, barcode", "barcode", True,
     "barcode IS NOT NULL"),
    ("uq_brands_name_en", "brands", "tenant_id, lower(name_en::text)", "lower(name_en::text)",
     True, None),
    ("uq_categories_name_en", "categories", "tenant_id, lower(name_en::text)",
     "lower(name_en::text)", True, None),
    ("uq_payment_accounts_name", "payment_accounts", "tenant_id, lower(name::text)",
     "lower(name::text)", True, None),
    ("uq_customers_is_walk_in", "customers", "tenant_id", "is_walk_in", True, "is_walk_in"),
    ("uq_opening_balances_active", "opening_balances", "tenant_id", "status", True,
     "status = 'posted'"),
    ("ix_journal_lines_party", "journal_lines",
     "tenant_id, account_id, party_type, party_id", "account_id, party_type, party_id",
     False, None),
    ("ix_journal_lines_payment_account_id", "journal_lines", "tenant_id, payment_account_id",
     "payment_account_id", False, None),
    ("ix_journal_entries_business_date", "journal_entries", "tenant_id, business_date",
     "business_date", False, None),
    ("ix_journal_entries_source", "journal_entries",
     "tenant_id, source_doc_type, source_doc_id", "source_doc_type, source_doc_id", False, None),
    ("ix_inventory_movements_business_date", "inventory_movements", "tenant_id, business_date",
     "business_date", False, None),
    ("ix_inventory_movements_source", "inventory_movements",
     "tenant_id, source_doc_type, source_doc_id", "source_doc_type, source_doc_id", False, None),
    ("ix_sales_invoices_business_date", "sales_invoices", "tenant_id, business_date, id",
     "business_date", False, None),
    ("ix_supplier_payments_supplier_id_business_date", "supplier_payments",
     "tenant_id, supplier_id, business_date", "supplier_id, business_date", False, None),
    ("ix_purchase_orders_supplier_id_status", "purchase_orders",
     "tenant_id, supplier_id, status", "supplier_id, status", False, None),
    ("ix_customers_name", "customers", "tenant_id, lower(name::text)", "lower(name::text)",
     False, None),
    ("ix_suppliers_name", "suppliers", "tenant_id, lower(name::text)", "lower(name::text)",
     False, None),
    ("ix_products_name_en", "products", "tenant_id, lower(name_en::text)",
     "lower(name_en::text)", False, None),
    ("ix_audit_logs_occurred_at", "audit_logs", "tenant_id, occurred_at", "occurred_at",
     False, None),
    ("ix_audit_logs_entity", "audit_logs", "tenant_id, entity_type, entity_id",
     "entity_type, entity_id", False, None),
    ("ix_batches_expiry_date", "batches", "tenant_id, expiry_date", "expiry_date", False, None),
)  # fmt: skip

PARTIES = (("customer", "customers"), ("supplier", "suppliers"))


def _tenant_tables() -> set[str]:
    rows = op.get_bind().scalars(
        sa.text(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'tenant_id' "
            "AND table_name <> 'tenant_memberships'"
        )
    )
    return set(rows)


def _foreign_keys(tables: set[str]) -> list[sa.Row]:  # type: ignore[type-arg]
    """Foreign keys of tenant tables: name, child, parent, child columns, parent columns, rule."""
    return list(
        op.get_bind().execute(
            sa.text(
                "SELECT k.conname, c.relname AS child, p.relname AS parent, "
                " (SELECT array_agg(a.attname ORDER BY u.n) FROM unnest(k.conkey) "
                "    WITH ORDINALITY u(num, n) JOIN pg_attribute a "
                "    ON a.attrelid = k.conrelid AND a.attnum = u.num) AS child_cols, "
                " (SELECT array_agg(a.attname ORDER BY u.n) FROM unnest(k.confkey) "
                "    WITH ORDINALITY u(num, n) JOIN pg_attribute a "
                "    ON a.attrelid = k.confrelid AND a.attnum = u.num) AS parent_cols, "
                " pg_get_constraintdef(k.oid) AS definition "
                "FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid "
                "JOIN pg_class p ON p.oid = k.confrelid "
                "WHERE k.contype = 'f' AND c.relname = ANY(:tables) ORDER BY 2, 1"
            ),
            {"tables": sorted(tables)},
        )
    )


def _rule(definition: str) -> str:
    return definition[definition.index(" ON DELETE") :] if " ON DELETE" in definition else ""


def _index(name: str, table: str, columns: str, unique: bool, where: str | None) -> None:
    op.execute(f"DROP INDEX {name}")
    op.execute(
        f"CREATE {'UNIQUE ' if unique else ''}INDEX {name} ON {table} ({columns})"
        + (f" WHERE {where}" if where else "")
    )


def upgrade() -> None:
    tables = _tenant_tables()
    keys = _foreign_keys(tables)

    for parent in sorted({k.parent for k in keys if k.parent in tables}):
        op.execute(f"ALTER TABLE {parent} ADD CONSTRAINT uq_tid_{parent} UNIQUE (tenant_id, id)")

    for key in keys:
        if key.parent in tables:
            target = f"{key.parent} (tenant_id, id)"
        elif key.parent == "users":
            target = "tenant_memberships (tenant_id, user_id)"
        else:
            continue  # global reference data
        column = key.child_cols[0]
        op.execute(f"ALTER TABLE {key.child} DROP CONSTRAINT {key.conname}")
        op.execute(
            f"ALTER TABLE {key.child} ADD CONSTRAINT {key.conname} "
            f"FOREIGN KEY (tenant_id, {column}) REFERENCES {target}"
            f"{_rule(key.definition).replace(' NOT VALID', '')} NOT VALID"
        )
        op.execute(f"ALTER TABLE {key.child} VALIDATE CONSTRAINT {key.conname}")

    for table in sorted(tables):
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT fk_tid_{table} "
            "FOREIGN KEY (tenant_id) REFERENCES tenants (id)"
        )

    for name, table, column in UNIQUES:
        op.drop_constraint(name, table)
        op.create_unique_constraint(name, table, ["tenant_id", column])
    for name, table, columns, _, unique, where in INDEXES:
        _index(name, table, columns, unique, where)

    # The sub-ledger party has no single parent table; generated columns give each kind a key.
    for kind, parent in PARTIES:
        op.execute(
            f"ALTER TABLE journal_lines ADD COLUMN party_{kind}_id bigint "
            f"GENERATED ALWAYS AS (CASE WHEN party_type = '{kind}' THEN party_id END) STORED"
        )
        if f"{parent}" in tables:
            op.execute(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint "
                f"WHERE conname = 'uq_tid_{parent}') THEN ALTER TABLE {parent} "
                f"ADD CONSTRAINT uq_tid_{parent} UNIQUE (tenant_id, id); END IF; END $$"
            )
        op.execute(
            f"ALTER TABLE journal_lines ADD CONSTRAINT fk_journal_lines_party_{kind} "
            f"FOREIGN KEY (tenant_id, party_{kind}_id) REFERENCES {parent} (tenant_id, id)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if (bind.scalar(sa.text("SELECT count(*) FROM tenants")) or 0) > 1:
        raise RuntimeError(
            "More than one dealership exists; downgrading would merge their books. "
            "Restore a backup instead."
        )
    for kind, _ in PARTIES:
        op.execute(f"ALTER TABLE journal_lines DROP COLUMN party_{kind}_id")
    for name, table, _, columns, unique, where in INDEXES:
        _index(name, table, columns, unique, where)
    for name, table, column in UNIQUES:
        op.drop_constraint(name, table)
        op.create_unique_constraint(name, table, [column])

    tables = _tenant_tables()
    for table in sorted(tables):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT fk_tid_{table}")
    for key in _foreign_keys(tables):
        if key.parent not in tables and key.parent != "tenant_memberships":
            continue
        parent = "users" if key.parent == "tenant_memberships" else key.parent
        column = key.child_cols[-1]
        op.execute(f"ALTER TABLE {key.child} DROP CONSTRAINT {key.conname}")
        op.execute(
            f"ALTER TABLE {key.child} ADD CONSTRAINT {key.conname} "
            f"FOREIGN KEY ({column}) REFERENCES {parent} (id){_rule(key.definition)}"
        )
    for parent in sorted(
        bind.scalars(sa.text("SELECT conname FROM pg_constraint WHERE conname LIKE 'uq\\_tid\\_%'"))
    ):
        op.execute(f"ALTER TABLE {parent.removeprefix('uq_tid_')} DROP CONSTRAINT {parent}")
