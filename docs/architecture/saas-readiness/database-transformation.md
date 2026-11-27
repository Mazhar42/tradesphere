# Database transformation

Moves the single-business schema (56 tables, introspected from a migrated database on 2026-11-27) to a shared-schema multi-tenant one. Decisions are in `architecture-decision-records.md` (ADR-002, 003).

## 1. Rules

1. Every table is exactly one of **platform**, **global reference** or **tenant**. A test fails if a table is unclassified.
2. Every tenant table has `tenant_id bigint NOT NULL`, RLS enabled **and forced**, and one tenant policy.
3. Every foreign key between tenant tables is composite: `(tenant_id, x_id) → parent (tenant_id, id)`. PostgreSQL's referential checks **ignore RLS**, so single-column keys would allow cross-tenant references.
4. Every business unique constraint starts with `tenant_id`, or includes a column that is already tenant-unique. Otherwise a duplicate-key error reveals another tenant's data.
5. Primary keys stay as they are (`id`), except the two natural keys in §5.

## 2. Ownership matrix (all 56 existing tables)

### Platform-owned (2)
| Table | Note |
|---|---|
| `users` | One login per person. Loses the direct link to roles |
| `refresh_tokens` | Belongs to a user, not a tenant |

### Global reference (1)
| Table | Note |
|---|---|
| `permissions` | Code-defined catalog, seeded from `permissions_catalog.py` |

### Tenant-owned (53)
| Group | Tables |
|---|---|
| Identity and control (7) | `roles`, `role_permissions`, `user_roles`, `app_settings`, `audit_logs`, `idempotency_keys`, `document_sequences` |
| Catalog (5) | `categories`, `brands`, `uoms`, `products`, `product_uoms` |
| Inventory (9) | `locations`, `batches`, `inventory_movements`, `stock_balances`, `product_costs`, `stock_adjustments`, `stock_adjustment_items`, `stock_transfers`, `stock_transfer_items` |
| Parties (2) | `customers`, `suppliers` |
| Purchasing (8) | `purchase_orders`, `purchase_order_items`, `goods_receipts`, `goods_receipt_items`, `supplier_payments`, `payment_po_allocations`, `purchase_returns`, `purchase_return_items` |
| Sales (8) | `sales_invoices`, `sales_invoice_items`, `sale_item_batches`, `customer_receipts`, `receipt_allocations`, `sales_returns`, `sales_return_items`, `daily_sales_summary` |
| Claims (6) | `claimable_items`, `company_claims`, `company_claim_lines`, `claim_settlements`, `claim_settlement_lines`, `commission_payments` |
| Accounting (5) | `accounts`, `payment_accounts`, `journal_entries`, `journal_lines`, `opening_balances` |
| Finance (3) | `expenses`, `fund_transfers`, `owner_transactions` |

`uoms`, `locations` and `accounts` are tenant-owned, not shared reference data: dealers create units (`POST /uoms`) and accounts, and each needs its own copies seeded from templates.

## 3. New tables

### Platform tables
| Table | Key columns | Phase |
|---|---|---|
| `tenants` | id, slug (unique), legal_name, status, timezone, currency_code, fiscal_year_start_month, cutover_date, created_at | Tenancy |
| `tenant_memberships` | tenant_id, user_id, status, managed_by_tenant, invited_by, joined_at; **unique (tenant_id, user_id)**; never deleted, only deactivated | Tenancy |
| `tenant_invitations` | tenant_id, email/phone, role_ids, token_hash, expires_at, accepted_at | Identity |
| `user_tokens` | user_id, purpose (verify, reset), token_hash, expires_at, used_at | Identity |
| `mfa_factors`, `mfa_recovery_codes` | user_id, encrypted secret, confirmed_at / code_hash, used_at | Identity |
| `auth_events` | occurred_at, user_id null, identifier, action, ip, request_id (login events happen before a tenant is known) | Tenancy |
| `platform_operators` | user_id, role, status | Platform |
| `platform_audit_logs` | append-only; operator, action, target tenant, reason | Platform |
| `support_access_grants` | tenant_id, operator_id, approved_by (tenant owner), reason, scope, expires_at, revoked_at | Platform |
| `plans`, `plan_entitlements` | code, interval, price, currency / plan_id, key, limit | Billing |
| `subscriptions`, `subscription_events` | tenant_id (one live row per tenant), plan_id, status, period dates / append-only history | Billing |
| `tenant_entitlement_overrides` | tenant_id, key, limit, reason, expires_at | Billing |
| `billing_invoices`, `billing_payments` | tenant_id, number, amounts, status / method, reference, recorded_by | Billing |
| `payment_provider_events` | provider, event_id (**unique per provider**), payload, processed_at | Billing |
| `jobs`, `outbox_events` | tenant_id null, kind, payload, status, run_at, attempts, locked_by, dedupe_key | Jobs |

### Tenant tables added by later phases
`import_jobs`, `import_files`, `import_rows`, `import_mappings`, `external_refs`, `supplier_products`, `supplier_price_history`, `integration_connections`, `sync_runs`, `export_files`. Columns are specified in the import, search and billing documents; all follow the rules in §1.

Those documents also add `imported_documents`, opening-load lines, external stock and balance snapshots, `support_requests` and `tenant_usage`. Classify each when created.

## 4. Foreign keys

Apply these mechanical transforms to the current constraints.

| Current | Becomes | Count |
|---|---|---|
| Tenant child → tenant parent (including self-references and `ON DELETE CASCADE`) | `FOREIGN KEY (tenant_id, x_id) REFERENCES parent (tenant_id, id)`, same delete rule | all |
| `created_by_id`, `updated_by_id`, `actor_id` → `users(id)` in tenant tables | `(tenant_id, x_id) → tenant_memberships (tenant_id, user_id)` | all |
| `idempotency_keys.user_id` → `users` | → `tenant_memberships` | 1 |
| `user_roles (user_id, role_id)` | `(tenant_id, user_id) → tenant_memberships`, `(tenant_id, role_id) → roles` | 2 |
| `role_permissions.permission_id` → `permissions` | Unchanged (global reference) | 1 |
| `refresh_tokens` → `users`, self | Unchanged | 2 |

Every referenced tenant table gets `UNIQUE (tenant_id, id)`.

**Polymorphic references.**
- `journal_lines.party_type/party_id` gets database enforcement through generated columns, with no application change:
  ```sql
  ALTER TABLE journal_lines
    ADD COLUMN party_customer_id bigint GENERATED ALWAYS AS
      (CASE WHEN party_type = 'customer' THEN party_id END) STORED,
    ADD COLUMN party_supplier_id bigint GENERATED ALWAYS AS
      (CASE WHEN party_type = 'supplier' THEN party_id END) STORED,
    ADD FOREIGN KEY (tenant_id, party_customer_id) REFERENCES customers (tenant_id, id),
    ADD FOREIGN KEY (tenant_id, party_supplier_id) REFERENCES suppliers (tenant_id, id);
  ```
- `source_doc_type/source_doc_id/source_line_id` on `journal_entries`, `inventory_movements` and `claimable_items` stay polymorphic. The row's own `tenant_id` and RLS stop cross-tenant reads (ids are database-wide identities), and a new reconcile check reports any source that is missing or belongs to another tenant.

## 5. Unique constraints and keys

| Table | Now | Target |
|---|---|---|
| `accounts`, `locations`, `roles`, `uoms`, `customers`, `suppliers` | `(code)` | `(tenant_id, code)` |
| `products` | `(sku)`; `(barcode)` where not null | `(tenant_id, sku)`; `(tenant_id, barcode)` |
| `brands`, `categories` | `(lower(name_en))` | `(tenant_id, lower(name_en))` |
| `payment_accounts` | `(lower(name))` | `(tenant_id, lower(name))` |
| `customers` | one walk-in | `(tenant_id) WHERE is_walk_in` |
| `opening_balances` | one posted | `(tenant_id) WHERE status = 'posted'` (replaced again by the per-domain design in the import phase) |
| every `doc_no`, `journal_entries.entry_no` | global | `(tenant_id, doc_no)`, keeping existing `WHERE` clauses |
| `idempotency_keys` | `(user_id, key)` | `(tenant_id, user_id, key)` |
| `document_sequences` | PK `(doc_type, period)` | PK `(tenant_id, doc_type, period)` |
| `app_settings` | PK `(key)` | PK `(tenant_id, key)` |

**Unchanged** because a column already identifies one tenant: `product_costs(product_id)`, `daily_sales_summary(business_date, product_id)`, `stock_balances(product_id, batch_id, location_id)`, `batches(product_id, batch_no)`, `product_uoms(product_id, uom_id)`, `receipt_allocations(receipt_id, invoice_id)`, the two `reversal_of_id` uniques, and the join-table keys.

**Code that must change with these** (all upserts and natural-key lookups found):
| Location | Change |
|---|---|
| `shared/sequences.py:29-39` | Add `tenant_id` to the conflict target and `current_value` |
| `shared/idempotency.py:76` | Conflict target `("tenant_id","user_id","key")` |
| `modules/settings/service.py:21,34`, `seed.py:166` | `session.get(AppSetting, (tenant_id, key))` |
| `modules/inventory/stock.py:63,125`, `modules/sales/projections.py:29,57` | No target change; `tenant_id` comes from the column default |

## 6. Tenant context and RLS

Checked on PostgreSQL 17 on 2026-11-27: adding `tenant_id` by constant default to a table with an append-only trigger, the generated party column with its composite key, fail-closed reads and inserts under forced RLS, superuser bypass, and a single-column foreign key accepting a cross-tenant reference. Also checked: the session hook keeps the setting across commit, rollback and savepoint and leaves nothing on the pooled connection; `pg_dump` fails on a forced-RLS table without bypass.

```sql
CREATE FUNCTION app_current_tenant() RETURNS bigint
LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::bigint $$;
-- app_current_user() is the same, reading app.user_id

ALTER TABLE products
  ALTER COLUMN tenant_id SET DEFAULT app_current_tenant();

ALTER TABLE products ENABLE ROW LEVEL SECURITY;
ALTER TABLE products FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON products
  USING      (tenant_id = (SELECT app_current_tenant()))
  WITH CHECK (tenant_id = (SELECT app_current_tenant()));
```

- **Fails closed.** With no context the function returns NULL: reads return nothing and inserts are rejected.
- **The column default** means existing inserts need no change, and `WITH CHECK` still rejects a wrong explicit value.
- **`(SELECT …)`** lets the planner evaluate the setting once per statement.
- **`tenant_memberships`** uses `tenant_id = app_current_tenant() OR user_id = app_current_user()`, so a user can list their own dealerships before choosing one. The session hook below sets `app.user_id` the same way.

### Pooling and multiple workers
Context is set with `set_config('app.tenant_id', :id, true)`, which is **transaction-local**, so a pooled connection carries nothing to the next request, across any number of workers. It also works behind a transaction-mode pooler.

Because the routers commit and then read (for example `sales/router.py:85-86`), setting it once per request is **not** enough. The value is stored on the session and re-applied on every new transaction:

```python
@event.listens_for(Session, "after_begin")
def _apply_context(session, transaction, connection):
    tenant_id = session.info.get("tenant_id")
    if tenant_id is not None:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :v, true)"), {"v": str(tenant_id)}
        )
```

### Database roles
| Role | Used by | Attributes |
|---|---|---|
| `tradesphere_owner` | Migrations only | Owns the schema; `NOSUPERUSER NOBYPASSRLS` |
| `tradesphere_app` | API requests, tenant jobs | `NOSUPERUSER NOBYPASSRLS`; DML only |
| `tradesphere_platform` | Platform admin API, job claiming, billing | Grants on **platform tables only**, no grant on any tenant business table |
| `tradesphere_backup` | Logical dumps | `BYPASSRLS`, read-only |

- `REVOKE UPDATE, DELETE, TRUNCATE` on `inventory_movements`, `journal_entries`, `journal_lines`, `audit_logs`, `subscription_events`, `platform_audit_logs` from the app and platform roles. Triggers remain.
- Platform tables with a `tenant_id` (billing, subscriptions): the app role sees its own tenant by policy; the platform role has an allow-all policy.
- **Backups:** `pg_dump` fails on forced-RLS tables unless the role can bypass RLS, hence the separate backup role. Physical backups are unaffected.
- **Data migrations** run as the owner, which forced RLS also restricts: any later backfill must loop over tenants and set the context, or it will silently update nothing.
- **Managed hosts** (Render) may not allow creating these roles. Verify as described in `deployment-render-free.md` §6. If only the owner exists, `FORCE ROW LEVEL SECURITY` still isolates tenants.
- Roles are created by a provisioning script, not Alembic. Moving ownership from the current superuser needs a one-time superuser step.

## 7. Indexes

RLS adds `tenant_id = ?` to every query, so existing secondary indexes get `tenant_id` as their first column:

| Table | Index |
|---|---|
| `journal_lines` | `(tenant_id, account_id, party_type, party_id)`, `(tenant_id, payment_account_id)`; keep `(entry_id)` |
| `journal_entries` | `(tenant_id, business_date)`, `(tenant_id, source_doc_type, source_doc_id)` |
| `inventory_movements` | `(tenant_id, business_date)`, `(tenant_id, source_doc_type, source_doc_id)`; keep `(product_id, id)` |
| `sales_invoices` | `(tenant_id, business_date, id)`, `(tenant_id, customer_id, business_date)` |
| `supplier_payments`, `purchase_orders` | prefix existing indexes |
| `customers`, `suppliers`, `products` | `(tenant_id, lower(name…))`. **No trigram indexes:** under RLS they are not used (measured, `search-and-discovery.md` §1) |
| `audit_logs` | `(tenant_id, occurred_at)`, `(tenant_id, entity_type, entity_id)` |
| `batches` | `(tenant_id, expiry_date)` |

Indexes that lead with a tenant-unique column (foreign key ids) stay. Confirm the final set with `EXPLAIN` on load-test data before dropping any.

## 8. Migration order

Only test data exists, so there is no zero-downtime requirement. It is still staged, because tests run every migration from scratch and each step must be independently testable.

| # | Step | Contents | Reversible |
|---|---|---|---|
| 0 | Provision roles | Script; move table ownership to `tradesphere_owner`; point tests' app session at `tradesphere_app` | Yes |
| 1 | Tenancy tables | `tenants`, `tenant_memberships`, `auth_events`. Create a "default" tenant **only if** business rows exist | Yes |
| 2 | Application plumbing (code) | Tenant resolution, `after_begin` hook, per-tenant provisioning, per-tenant dates and numbering. No behaviour change | Yes |
| 3 | Add `tenant_id` | See below | Yes (drop column) |
| 4 | Constraints | `UNIQUE (tenant_id, id)`, composite FKs `NOT VALID` then `VALIDATE`, tenant-scoped uniques and keys, indexes; drop old constraints last | Yes while one tenant exists |
| 5 | Identity backfill | Memberships from `users`; copy roles per tenant; re-point `user_roles`; move login events | Yes while one tenant exists |
| 6 | RLS | Grants, function, defaults, enable and force, policies, revokes | Yes |
| 7 | Verification gate | §9 queries and isolation tests pass before a second tenant may be created | — |

### Adding `tenant_id` without rewriting history
`UPDATE` is blocked on the ledger and audit tables by their triggers, and must stay blocked. Use a constant default, which PostgreSQL stores in metadata without updating rows:

```sql
-- :default_tenant is the id created in step 1
ALTER TABLE journal_lines ADD COLUMN tenant_id bigint NOT NULL DEFAULT :default_tenant;
ALTER TABLE journal_lines ALTER COLUMN tenant_id SET DEFAULT app_current_tenant();
```

On an empty database (fresh install, test run) there is no default tenant; add the column `NOT NULL` with the function default. No historical value, number or amount changes.

`app.cli seed` splits into `seed` (permissions only) and `provision-tenant` (units, locations, chart of accounts, payment accounts, walk-in customer, settings, system roles). Provisioning may update **system** roles only, never roles a tenant created.

## 9. Verification

Run after step 6; every query must return zero rows.

```sql
-- 1. Tenant tables without forced RLS (exclude the platform and reference lists)
SELECT c.relname FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
  AND c.relname NOT IN ('alembic_version','users','refresh_tokens','permissions' /* + platform list */)
  AND NOT (c.relrowsecurity AND c.relforcerowsecurity);

-- 2. Foreign keys between tenant tables that omit tenant_id
SELECT conrelid::regclass, conname FROM pg_constraint k
WHERE contype = 'f'
  AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = k.conrelid  AND a.attname = 'tenant_id')
  AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = k.confrelid AND a.attname = 'tenant_id')
  AND NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = k.conrelid
                  AND a.attname = 'tenant_id' AND a.attnum = ANY (k.conkey));

-- 3. Over-privileged runtime roles
SELECT rolname FROM pg_roles
WHERE rolname IN ('tradesphere_owner','tradesphere_app','tradesphere_platform')
  AND (rolsuper OR rolbypassrls);

-- 4. Orphaned or cross-tenant journal sources (repeat per source_doc_type)
SELECT j.id FROM journal_entries j
LEFT JOIN sales_invoices d ON d.id = j.source_doc_id AND d.tenant_id = j.tenant_id
WHERE j.source_doc_type = 'sales_invoice' AND d.id IS NULL;
```

**Data integrity:** before step 1 record, per table, row counts and these totals; after step 6 they must be identical: `sum(debit)`, `sum(credit)`, balance per account code, `sum(qty_base)` and `sum(value)` of movements, `sum(total_value)` of `product_costs`, `max(last_value)` per document type. Then `app.cli reconcile` for the tenant.

## 10. Rollback

- **Before a second tenant exists:** Alembic `downgrade` restores the old shape. Each migration's downgrade is exercised in CI.
- **After a second tenant exists:** downgrade must refuse to run. Recover by restoring a backup.
- **Always:** take and verify a backup before step 3. The Render demo may instead be reset and reseeded.
