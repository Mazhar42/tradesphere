# Current-state audit

| | |
|---|---|
| Audited | `main` at `84b6326` (branch `saas-transformation`) |
| Date | 2026-11-27 |
| Method | Code and configuration reading, schema introspection of a freshly migrated database, local check runs, and three behaviour probes |
| Paths | `app/…` means `backend/app/…` |

## 1. Verdict

TradeSphere is a well-built **single-dealership** application with a sound financial core. It is **not** a SaaS product: tenancy, subscriptions, onboarding, import, background work and platform administration do not exist. One reproducible concurrency bug (TS-02) is on `main` today.

## 2. Baseline evidence

| Check | Command | Result |
|---|---|---|
| Backend lint | `ruff check .` | Passed |
| Backend format | `ruff format --check .` | 138 files formatted |
| Backend types | `mypy app` | No issues in 107 files |
| Backend tests | `pytest` (PostgreSQL 17) | **235 passed** in 65 s |
| Frontend types | `pnpm typecheck` | Passed |
| Frontend lint | `pnpm lint` | Passed |
| Frontend tests | `pnpm test` | **17 passed** (4 files) |
| Frontend build | `pnpm build` | Passed. Entry chunk 580 kB (184 kB gzip), shared chunk 200 kB (61 kB gzip) |
| Migrations | `upgrade head` → `downgrade base` → `upgrade head` | All passed |
| Model drift | `alembic check` | No new operations |
| Seed and reconcile | `app.cli seed`, `app.cli reconcile` | Passed on an empty database |

**Not run in this audit:** production image builds, the production compose stack, the restore drill, the load test, any dependency or secret scan, any browser test, and anything against Render. Results recorded earlier in `docs/operations.md` for these are not re-verified.

The backend ran in Docker. Compose's bake builder rejected the Dockerfile path on this Windows host, so the dev image was built with `docker build --target dev -t tradesphere-api ./backend`.

## 3. Implementation status

Status classes: **V** implemented and verified · **U** implemented, insufficiently tested · **P** partial · **D** documented, not implemented · **M** missing. "Verified" means automated tests cover it and passed in this audit.

| Capability | Status | Notes |
|---|---|---|
| Catalog, UoM conversions, batches | V | |
| Purchase orders, partial goods receipts, supplier advances and allocation | V | |
| Sales invoices, FEFO, credit limit, receipts | V | See TS-02 |
| Moving weighted average cost, no negative stock | V | |
| Sales and purchase returns, claims, settlements, commissions | V | No reversal path (TS-03) |
| Expenses, fund transfers, owner transactions | P | Post only, no reversal (TS-03) |
| Opening balances | P | One monolithic load (TS-08) |
| Double-entry journal, balanced trigger, append-only ledgers | V | |
| Gapless numbering, idempotency, period lock | V | Period-lock race (TS-18) |
| Reconciliation | P | No receivable or payable check (TS-14) |
| Reversal of any posted document (`docs/plan.md:280`) | D | Only five document types |
| Login, refresh rotation, reuse revocation | V | See TS-04, TS-15 |
| RBAC (4 fixed roles, 30 permissions) | V | Roles are global |
| Audit log | V | |
| Reports and XLSX export | V | |
| English and Bangla UI | U | Key parity (822 keys each); no UI tests |
| Frontend screens | U | 17 unit tests, none for pages, auth or the API client |
| Production compose, Caddy | U | CI builds images and validates the file; not exercised end to end here |
| Backups and restore drill | U | See TS-06 |
| Monitoring, error tracking, alerting (`docs/plan.md` §10) | D | |
| Multi-tenancy, subscriptions, billing, platform admin | M | |
| Registration, invitations, password recovery, verification, MFA | M | |
| Import, external references, integrations | M | |
| Background jobs, outbox, email or SMS | M | |
| Global search, fuzzy search, supplier catalogs, barcode discovery | M | `ILIKE` per list only |
| Row-level security | M | Not claimed by the docs either |

## 4. Findings

Severity is judged against the goal of a commercial multi-tenant SaaS.

| ID | Severity | Title |
|---|---|---|
| TS-01 | Critical | No tenant concept or isolation |
| TS-02 | High | Deadlock between invoice posting and reversal (reproduced) |
| TS-03 | High | Most posted documents cannot be corrected |
| TS-04 | High | Client IP is spoofable on a directly reachable API (reproduced) |
| TS-05 | High | Database privilege model cannot support isolation |
| TS-06 | Critical | Backup and recovery not fit for customer data |
| TS-07 | High | No error tracking, metrics or alerting |
| TS-08 | High | Opening balances block gradual onboarding |
| TS-09 | High | Global user model cannot be safely shared between dealerships |
| TS-10 | High | SaaS capabilities missing |
| TS-11 | Medium | All postings serialise on one counter row |
| TS-12 | Medium | Environment-wide business configuration |
| TS-13 | Medium | Search and pagination will not scale |
| TS-14 | Medium | Reconciliation is incomplete |
| TS-15 | Medium | Authentication hardening gaps (one reproduced) |
| TS-16 | Medium | Web edge hardening gaps |
| TS-17 | Medium | Test and CI gaps |
| TS-18 | Low | Period-lock check is not serialised with posting |
| TS-19 | Low | Two rounding modes |
| TS-20 | Low | Unbounded operational tables |
| TS-21 | Low | Module boundaries not enforced |
| TS-22 | Low | Frontend resilience |
| TS-23 | Low | Documentation and configuration drift |
| TS-24 | Medium | Reconcile loads every sale into memory (measured, added in batch 2) |
| TS-25 | Medium | Reversing a sale can overwrite a claim being submitted (found during phase 0) |

Findings describe `main` at `84b6326`. Phase 0 status is recorded in `implementation-roadmap.md`.

### TS-01 · Critical · No tenant concept or isolation — Missing
- **Files:** all 56 tables; `app/core/db.py`, `app/core/permissions.py`, `app/seed.py`, `app/shared/sequences.py`.
- **Current:** one database equals one business. Global uniques include `products.sku`, `customers.code`, `suppliers.code`, `accounts.code`, `uoms.code`, `locations.code`, `roles.code` and every `doc_no` column. `app_settings` is keyed by `key`, `document_sequences` by `(doc_type, period)`. Only one walk-in customer and one active opening balance can exist.
- **Problem:** a second dealership cannot be added without sharing every record.
- **Consequences:** no SaaS. A naive `tenant_id` retrofit would leak data through unscoped queries, foreign keys and unique errors.
- **Recommendation:** ADR-002 and `database-transformation.md`.
- **Dependencies:** TS-05, TS-09, TS-12.
- **Tests:** schema classification test; cross-tenant negative tests for every route, report, export and job.
- **Accept when:** with RLS on, the runtime role cannot read, write or reference another tenant's row by any path.

### TS-02 · High · Deadlock between invoice posting and reversal — V (defect reproduced)
- **Files:** `app/modules/sales/invoices.py:530-576, 579-653`, `app/modules/sales/receipts.py:240`, `app/modules/accounting/journal.py:101`, `app/modules/inventory/stock.py:56`.
- **Current:** posting locks the product cost row, then takes the `JV` sequence row (`journal.py:101`). Reversing a sale paid at the counter reverses the receipt first (`invoices.py:624`), which takes the `JV` row, and only then locks product costs (`invoices.py:626`). The orders are opposite.
- **Evidence:** 150 sales posted while another thread repeatedly reversed an earlier cash sale of the same product. One sale returned **HTTP 500**; PostgreSQL logged `deadlock detected` between the `document_sequences` upsert and the `product_costs` insert. Books still reconciled afterwards.
- **Consequences:** random failed sales during busy periods, shown as server errors.
- **Recommendation:** adopt one documented global lock order (party → document → `product_costs` by id → `stock_balances` → sequences) and, in `reverse_invoice`, lock costs before any journal posting. Also retry once on SQLSTATE `40P01` and `40001`, and map an exhausted retry to 409.
- **Dependencies:** none. Fix before tenancy work.
- **Tests:** a committed-data concurrency test like `tests/test_sales_concurrency.py` that races post against reverse.
- **Accept when:** 1,000 racing iterations produce no 500 and no logged deadlock.

### TS-03 · High · Most posted documents cannot be corrected — P / D
- **Files:** `app/modules/finance/service.py:33,79,141`, `app/modules/sales/returns.py`, `app/modules/purchasing/returns.py`, `app/modules/claims/service.py`, `app/modules/inventory/documents.py`.
- **Current:** reversal exists only for sales invoices, customer receipts, goods receipts, supplier payments and opening balances. There is none for expenses, fund transfers, owner transactions, sales returns, purchase returns, claim settlements, rejected claim lines or commissions. Adjustments and transfers can be countered only by a new document. There is no manual journal.
- **Problem:** contradicts "corrections happen by reversal" (`docs/plan.md:26, 280`). A mistyped expense stays in the books.
- **Related limit:** a stock-moving document cannot be reversed once *any* later movement exists for one of its products (`stock.py:259`). This protects average cost and is correct, but in a busy shop it makes invoice reversal rarely available.
- **Consequences:** wrong profit, cash and party balances with no supported fix; pressure to edit the database.
- **Recommendation:** add reversals for money-only documents first (expense, transfer, owner transaction, commission, settlement). For stock documents, apply the same later-movement rule and direct users to a return or adjustment.
- **Dependencies:** TS-02 (lock order).
- **Tests:** per type: reversal restores account, party and stock totals; double reversal refused; period lock respected.
- **Accept when:** every posted document type has a reversal or a documented compensating procedure.

### TS-04 · High · Client IP is spoofable — V (defect reproduced)
- **Files:** `backend/start.sh:11`, `backend/Dockerfile:33`, `app/main.py:65`, `app/modules/auth/service.py:29-48`.
- **Current:** uvicorn runs with `--forwarded-allow-ips "*"`.
- **Evidence:** with uvicorn's `ProxyHeadersMiddleware` set to trust `*`, the header `X-Forwarded-For: 6.6.6.6, 203.0.113.9` yields client IP `6.6.6.6`, the value the client chose.
- **Consequences:** on Render the API has its own public address, so the per-IP login limit can be bypassed and audit-log IPs forged. On the VPS today Caddy is the only client, so this is not reachable; adding Cloudflare without Caddy `trusted_proxies` would instead make every user share a Cloudflare IP and one rate-limit bucket.
- **Recommendation:** make trusted proxy addresses configurable, never `*` on a public service; on the VPS trust only the Caddy network, and configure Caddy `trusted_proxies` for Cloudflare. Add a per-process-independent API rate limit.
- **Tests:** forged header does not change the recorded IP.
- **Accept when:** both deployment profiles record the real client IP and ignore forged values.

### TS-05 · High · Database privilege model cannot support isolation — U
- **Files:** `deploy/db/init/01-app-role.sh`, `deploy/docker-compose.prod.yml`, `deploy/backup/backup.sh`, `backend/tests/conftest.py`, `render.yaml`.
- **Current:** migrations, backups and the restore drill run as `POSTGRES_USER`, a **superuser**. The runtime role has `UPDATE` and `DELETE` on every table, including ledgers and audit (only triggers stop it). Tests and the Render demo connect as the table owner or superuser. The role script runs only on first volume creation and interpolates the password into SQL.
- **Problem:** superusers bypass RLS entirely, and table owners bypass it unless it is forced. Isolation tests run this way would pass while proving nothing.
- **Recommendation:** four roles (ADR-003), revoke `UPDATE, DELETE, TRUNCATE` on append-only tables, run the application test session as the runtime role.
- **Dependencies:** blocks TS-01.
- **Tests:** catalog assertions on `rolsuper`, `rolbypassrls`, table owners and grants.
- **Accept when:** no application or migration connection is a superuser, and tests fail if one is.

### TS-06 · Critical · Backup and recovery not fit for customer data — U / P
- **Files:** `deploy/backup/backup.sh`, `deploy/backup/Dockerfile`, `deploy/docker-compose.prod.yml`, `deploy/scripts/restore-drill.sh`.
- **Current:** unencrypted `pg_dump`; off-site copy optional and off by default; no WAL archiving or point-in-time recovery; a `sleep 86400` loop rather than a schedule; failures print to a log nobody watches; backup container runs as root. `pg_dump` omits roles, and the drill uses `--no-privileges`, so a rebuilt server's grants are never tested.
- **Consequences:** up to 24 hours of lost sales and money records, silent backup failure, readable dumps if the disk or bucket is exposed.
- **Recommendation:** encrypted physical backups with WAL archiving, mandatory off-site storage, scheduled restore tests, failure alerts. Detail in `deployment-vps-production.md`.
- **Tests:** timed restore to a clean host, including roles and grants; reconcile on the restored data.
- **Accept when:** a drill demonstrates the agreed RPO and RTO.

### TS-07 · High · No error tracking, metrics or alerting — D
- **Files:** `app/core/observability.py`, `app/main.py:60-111`, `docs/operations.md:89-96`.
- **Current:** JSON logs with a request id and a `/api/health` check that runs `SELECT 1`. No error tracker, metrics, uptime check, disk alert or separate liveness/readiness endpoints.
- **Consequences:** failures such as TS-02 are found by customers.
- **Recommendation:** error tracking, uptime and disk alerts, database and slow-query metrics, tenant id in log context.
- **Accept when:** an induced 500, a failed backup and a full disk each raise an alert.

### TS-08 · High · Opening balances block gradual onboarding — P
- **Files:** `app/modules/accounting/opening.py:64-305`, `app/modules/sales/reports.py:55-133`.
- **Current:** stock, customer dues, supplier balances, claims and cash post as **one** document; only one may be active; it must be dated before today; reversal is refused once any later stock movement exists. Customer dues are a single figure per customer, shown in ageing as `on_account` with no invoice date.
- **Problem:** the product goal is "catalog first, stock later, accounting later". Today the dealer must have every figure at once and cannot add a forgotten part after trading starts. Outstanding purchase orders cannot be opened at all with their part-received state.
- **Recommendation:** opening loads per domain (stock, receivables, payables, claims, cash), one active per domain and item, with a tenant cutover date and optional dated open items. Postings still go through the existing journal and stock engines. Design in `data-import-and-coexistence.md`.
- **Accept when:** a tenant can load stock today and receivables next week, without double counting.

### TS-09 · High · Global user model cannot be safely shared — P
- **Files:** `app/modules/users/models.py`, `app/modules/users/service.py:118-169`, `app/seed.py:71-90`.
- **Current:** usernames are the only identifier (no email), roles are attached directly to users, any holder of `users:write` can reset any user's password, and `seed` overwrites every role's permissions (`seed.py:89`).
- **Problem:** once one person can belong to two dealerships, a dealership owner who can reset that password can take over the other dealership's access.
- **Recommendation:** ADR-004: memberships, per-tenant roles, and no tenant-admin password reset for accounts with more than one membership.
- **Accept when:** tests show tenant A's owner cannot change the credentials or sessions of a shared user.

### TS-10 · High · SaaS capabilities missing — Missing
Registration, verification, recovery, MFA, invitations, subscriptions, entitlements, billing, platform administration, support access, import, external references, integrations, background jobs, notifications, global search and product discovery. Each is designed in the target architecture and its companion documents. **Accept when:** the roadmap's per-phase criteria pass.

### TS-11 · Medium · All postings serialise on one counter row — U
- **Files:** `app/shared/sequences.py:29-39`, `app/modules/accounting/journal.py:101`, `app/modules/sales/invoices.py:553`.
- **Current:** gapless numbers hold a row lock until commit. Every journal posting shares the `JV` row, and every sale takes the `INV` row before stock work.
- **Consequence:** one posting at a time per database. `docs/operations.md` records about 21–23 sales per second locally (not re-run).
- **Recommendation:** keep gapless numbering. Tenant-scoping the key makes the limit per dealership, which is far above a dealership's rate. Take sequence rows as late as possible (with TS-02). Measure before changing anything else.
- **Accept when:** load tests show 50 tenants post concurrently without cross-tenant waiting.

### TS-12 · Medium · Environment-wide business configuration — P
- **Files:** `app/core/config.py:38-39`, `app/shared/dates.py:8`, `app/modules/sales/printing.py:103`.
- **Current:** timezone and fiscal-year start come from environment variables, the timezone is fixed at import time, and the currency symbol `Tk` is hard-coded.
- **Recommendation:** per-tenant `timezone`, `fiscal_year_start_month` and `currency_code`; make the fiscal-year start immutable after the first posting. Launch supports BDT only.
- **Accept when:** two tenants with different fiscal years number documents correctly.

### TS-13 · Medium · Search and pagination will not scale — P
- **Files:** `ILIKE '%q%'` in the catalog, parties, inventory, purchasing, sales, accounting and users services; `app/shared/pagination.py`.
- **Current:** leading-wildcard search with no trigram index; offset pagination with a full `COUNT(*)`; one keyset endpoint (product movements). `post_journal` loads the whole `accounts` table on every posting (`journal.py:64`).
- **Recommendation:** `search-and-discovery.md` and `performance-and-scalability.md`, which record measurements taken afterwards.
- **Accept when:** query plans and latency budgets pass on the medium-dealership dataset.

### TS-14 · Medium · Reconciliation is incomplete — P
- **Files:** `app/modules/inventory/reconcile.py:19`, `app/cli.py:52-73`.
- **Current:** checks stock balances, cost rows, inventory account, trial balance and the daily sales projection. It does not check receivables or payables against documents, claims receivable against claimable items, or COGS against movements. Stock projections can be verified but not rebuilt. All checks scan full tables.
- **Recommendation:** add the missing checks and per-tenant execution as a job; add rebuild commands.
- **Accept when:** each accounting invariant has a check that fails on deliberately corrupted data.

### TS-15 · Medium · Authentication hardening gaps — U
- **Files:** `app/modules/auth/service.py:29-48, 116-171`, `app/core/security.py`.
- **Evidence (reproduced):** after logout, presenting the previously rotated refresh token within the 30-second grace window still returned **200 with an access token**. The grace branch (`service.py:135-141`) does not check that the family is still alive. By code reading, the same applies after a password reset; a deactivated user is refused earlier.
- **Other:** anyone can lock out a known username with five bad attempts; the limit is check-then-act, so parallel requests exceed it; no general API rate limit; no MFA; access tokens have no `aud`/`iss`; passwords require length only.
- **Recommendation:** in the grace branch require an unrevoked successor in the family; rate limit by username and IP together with progressive delay; add MFA (ADR-004).
- **Accept when:** a test proves no token is issued after logout, and lockout cannot be triggered from one hostile IP alone.

### TS-16 · Medium · Web edge hardening gaps — P
- **Files:** `deploy/Caddyfile`, `frontend/Dockerfile`.
- **Current:** HSTS, nosniff, frame deny and referrer policy are set. No Content-Security-Policy or Permissions-Policy, no request body limit, no timeouts; Caddy and backup containers run as root; compose sets no resource limits, log rotation or separate networks. Render's static site sets none of these headers.
- **Accept when:** header and container checks pass for both profiles.

### TS-17 · Medium · Test and CI gaps — P
- **Files:** `.github/workflows/ci.yml`, `frontend/src`, `backend/tests`.
- **Current:** strong backend suite (21 files), three concurrency tests, no end-to-end tests. CI lacks `alembic check`, a downgrade run, an OpenAPI-to-`schema.d.ts` drift check, dependency, secret and image scanning, and coverage reporting.
- **Accept when:** these are required checks on every pull request.

### TS-18 · Low · Period-lock race — U
`ensure_period_open` (`app/modules/settings/service.py:67`) reads the lock date without a row lock, so a posting in flight while the owner locks the period can commit inside it. **Fix:** share-lock the setting row when posting; lock it exclusively when changing. **Test:** committed-data race.

### TS-19 · Low · Two rounding modes — U
Money uses half-up; cost and unit-price quantisation call `.quantize()` with no mode, which is half-even (`stock.py:87,136,210`, `sales/invoices.py:172-180, 403`). **Do not change silently:** it affects historical comparability. Record the rule, and change only with a note and tests.

### TS-20 · Low · Unbounded operational tables — Missing
`idempotency_keys` (stores response bodies), `refresh_tokens` and `audit_logs` are never purged or archived. **Fix:** retention jobs; audit retention is a policy decision.

### TS-21 · Low · Module boundaries not enforced — P
Cycles: inventory↔sales (`inventory/reconcile.py` imports `sales.rebuild`) and accounting↔inventory (`accounting/opening.py`). `core/permissions.py` imports a module. Finance reports query all tables directly. **Fix:** move reconcile and opening into modules above both; add an import-linter contract to CI.

### TS-22 · Low · Frontend resilience — P
No error boundary or not-found page; permission checks run at render, not in `beforeLoad`; `/suppliers/$supplierId` requires `purchasing:pay` to view; invoice and order forms bypass React Hook Form and Zod. The server still enforces every permission.

### TS-23 · Low · Documentation and configuration drift
| Claim | Reality |
|---|---|
| `audit_logs` has no UPDATE/DELETE grants in production (`docs/plan.md:74`) | Grants exist; triggers enforce |
| Health at `/health`, OpenAPI at `/openapi.json` (`docs/plan.md` §5, §10) | `/api/health`, `/api/v1/openapi.json` |
| "Nightly" backups | 24-hour loop that also runs on each restart |
| Migrations run before every API start (`docs/operations.md`) | Only on `compose up`, not on automatic restarts |
| Sentry and disk alerts in phase 6 | Not set up |
| `docs/deploy-render.md` mentions an unpushed branch | Merged in PR #5 |
| `render.yaml` rewrites to a suffixed API host while CORS assumes an unsuffixed web host | Confirm actual service names |
| `loadtest_sales.py` docstring says opening stock | Uses a purchase order and goods receipt |

### TS-24 · Medium · Reconcile does not scale — U
- **Files:** `app/modules/sales/rebuild.py:25-67`, `app/modules/inventory/reconcile.py:19`, `GET /api/v1/inventory/reconcile`.
- **Current:** the daily-sales check loads every posted invoice and return, with their lines and joined records, as ORM objects.
- **Evidence:** on 200,900 synthetic invoices `app.cli reconcile` took **117 s** (it ended with expected drift, because the synthetic rows have no projections). Local Docker, not a VPS.
- **Consequences:** the API route holds a worker thread and much memory for minutes; with many tenants it is a self-inflicted denial of service.
- **Recommendation:** compare grouped sums in SQL; run as a job; the endpoint returns the last result (roadmap 6.4).
- **Accept when:** a medium tenant reconciles within 60 s with bounded memory.

### TS-25 · Medium · Sale reversal can overwrite a claim submission — U
- **Files:** `app/modules/claims/service.py` (`cancel_for_document`, `submit_claim`).
- **Current:** `cancel_for_document` reads claimable items without `FOR UPDATE`, then sets them `cancelled`. `submit_claim` locks an item and sets it `in_claim`.
- **Problem:** found by code reading during phase 0, **not reproduced**. If a claim is submitted between the read and the write, the reversal can overwrite `in_claim` with `cancelled`, leaving a submitted claim with a cancelled item.
- **Recommendation:** lock the items `FOR UPDATE` in `cancel_for_document` and add a committed-data race test. Fits phase 6.
- **Accept when:** the race ends with either a refused reversal or a refused submission.

## 5. What to keep

These are good decisions that the transformation must preserve:

- Documents as separate business events; `draft → posted → reversed`.
- Append-only ledgers enforced by triggers, and a deferred balanced-journal trigger.
- `Decimal`/`NUMERIC` end to end, strings over the API.
- Gapless numbering, ordered row locks, idempotency keys in the same transaction.
- One transaction per operation: services never commit.
- Tests against real PostgreSQL that exercise the migrations.
- Typed API client generated from OpenAPI; access token in memory, refresh token in an `HttpOnly` strict cookie.
- Argon2id, production configuration guard, API docs off in production.

## 6. File-by-file map

| Area | Files | Assessment | Findings |
|---|---|---|---|
| Entry and core | `app/main.py`, `core/{db,config,security,permissions,errors,context,observability}.py` | Clean and small. `db.py` is the hook for tenant context | TS-01, 07, 12 |
| Shared | `shared/{models,money,dates,sequences,idempotency,audit,pagination}.py` | Sound; needs tenant scoping | TS-01, 11, 13, 20 |
| Accounting | `modules/accounting/{journal,opening,service,models,router}.py` | Strong engine; opening needs redesign | TS-08, 11, 13 |
| Inventory | `modules/inventory/{stock,allocation,batches,documents,reconcile}.py` | Strong engine | TS-02, 14, 19 |
| Sales | `modules/sales/{invoices,receipts,returns,ledger,projections,rebuild,printing,reports}.py` | Well tested; large `invoices.py` | TS-02, 03, 12 |
| Purchasing | `modules/purchasing/{service,receiving,payments,returns}.py` | Sound. Supplier payments take no supplier lock, but order allocations are protected by order locks | TS-03 |
| Claims | `modules/claims/{service,reads}.py` | Works; no reversal | TS-03 |
| Finance | `modules/finance/{service,reports,xlsx}.py` | Post-only. XLSX writes inline strings, so no formula injection | TS-03 |
| Identity | `modules/{auth,users,audit}`, `seed.py`, `bootstrap.py` | Good single-tenant design | TS-09, 15 |
| Catalog, parties, settings | `modules/{catalog,parties,settings}` | Straightforward | TS-01, 13 |
| Frontend | `frontend/src/{api,components,features,i18n,lib}` | Consistent; code-split routes | TS-17, 22 |
| Operations | `deploy/`, `render.yaml`, `backend/{Dockerfile,start.sh}`, `.github/` | Reasonable first deployment | TS-04, 05, 06, 16, 17 |
