"""add tenant_id to every business table

Revision ID: a21000000003
Revises: a21000000002
Create Date: 2026-11-27

Ledgers and the audit log refuse UPDATE, and must keep refusing it. Existing rows therefore get
their dealership from a constant column default, which PostgreSQL records in the catalog without
rewriting or touching any row; the default is then switched to the session's dealership.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a21000000003"
down_revision: str | Sequence[str] | None = "a21000000002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen here: later tables bring their own tenant_id.
TABLES = (
    "roles", "role_permissions", "user_roles", "app_settings", "audit_logs", "idempotency_keys",
    "document_sequences", "categories", "brands", "uoms", "products", "product_uoms", "locations",
    "batches", "inventory_movements", "stock_balances", "product_costs", "stock_adjustments",
    "stock_adjustment_items", "stock_transfers", "stock_transfer_items", "customers", "suppliers",
    "purchase_orders", "purchase_order_items", "goods_receipts", "goods_receipt_items",
    "supplier_payments", "payment_po_allocations", "purchase_returns", "purchase_return_items",
    "sales_invoices", "sales_invoice_items", "sale_item_batches", "customer_receipts",
    "receipt_allocations", "sales_returns", "sales_return_items", "daily_sales_summary",
    "claimable_items", "company_claims", "company_claim_lines", "claim_settlements",
    "claim_settlement_lines", "commission_payments", "accounts", "payment_accounts",
    "journal_entries", "journal_lines", "opening_balances", "expenses", "fund_transfers",
    "owner_transactions",
)  # fmt: skip


def upgrade() -> None:
    for name in ("tenant", "user"):
        op.execute(
            f"CREATE FUNCTION app_current_{name}() RETURNS bigint LANGUAGE sql STABLE AS "
            f"$$ SELECT NULLIF(current_setting('app.{name}_id', true), '')::bigint $$"
        )
    bind = op.get_bind()
    tenants = bind.scalars(sa.text("SELECT id FROM tenants ORDER BY id")).all()
    has_rows = [
        t
        for t in TABLES
        if bind.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {t})"))
    ]
    if has_rows and len(tenants) != 1:
        raise RuntimeError(
            f"{', '.join(has_rows)} hold rows but there is not exactly one dealership to "
            "give them to. Nothing was changed."
        )

    for table in TABLES:
        existing = f"{int(tenants[0])}" if table in has_rows else "app_current_tenant()"
        op.execute(f"ALTER TABLE {table} ADD COLUMN tenant_id bigint NOT NULL DEFAULT {existing}")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN tenant_id SET DEFAULT app_current_tenant()")

    # Keys that upserts and lookups name, so they must change together with the code.
    op.execute("ALTER TABLE app_settings DROP CONSTRAINT pk_app_settings")
    op.create_primary_key("pk_app_settings", "app_settings", ["tenant_id", "key"])
    op.execute("ALTER TABLE document_sequences DROP CONSTRAINT pk_document_sequences")
    op.create_primary_key(
        "pk_document_sequences", "document_sequences", ["tenant_id", "doc_type", "period"]
    )
    op.drop_constraint("uq_idempotency_keys_user_id_key", "idempotency_keys")
    op.create_unique_constraint(
        "uq_idempotency_keys_tenant_id_user_id_key",
        "idempotency_keys",
        ["tenant_id", "user_id", "key"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if (bind.scalar(sa.text("SELECT count(*) FROM tenants")) or 0) > 1:
        raise RuntimeError(
            "More than one dealership exists; downgrading would merge their books. "
            "Restore a backup instead."
        )
    op.drop_constraint("uq_idempotency_keys_tenant_id_user_id_key", "idempotency_keys")
    op.create_unique_constraint(
        "uq_idempotency_keys_user_id_key", "idempotency_keys", ["user_id", "key"]
    )
    op.execute("ALTER TABLE document_sequences DROP CONSTRAINT pk_document_sequences")
    op.create_primary_key("pk_document_sequences", "document_sequences", ["doc_type", "period"])
    op.execute("ALTER TABLE app_settings DROP CONSTRAINT pk_app_settings")
    op.create_primary_key("pk_app_settings", "app_settings", ["key"])
    for table in TABLES:
        op.drop_column(table, "tenant_id")
    op.execute("DROP FUNCTION app_current_tenant()")
    op.execute("DROP FUNCTION app_current_user()")
