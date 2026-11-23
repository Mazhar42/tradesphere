# Operations runbook

Production runs on one Ubuntu VPS with Docker Compose. The stack is defined in
[`deploy/docker-compose.prod.yml`](../deploy/docker-compose.prod.yml):

| Service  | What it is | Publishes |
|----------|------------|-----------|
| `web`    | Caddy: HTTPS, the built SPA, `/api` proxied to the API | 80, 443 |
| `api`    | FastAPI under uvicorn, 2 workers, runs as the least-privileged DB role | nothing |
| `migrate`| `alembic upgrade head`, runs before every API start, then exits | nothing |
| `db`     | PostgreSQL 17; owner role `tradesphere`, runtime role `tradesphere_app` | nothing |
| `backup` | nightly `pg_dump -Fc`, verification, retention, off-site copy | nothing |

## 1. First deploy

1. Provision an Ubuntu LTS VPS (2 vCPU / 4 GB), install Docker Engine and the compose plugin, and open
   ports 80 and 443 only. Point the domain's A record at the server.
2. Copy the repository to the server and create the environment file:
   ```sh
   cd deploy
   cp .env.example .env
   openssl rand -base64 48   # use the output for JWT_SECRET
   openssl rand -base64 32   # and for POSTGRES_PASSWORD / APP_DB_PASSWORD (one each)
   ```
   Fill in `SITE_ADDRESS` (the domain), `SITE_URL` (`https://` + the domain), and the three secrets.
   `.env` is gitignored; keep a copy in your password manager.
3. Build and start:
   ```sh
   docker compose -f docker-compose.prod.yml --env-file .env up -d --build
   docker compose -f docker-compose.prod.yml --env-file .env run --rm --no-deps migrate python -m app.cli seed
   docker compose -f docker-compose.prod.yml --env-file .env run --rm --no-deps migrate \
     python -m app.cli create-owner --username <owner> --full-name "<Name>"
   ```
   The owner password is prompted for, not passed on the command line.
4. Check: `https://<domain>/api/health` returns `{"status":"ok"}`, and `https://<domain>/api/docs` returns
   404 (docs are disabled in production).

## 2. Updating the application

```sh
git pull
cd deploy
docker compose -f docker-compose.prod.yml --env-file .env up -d --build
```

`migrate` runs the pending migrations first; the API starts only after it has finished successfully.
Take a backup before updating if the release contains migrations (see section 3).

**Rollback:** check out the previous commit and repeat the command. Migrations are not reversed
automatically; a rollback across a migration needs a restore from backup (section 4).

## 3. Backups

- The `backup` service runs `deploy/backup/backup.sh` every 24 hours (`BACKUP_INTERVAL_SECONDS`).
  Each run writes `backups/daily/*.dump`, verifies the archive with `pg_restore --list`, and keeps
  the newest 14 daily dumps. Sunday's dump is copied to `backups/weekly/` (8 kept) and the 1st of the
  month's to `backups/monthly/` (12 kept).
- Failed runs print `BACKUP FAILED` to the container log: `docker compose ... logs backup`.
- **Off-site copy (required before go-live):** create an S3-compatible bucket (Backblaze B2, Wasabi or
  Cloudflare R2) and a key that can only write to that bucket. Then set in `deploy/.env`, for B2:
  ```
  RCLONE_REMOTE=b2:tradesphere-backups
  RCLONE_CONFIG_B2_TYPE=b2
  RCLONE_CONFIG_B2_ACCOUNT=<key id>
  RCLONE_CONFIG_B2_KEY=<application key>
  ```
  and restart the backup service. The copy uses `rclone copy`, never `sync`, so an emptied server cannot
  delete the remote history. Set a lifecycle rule on the bucket to expire objects older than about a year.
- Run one backup by hand to check: `docker compose ... run --rm --no-deps backup /scripts/backup.sh`.

## 4. Restore drill (monthly)

```sh
cd deploy
bash scripts/restore-drill.sh                                   # newest daily dump
bash scripts/restore-drill.sh backups/monthly/<file>.dump       # or a specific one
```

The script restores the dump into a scratch database, runs the reconcile checks on it (stock, cost,
inventory account, trial balance, daily projection), prints row counts, drops the scratch database and
reports `restore drill PASSED` or the failure. Production data is not touched. Record the date and
result in the operations log. A drill that has not been run for 35 days is overdue.

For a real disaster recovery, restore into an empty database the same way (`pg_restore --no-owner`),
then start the stack against it.

## 5. Monitoring

| What | How | Status |
|------|-----|--------|
| Application health | Uptime monitor on `https://<domain>/api/health`, every 1 minute, alert after 2 failures | To set up (service account required) |
| Container health | `docker compose ps` shows `healthy` for `api` | Built in |
| Structured logs | JSON lines on stdout of `api` (`docker compose logs api`). Each request has `request_id`, `status`, `duration_ms`, `ip`. `X-Request-ID` ties a response to its log line and audit rows | Built in |
| Errors | Sentry for backend and frontend | **Not set up** (needs a Sentry project and an approved dependency) |
| Disk and database size | Host alert on disk above 80%; `pg_database_size('tradesphere')` weekly | To set up on the host |
| Backups | `BACKUP FAILED` in logs; check the off-site copy weekly | Manual until alerting is added |

**Login protection:** after 5 failed sign-ins for a username, or 30 from one IP, within 15 minutes,
sign-in is refused (HTTP 429) for that window. Limits are in `.env`-configurable settings
(`LOGIN_MAX_FAILURES_PER_USER`, `LOGIN_MAX_FAILURES_PER_IP`, `LOGIN_WINDOW_MINUTES`).

## 6. Go-live data migration

Load the books once, dated the day before go-live, through the opening-balances screen
(**Administration → Opening balances**). Collect these from the manual books first:

1. Product list with DP, TP, MRP, carton/piece conversions, and batch/expiry tracking flags.
2. Opening stock per batch with cost, per location (sellable / damaged / expired).
3. Customer dues per customer (or one lump sum per customer). Credit limits.
4. Supplier balances: advances (the company holds our money) and payables (we owe).
5. Pending company claims per supplier.
6. Cash, bank and MFS balances per account.

Checklist:
- Enter the data, then review the wizard's preview totals against the books before posting.
- After posting, check the trial balance balances (**Accounts**), and that the cash, customer dues and
  supplier balances match the books. Then run the reconcile check; it must report no drift:
  ```sh
  docker compose -f docker-compose.prod.yml --env-file .env run --rm --no-deps migrate python -m app.cli reconcile
  ```
  It exits with status 1 and lists each drift. There is no reconcile screen yet (see section 10).
- Make a backup immediately after the opening balances are posted.
- If something is wrong, reverse the opening balances (allowed while no later stock movements exist)
  and post again.

## 7. Load test (sales posting)

`backend/scripts/loadtest_sales.py` creates its own products, customers and stock (through a purchase
order and goods receipt), posts sales from several threads, and reconciles at the end. Run it only on a
stack that may be filled with test data:

```sh
docker run --rm --network tradesphere-prod_default -v "<repo>/backend:/app" tradesphere-api \
  python scripts/loadtest_sales.py --base-url http://api:8000 --user <owner> --password <password> \
  --sales 300 --concurrency 10
```

Measured on a local Docker Desktop stack (2 API workers, PostgreSQL 17), not the VPS:

| Run | Sales | Threads | Posted | Throughput | p50 (create+post) | p95 | Reconcile |
|-----|------:|--------:|-------:|-----------:|------------------:|----:|-----------|
| 1   | 300   | 10      | 300    | 23/s       | 302 ms            | 1.16 s | OK    |
| 2   | 600   | 20      | 600    | 21/s       | 608 ms            | 2.9 s  | OK    |

No request failed. Throughput levels off at about 21 to 23 posted sales per second, with latency rising
as concurrency doubles, which points at lock contention on the product cost rows. A dealership posts a
few hundred invoices a day, so this is well above the need. Re-run on the VPS before go-live.

## 8. Security settings in production

- The API refuses to start in production unless `JWT_SECRET` is at least 32 characters and not the
  development value, and `CORS_ORIGINS` names the site (never `*`).
- The API connects as `tradesphere_app`: it can read and write data but cannot create or alter tables
  (verified: `CREATE TABLE` is refused). Migrations run as the owner role only.
- Passwords are hashed with Argon2id. Refresh tokens rotate on each use and are revoked on reuse.
- Interactive API docs and the OpenAPI schema are disabled in production.
- Caddy sends HSTS, `X-Content-Type-Options`, `X-Frame-Options: DENY` and a referrer policy.
- Only ports 80 and 443 are published. The database and the API are reachable only inside the stack.
- Secrets live in `deploy/.env` on the server only.

## 9. Staff training (outline)

Each user is trained on the screens for their role, with a short walkthrough and a practice on a
test copy of the data:

1. Owner: dashboard, reports and the Reconcile check; opening balances; users and roles; backups.
2. Storekeeper: receiving goods (purchase order → goods receipt), stock, adjustments and transfers.
3. Cashier: invoices, payments, returns, customer statements.
4. Accountant or manager: expenses, transfers, owner money, claims and settlements, month-end reports.

Record who has been trained and when.

## 10. Open items before go-live

- [ ] VPS provisioned, domain pointed, `.env` filled in, stack running with HTTPS.
- [ ] Off-site backup bucket configured and one backup confirmed in it.
- [ ] Uptime monitor set up on `/api/health`.
- [ ] Sentry (backend and frontend) — needs an account and an approved dependency.
- [ ] Restore drill run on the VPS.
- [ ] Go-live data collected from the books and loaded through the wizard; reconcile clean.
- [ ] Staff trained.

Not built yet: a reconcile screen (the check is available as the CLI command above and as
`GET /api/v1/inventory/reconcile`).
