"""Single source of truth for permissions and default roles.

`python -m app.cli seed` syncs these into the database (idempotent).
Permissions for later phases are declared now so roles stay stable.
"""

PERMISSIONS: dict[str, str] = {
    # Administration
    "users:read": "View users and roles",
    "users:write": "Create and edit users, assign roles",
    "settings:read": "View business settings",
    "settings:write": "Change business settings and period lock",
    "audit:read": "View the audit log",
    # Master data
    "catalog:read": "View products, categories, brands, units",
    "catalog:write": "Create and edit products, categories, brands, units",
    "customers:read": "View customers",
    "customers:write": "Create and edit customers",
    "suppliers:read": "View suppliers",
    "suppliers:write": "Create and edit suppliers",
    # Inventory & purchasing (phase 2)
    "inventory:read": "View stock and stock movements",
    "inventory:adjust": "Post stock adjustments, transfers and write-offs",
    "purchasing:read": "View purchase orders and goods receipts",
    "purchasing:write": "Create purchase orders and receive goods",
    "purchasing:pay": "Record supplier payments and view supplier accounts",
    "purchasing:reverse": "Reverse posted goods receipts and supplier payments",
    # Sales (phase 3/4)
    "sales:read": "View sales invoices and returns",
    "sales:write": "Create and post sales invoices and receipts",
    "sales:return": "Post sales returns",
    "sales:reverse": "Reverse posted sales documents",
    "sales:price_override": "Sell at a price different from the trade price",
    # Claims (phase 4)
    "claims:read": "View company claims",
    "claims:write": "Create claims, record commissions and settlements",
    # Finance & reports (phase 5)
    "finance:read": "View accounts, cashbook and journals",
    "finance:write": "Record expenses, transfers and owner transactions",
    "imports:write": "Upload and check data to import",
    "imports:approve": "Approve an import so it changes records",
    "finance:reverse": "Reverse expenses, fund transfers and owner transactions",
    "claims:reverse": "Reverse claim settlements and commission payments",
    "finance:opening": "Post or reverse opening balances (go-live data load)",
    "reports:read": "View operational reports",
    "reports:profit": "View cost, profit and margin figures",
}

ROLES: dict[str, tuple[str, str, frozenset[str]]] = {
    "owner": ("Owner", "Full access to everything", frozenset(PERMISSIONS)),
    "manager": (
        "Manager",
        "Runs daily operations; cannot manage users or lock periods",
        frozenset(PERMISSIONS) - {"users:write", "settings:write", "finance:opening"},
    ),
    "cashier": (
        "Cashier",
        "Sales counter: invoices, receipts and returns",
        frozenset(
            {
                "catalog:read",
                "customers:read",
                "customers:write",
                "inventory:read",
                "sales:read",
                "sales:write",
                "sales:return",
                "claims:read",
                "reports:read",
                "settings:read",
            }
        ),
    ),
    "storekeeper": (
        "Storekeeper",
        "Warehouse: receiving goods, stock counts and transfers",
        frozenset(
            {
                "catalog:read",
                "catalog:write",
                "suppliers:read",
                "inventory:read",
                "inventory:adjust",
                "purchasing:read",
                "purchasing:write",
                "settings:read",
            }
        ),
    ),
}
