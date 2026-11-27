"""plans, subscriptions, entitlements and manual billing

Revision ID: a51000000001
Revises: a41000000001
Create Date: 2026-11-27

No plan is created: names, limits and prices are a business decision. A dealership without a
subscription row is unrestricted, so existing ones are unaffected.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.schema import SchemaItem

from alembic import op

revision: str = "a51000000001"
down_revision: str | Sequence[str] | None = "a41000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True)


def _created(name: str = "created_at") -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _tenant() -> list[SchemaItem]:
    return [
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
    ]


def _col(name: str, type_: sa.types.TypeEngine, nullable: bool = True, **kw: object) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, type_, nullable=nullable, **kw)  # type: ignore[arg-type]


MONEY = sa.Numeric(18, 2)
# The dealership reads its own rows; only the platform (billing) writes.
TENANT_READS = (
    "subscriptions",
    "subscription_events",
    "tenant_entitlement_overrides",
    "billing_invoices",
    "billing_payments",
    "billing_payment_claims",
)


def upgrade() -> None:
    op.add_column(
        "platform_operators",
        _col("role", sa.String(20), False, server_default="admin"),
    )
    op.create_check_constraint(
        op.f("ck_platform_operators_role_valid"),
        "platform_operators",
        "role IN ('admin','billing','support')",
    )
    op.create_table(
        "plans",
        _id(),
        _col("code", sa.String(30), False),
        _col("version", sa.Integer(), False),
        _col("name", sa.String(100), False),
        _col("interval", sa.String(5), False),
        _col("price", MONEY, False),
        _col("currency", sa.String(3), False),
        _col("trial_days", sa.Integer(), False),
        _col("is_public", sa.Boolean(), False),
        _created(),
        sa.UniqueConstraint("code", "version", name="uq_plans_code_version"),
        sa.CheckConstraint(
            "\"interval\" IN ('month','year')", name=op.f("ck_plans_interval_valid")
        ),
        sa.CheckConstraint("price >= 0 AND trial_days >= 0", name=op.f("ck_plans_not_negative")),
    )
    op.create_table(
        "plan_entitlements",
        _col("plan_id", sa.BigInteger(), False),
        _col("key", sa.String(50), False),
        _col("limit_value", sa.BigInteger()),
        _col("enabled", sa.Boolean()),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.PrimaryKeyConstraint("plan_id", "key", name="pk_plan_entitlements"),
    )
    op.create_table(
        "subscriptions",
        _id(),
        *_tenant(),
        _col("plan_id", sa.BigInteger(), False),
        _col("status", sa.String(12), False),
        _col("trial_ends_on", sa.Date()),
        _col("period_start", sa.Date()),
        _col("period_end", sa.Date()),
        _col("read_only_since", sa.Date()),
        _col("cancel_at_period_end", sa.Boolean(), False, server_default="false"),
        _col("pending_plan_id", sa.BigInteger()),
        _created(),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.ForeignKeyConstraint(["pending_plan_id"], ["plans.id"]),
        sa.UniqueConstraint("tenant_id", name="uq_subscriptions_tenant_id"),
        sa.CheckConstraint(
            "status IN ('trialing','active','past_due','read_only','suspended')",
            name="ck_subscriptions_status_valid",
        ),
    )
    op.create_table(
        "subscription_events",
        _id(),
        *_tenant(),
        _col("from_status", sa.String(12)),
        _col("to_status", sa.String(12), False),
        _col("reason", sa.String(200), False),
        _col("actor_id", sa.BigInteger()),
        _created("occurred_at"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
    )
    op.execute(
        "CREATE TRIGGER subscription_events_append_only BEFORE UPDATE OR DELETE "
        "ON subscription_events FOR EACH ROW EXECUTE FUNCTION forbid_modification()"
    )
    op.create_table(
        "tenant_entitlement_overrides",
        _id(),
        *_tenant(),
        _col("key", sa.String(50), False),
        _col("limit_value", sa.BigInteger()),
        _col("enabled", sa.Boolean()),
        _col("reason", sa.String(200), False),
        _col("expires_on", sa.Date()),
        _col("created_by_id", sa.BigInteger()),
        _created(),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
    )
    op.execute("CREATE SEQUENCE billing_invoice_number_seq")
    op.create_table(
        "billing_invoices",
        _id(),
        *_tenant(),
        _col("number", sa.String(30), False),
        _col("plan_id", sa.BigInteger(), False),
        _col("period_start", sa.Date(), False),
        _col("period_end", sa.Date(), False),
        _col("amount", MONEY, False),
        _col("currency", sa.String(3), False),
        _col("status", sa.String(5), False, server_default="open"),
        _col("issued_on", sa.Date(), False),
        _col("due_on", sa.Date(), False),
        _col("description", sa.String(200), False),
        _created(),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.UniqueConstraint("number", name="uq_billing_invoices_number"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tid_billing_invoices"),
        sa.CheckConstraint(
            "status IN ('open','paid','void')", name="ck_billing_invoices_status_valid"
        ),
        sa.CheckConstraint("amount >= 0", name=op.f("ck_billing_invoices_amount_not_negative")),
    )
    op.create_table(
        "billing_payments",
        _id(),
        *_tenant(),
        _col("invoice_id", sa.BigInteger(), False),
        _col("method", sa.String(20), False),
        _col("provider", sa.String(20), False),
        _col("provider_ref", sa.String(100), False),
        _col("amount", MONEY, False),  # negative for a reversal
        _col("received_on", sa.Date(), False),
        _col("recorded_by_id", sa.BigInteger()),
        _col("reverses_id", sa.BigInteger()),
        _created(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "invoice_id"], ["billing_invoices.tenant_id", "billing_invoices.id"]
        ),
        sa.ForeignKeyConstraint(["recorded_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reverses_id"], ["billing_payments.tenant_id", "billing_payments.id"]
        ),
        sa.UniqueConstraint("provider", "provider_ref", name="uq_billing_payments_provider_ref"),
        sa.UniqueConstraint("reverses_id", name="uq_billing_payments_reverses_id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tid_billing_payments"),
    )
    op.execute(
        "CREATE TRIGGER billing_payments_append_only BEFORE UPDATE OR DELETE "
        "ON billing_payments FOR EACH ROW EXECUTE FUNCTION forbid_modification()"
    )
    op.create_table(
        "billing_payment_claims",
        _id(),
        *_tenant(),
        _col("invoice_id", sa.BigInteger(), False),
        _col("method", sa.String(20), False),
        _col("reference", sa.String(100), False),
        _col("amount", MONEY, False),
        _col("submitted_by_id", sa.BigInteger(), False),
        _created(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "invoice_id"], ["billing_invoices.tenant_id", "billing_invoices.id"]
        ),
        sa.ForeignKeyConstraint(["submitted_by_id"], ["users.id"]),
    )

    own = "tenant_id = (SELECT app_current_tenant())"
    everyone = "(SELECT app_sees_all_tenants())"
    for table in TENANT_READS:
        # A claim ("I have paid") is the one thing the dealership may add.
        check = f"{own} OR {everyone}" if table == "billing_payment_claims" else everyone
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({own} OR {everyone}) WITH CHECK ({check})"
        )
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])


def downgrade() -> None:
    for table in reversed(TENANT_READS):
        op.drop_table(table)
    op.execute("DROP SEQUENCE billing_invoice_number_seq")
    op.drop_table("plan_entitlements")
    op.drop_table("plans")
    op.drop_constraint("ck_platform_operators_role_valid", "platform_operators")
    op.drop_column("platform_operators", "role")
