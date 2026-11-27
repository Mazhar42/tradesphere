# Performance and scalability

**No capacity claim is made.** Below are one local baseline, budgets to meet, and how to prove them.

## 1. Baseline (2026-11-27)

Development laptop, Docker with 16 CPUs and about 8 GB, PostgreSQL 17, production image with 2 API workers. Not representative of a VPS.

**Sales load test** (`backend/scripts/loadtest_sales.py`; latency covers create **and** post):

| Sales / threads | Posted per second | p50 | p95 | max | Reconcile |
|---|---|---|---|---|---|
| 300 / 10 | 19.5 | 342 ms | 1.40 s | 3.2 s | OK |
| 600 / 20 | 19.5 | 608 ms | 3.26 s | 7.2 s | OK |

Throughput is flat while latency doubles, consistent with postings queueing on the shared sequence rows (TS-11). That cause is inferred, not profiled.

**Queries** on synthetic data: 25,000 products, 200,900 invoices, 803,604 journal lines, 600,920 movements, 464 MB. Run as superuser (no RLS).

| Query | Time | Note |
|---|---|---|
| Product partial search | 41 ms | Sequential scan; see `search-and-discovery.md` for RLS behaviour |
| Customer balance | 13 ms | Party index used |
| Trial balance, one year | 131 ms | Parallel scan of lines and entries |
| Invoice list page 1 | 14 ms | |
| Invoice count | 35 ms | |
| Invoice list at offset 150,000 | 271 ms | |
| Same position by keyset | 4 ms | |
| Movements of one document | <1 ms | |
| **`reconcile`** | **117 s** | Loads every posted invoice and line into Python (`sales/rebuild.py:35`) |

The reconcile run ended with drift because the synthetic rows have no projections; the time is dominated by loading documents. See TS-24: `GET /inventory/reconcile` does this inside a web request.

## 2. Budgets

Server-side p95, medium dataset, stated hardware.

| Operation | Budget |
|---|---|
| Ordinary read or list | 300 ms |
| Master data write | 500 ms |
| Financial posting | 1 s (20-line invoice) |
| Search | 300 ms |
| Report, one month / one year | 1 s / 3 s |
| Export up to 50,000 rows | job, 60 s |
| Import 10,000 rows: validate / commit | 60 s / 5 min |
| Reconcile one medium tenant | job, 60 s |
| Error rate at target load | below 0.1 %, no deadlocks |
| Frontend entry bundle | 200 kB gzip (now 184 kB) |
| First useful screen on a mid phone over 4G | 3 s (not measured) |

## 3. Load scenarios

| | Small | Medium | SaaS |
|---|---|---|---|
| Tenants | 1 | 1 | 50 |
| Concurrent users | 5 | 20 | 250 (5 each) |
| Products | 5,000 | 25,000 | 5,000 each |
| Invoices | 20,000 | 200,000 | 20,000 each (1 million) |
| Mix | 60 % reads and search, 25 % sales and receipts, 10 % purchasing and stock, 5 % reports | same | same, plus 2 imports and 1 export running |
| Think time | 5 s | 5 s | 5 s |
| Duration | 30 min | 30 min | 2 h soak |

250 users with 5 s think time is about 50 requests per second.

**Infrastructure to test:** a staging VPS identical to the chosen production tier, a separate load-generator machine, a seeded data generator committed to the repo, `pg_stat_statements`, and resource metrics. Use a scripted tool (k6 or Locust) for the mix; keep the existing script as a quick posting check.

## 4. Database

- Every plan check runs **as the app role**, because RLS changes plans.
- Enable `pg_stat_statements`; log statements over 500 ms.
- Set for the app role: `statement_timeout` 15 s, `lock_timeout` 5 s, `idle_in_transaction_session_timeout` 30 s; longer limits for the worker. None are set today.
- Tenant-prefixed indexes (`database-transformation.md` §7); confirm each is used before keeping it.
- Keyset pagination for invoices, movements, journal, audit.
- Check list endpoints for N+1 with a query-count assertion in tests; many relationships load `joined` or `selectin` by default, including where they are not needed.
- `post_journal` loads all accounts per posting (`journal.py:64`); select only the codes used.
- Do not add projections or partitioning until a budget fails. Revisit partitioning of ledgers past about 50 million rows.

## 5. Connections, workers, locks

Connections = API processes × (`pool_size` + `max_overflow`) + worker + platform. Today 2 × 20 = 40. Keep below `max_connections` (default 100) with headroom for backups and migrations. Add a pooler only if processes multiply; transaction mode is compatible with transaction-local context.

Endpoints are synchronous and run in a thread pool, so slow reports occupy threads: move heavy work to jobs.

Locks: follow the single order from TS-02; take sequence rows as late as possible; retry once on deadlock or serialisation failure.

## 6. Jobs

Imports, exports, reconcile, projection rebuild, scheduled sync, billing transitions, notifications, retention purges. Rules: idempotent; one transaction per batch; per-tenant concurrency of 1 for heavy kinds so one dealer cannot starve others; priorities (`interactive` > `scheduled`); retries with backoff and a dead state; metrics for age of oldest job.

Reconcile must be rewritten as SQL aggregation (compare grouped sums in the database) and run nightly per tenant.

## 7. Outbox and caching

An outbox row is written in the business transaction for anything that must happen after commit (email, webhooks, usage counters). No Redis and no response cache now; both need a measured case.

## 8. Memory, payloads, frontend

Stream exports; never load a full table into Python; cap page size at 100 (exists); compress at Caddy (exists). Keep route-level code splitting; keep platform admin in its own chunk; virtualise only tables that actually exceed a few hundred rows.

## 9. Growth

The synthetic set is about 2.3 kB per invoice including indexes, **without** audit rows, idempotency bodies or claims. Plan 5–10 kB per invoice until measured: 1 million invoices ≈ 5–10 GB. Purge idempotency keys after 30 days and expired refresh tokens daily.

## 10. Scale triggers

| Signal | Action |
|---|---|
| CPU above 70 % sustained | Larger VPS |
| p95 over budget with idle CPU | Profile queries and locks first |
| Database competing with application | Move PostgreSQL to its own server or a managed service |
| Job age growing | More worker processes |
| Search budget fails | The option in `search-and-discovery.md` §1 |

## 11. Acceptance

All three scenarios meet §2 on the chosen tier with recorded reports, query plans for the top 20 statements, no deadlocks, and reconcile clean afterwards.
