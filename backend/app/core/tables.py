"""Which kind of data every table holds. Grants and row-level security follow from this list,
and a test fails if a table is missing from it.

- PLATFORM: not owned by one dealership. The platform role may use these, and only these.
- GLOBAL: reference data defined by the code; the running application only reads it.
- TENANT: belongs to one dealership.
- APPEND_ONLY: nothing may change or remove a row, by grant as well as by trigger.
"""

PLATFORM_TABLES = frozenset(
    {
        "users",
        "refresh_tokens",
        "user_tokens",
        "mfa_factors",
        "mfa_recovery_codes",
        "tenants",
        "tenant_memberships",
        "auth_events",
        "platform_operators",
        "platform_audit_logs",
        "support_access_grants",
        "jobs",
        "outbox_events",
        "plans",
        "plan_entitlements",
        "subscriptions",
        "subscription_events",
        "tenant_entitlement_overrides",
        "billing_invoices",
        "billing_payments",
        "billing_payment_claims",
    }
)

# Platform tables the application may only read: it never writes its own subscription.
APP_READ_ONLY_TABLES = frozenset(
    {
        "plans",
        "plan_entitlements",
        "subscriptions",
        "subscription_events",
        "tenant_entitlement_overrides",
        "billing_invoices",
        "billing_payments",
    }
)

# The application adds work and may look at its own; only the worker's role changes it.
APP_ADD_ONLY_TABLES = frozenset({"jobs", "outbox_events", "billing_payment_claims"})

# Closed to the application role altogether: a flaw in the dealership application must not be
# able to make or unmake an operator.
OPERATOR_TABLES = frozenset({"platform_operators"})
# The application may add to these but never read or change them.
APP_INSERT_ONLY_TABLES = frozenset({"platform_audit_logs"})

# Rows that are deactivated, never removed: documents and history point at them.
NO_DELETE_TABLES = frozenset({"tenants", "tenant_memberships"})

GLOBAL_TABLES = frozenset({"permissions"})

TENANT_TABLES = frozenset(
    {
        # identity and control
        "roles",
        "tenant_invitations",
        "role_permissions",
        "user_roles",
        "app_settings",
        "audit_logs",
        "idempotency_keys",
        "document_sequences",
        # catalog
        "categories",
        "brands",
        "uoms",
        "products",
        "product_uoms",
        # inventory
        "locations",
        "batches",
        "inventory_movements",
        "stock_balances",
        "product_costs",
        "stock_adjustments",
        "stock_adjustment_items",
        "stock_transfers",
        "stock_transfer_items",
        # parties
        "customers",
        "suppliers",
        # purchasing
        "purchase_orders",
        "purchase_order_items",
        "goods_receipts",
        "goods_receipt_items",
        "supplier_payments",
        "payment_po_allocations",
        "purchase_returns",
        "purchase_return_items",
        # sales
        "sales_invoices",
        "sales_invoice_items",
        "sale_item_batches",
        "customer_receipts",
        "receipt_allocations",
        "sales_returns",
        "sales_return_items",
        "daily_sales_summary",
        # claims
        "claimable_items",
        "company_claims",
        "company_claim_lines",
        "claim_settlements",
        "claim_settlement_lines",
        "commission_payments",
        # accounting and finance
        "accounts",
        "payment_accounts",
        "journal_entries",
        "journal_lines",
        "opening_balances",
        "expenses",
        "fund_transfers",
        "owner_transactions",
        "reconcile_runs",
        "import_files",
        "import_jobs",
        "import_rows",
        "import_mappings",
        "external_refs",
        "opening_loads",
        "opening_lines",
        "imported_documents",
        "cutover_signoffs",
        "supplier_products",
        "supplier_price_history",
    }
)

APPEND_ONLY_TABLES = frozenset(
    {
        "supplier_price_history",
        "inventory_movements",
        "journal_entries",
        "journal_lines",
        "audit_logs",
        "auth_events",
        "platform_audit_logs",
        "subscription_events",
        "billing_payments",
    }
)

# Not application data.
SYSTEM_TABLES = frozenset({"alembic_version"})
