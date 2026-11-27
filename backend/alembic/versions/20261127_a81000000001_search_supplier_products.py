"""one Unicode form for existing names; supplier catalogs and price history

Revision ID: a81000000001
Revises: a71000000001
Create Date: 2026-11-27

Only master-data names are normalised. Ledgers, documents and the audit log are not touched.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a81000000001"
down_revision: str | Sequence[str] | None = "a71000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMES = {
    "products": ("name_en", "name_bn"),
    "categories": ("name_en", "name_bn"),
    "brands": ("name_en", "name_bn"),
    "uoms": ("name_en", "name_bn"),
    "accounts": ("name_en", "name_bn"),
    "customers": ("name", "shop_name", "address", "area"),
    "suppliers": ("name", "contact_person", "address"),
}


def upgrade() -> None:
    # For fuzzy ranking. Already present where `provision` ran (it needs more rights).
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    bind = op.get_bind()
    # Row-level security applies to the owner too: one dealership at a time.
    for tenant in bind.scalars(sa.text("SELECT id FROM tenants")).all():
        bind.execute(sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)})
        for table, columns in NAMES.items():
            for column in columns:
                bind.execute(
                    sa.text(
                        f"UPDATE {table} SET {column} = normalize({column}, NFC) "
                        f"WHERE {column} IS NOT NFC NORMALIZED"
                    )
                )
    bind.execute(sa.text("SELECT set_config('app.tenant_id', '', true)"))

    money = sa.Numeric(18, 4)
    for name, columns, constraints in (
        (
            "supplier_products",
            [
                sa.Column("supplier_id", sa.BigInteger(), nullable=False),
                sa.Column("supplier_sku", sa.String(60), nullable=False),
                sa.Column("description", sa.String(200), nullable=False),
                sa.Column("product_id", sa.BigInteger(), nullable=True),
                sa.Column("pack_factor", sa.Numeric(18, 3), server_default="1", nullable=False),
                sa.Column("price", money, nullable=True),
                sa.Column("min_order_qty", sa.Numeric(18, 3), nullable=True),
                sa.Column("valid_from", sa.Date(), nullable=True),
                sa.Column("source", sa.String(40), nullable=False),
                sa.Column(
                    "last_seen_at",
                    sa.DateTime(timezone=True),
                    server_default=sa.text("now()"),
                    nullable=False,
                ),
            ],
            [
                sa.ForeignKeyConstraint(
                    ["tenant_id", "supplier_id"],
                    ["suppliers.tenant_id", "suppliers.id"],
                    name="fk_supplier_products_supplier_id_suppliers",
                ),
                sa.ForeignKeyConstraint(
                    ["tenant_id", "product_id"],
                    ["products.tenant_id", "products.id"],
                    name="fk_supplier_products_product_id_products",
                ),
                sa.UniqueConstraint(
                    "tenant_id", "supplier_id", "supplier_sku", name="uq_supplier_products_sku"
                ),
            ],
        ),
        (
            "supplier_price_history",
            [
                sa.Column("supplier_product_id", sa.BigInteger(), nullable=False),
                sa.Column("price", money, nullable=False),
                sa.Column("valid_from", sa.Date(), nullable=True),
                sa.Column("source", sa.String(40), nullable=False),
                sa.Column(
                    "recorded_at",
                    sa.DateTime(timezone=True),
                    server_default=sa.text("now()"),
                    nullable=False,
                ),
            ],
            [
                sa.ForeignKeyConstraint(
                    ["tenant_id", "supplier_product_id"],
                    ["supplier_products.tenant_id", "supplier_products.id"],
                    name="fk_supplier_price_history_supplier_product",
                )
            ],
        ),
    ):
        op.create_table(
            name,
            sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
            sa.Column(
                "tenant_id",
                sa.BigInteger(),
                server_default=sa.text("app_current_tenant()"),
                nullable=False,
            ),
            *columns,
            *constraints,
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
    op.create_index(
        "ix_supplier_products_product", "supplier_products", ["tenant_id", "product_id"]
    )
    op.execute(
        "CREATE TRIGGER supplier_price_history_append_only BEFORE UPDATE OR DELETE "
        "ON supplier_price_history FOR EACH ROW EXECUTE FUNCTION forbid_modification()"
    )


def downgrade() -> None:
    op.drop_table("supplier_price_history")
    op.drop_table("supplier_products")
