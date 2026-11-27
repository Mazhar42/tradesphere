# Render free-tier demonstration deployment

> **NON-PRODUCTION. Test data only. Never the only copy of anything.** Render's own documentation says of free instances: "Do not use them for production applications."

This profile is independent of the VPS profile. It uses today's `render.yaml` and `backend/start.sh`. Items marked **[to build]** do not exist yet.

## 1. Provider limits

From [render.com/docs/free](https://render.com/docs/free) and [Blueprint reference](https://render.com/docs/blueprint-spec), checked 2026-11-27. Recheck before relying on them.

| Area | Limit |
|---|---|
| Web service sleep | After 15 minutes without traffic; about a minute to wake |
| Instance hours | 750 per workspace per month; suspended when used up |
| Restarts | May restart at any time |
| Filesystem | Ephemeral; no persistent disk |
| Scaling | One instance |
| Shell, one-off jobs | Not available |
| Pre-deploy command | Paid plans only |
| SMTP | Ports 25, 465, 587 blocked |
| Database | 1 GB; **one** free database per workspace; **expires after 30 days**, 14-day grace, then deleted; **no backups**; no connection pooling |
| Static site | Free; bandwidth and build minutes are metered |
| Abuse | High external traffic can get a service suspended |

Cost: nothing for these services within the limits. Bandwidth allowance is not stated on the page above.

**Use it for:** demonstrations, stakeholder review, UI validation, early feedback. **Not for:** real dealership or financial data, load testing, backup or restore proof, production sign-off.

## 2. Layout

| Resource | Definition |
|---|---|
| `tradesphere-api` | Docker web service, `./backend`, `sh /app/start.sh`, health `/api/health` |
| `tradesphere-web` | Static site from `frontend`; rewrites `/api/*` to the API and everything else to `index.html` |
| `tradesphere-db` | PostgreSQL 17, free |

## 3. Environment

| Variable | Value |
|---|---|
| `ENVIRONMENT` | `production`. **Keep it**: it enforces strong secrets, secure cookies, no API docs |
| `DATABASE_URL` | From the database (Blueprint) |
| `JWT_SECRET` | `generateValue: true`; unique to the demo |
| `CORS_ORIGINS` | JSON list with the real static site URL |
| `LOG_FORMAT` | `json` |
| `BOOTSTRAP_OWNER_USERNAME`, `_FULL_NAME`, `_PASSWORD` | `sync: false`. Prompted only at first Blueprint creation. Remove the password after first start |
| `DEPLOYMENT_PROFILE=demo` **[to build]** | Enables demo banner, in-process worker, log-only email |
| `TRUSTED_PROXIES` **[to build]** | Replaces `--forwarded-allow-ips "*"` (TS-04) |
| `VITE_DEMO=true` (static site) **[to build]** | "Demonstration – do not enter real data" banner |

Demo credentials and secrets are never reused in production.

## 4. Blueprint changes **[to build]**

```yaml
databases:
  - name: tradesphere-db
    plan: free
    ipAllowList: []          # block external connections
# on the static site
    headers:
      - { path: /*, name: X-Frame-Options, value: DENY }
      - { path: /*, name: X-Content-Type-Options, value: nosniff }
      - { path: /*, name: Referrer-Policy, value: strict-origin-when-cross-origin }
```

Add the Content-Security-Policy from `security-and-compliance.md` §5 after testing. With `ipAllowList: []`, no local `psql` access.

## 5. Build, migrate, bootstrap

- API: Render builds the Dockerfile's last stage (`prod`).
- Web: `npm install -g pnpm@12 && pnpm install --frozen-lockfile && pnpm build`, publish `./dist`, `NODE_VERSION=24`.
- Migrations: free services have no pre-deploy step or shell, so `start.sh` runs `alembic upgrade head`, `app.cli seed` and `app.cli bootstrap-owner` at each start. This is acceptable **only** because there is one instance. Never copy this to production.
- Owner: created from the bootstrap variables only if no active owner exists. After tenancy **[to build]**, bootstrap creates a platform operator and one demo tenant.

## 6. Database roles under tenancy

[Render's credentials page](https://render.com/docs/postgresql-credentials) allows extra users, and does not state whether the default user can create roles, is a superuser or can bypass RLS. **Verify on the demo database** before the tenancy phase deploys:

```sql
SELECT current_user, rolsuper, rolbypassrls, rolcreaterole FROM pg_roles WHERE rolname = current_user;
```

| Result | Action |
|---|---|
| Not superuser, no bypass | A single role is acceptable for the demo: `FORCE ROW LEVEL SECURITY` still isolates tenants |
| Superuser or bypass | The startup guard refuses to run; create a restricted user for the application |

In single-role mode the platform and application share a connection, so platform tables are not separated by role. Business-table isolation still holds. Another reason for test data only. No backup role exists; there are no backups.

## 7. Deploy

1. Dashboard → New → Blueprint → choose repository and branch.
2. Enter the three bootstrap values.
3. Wait for all three resources.
4. Check actual service URLs. Today the rewrite points at `tradesphere-api-m8s0.onrender.com` while CORS names `tradesphere-web.onrender.com`; correct both in `render.yaml` and commit.
5. Verify §8, sign in, then delete `BOOTSTRAP_OWNER_PASSWORD`.

Later pushes to the connected branch redeploy automatically. Connect the demo to a branch whose breakage is acceptable.

## 8. Health and smoke checks

```bash
curl -fsS https://<api-host>/api/health                 # {"status":"ok"}
curl -fsS https://<web-host>/api/health                 # same, through the rewrite
curl -s -o /dev/null -w "%{http_code}\n" https://<api-host>/api/docs   # 404
```

Then sign in, create a product, make a cash sale, open a report. With tenancy: confirm a second demo tenant cannot see the first.

Do **not** add a pinger to keep the service awake: it consumes instance hours and hides how the demo behaves.

## 9. Troubleshooting

| Symptom | Likely cause |
|---|---|
| First request takes about a minute | Service was asleep |
| API calls 404 from the web app | Rewrite host does not match the API URL |
| Sign-in works, then immediate sign-out | Cookie not returned: calls bypass the same-origin rewrite |
| Start fails on settings | Weak `JWT_SECRET` or `*` in CORS |
| Start fails connecting | Database expired or still creating |
| Service suspended | Instance hours or traffic limit |
| Deploy times out | Slow migration at start; check logs |
| Emails never arrive | SMTP blocked; use an HTTP email API, or log-only on the demo |

## 10. Reset and teardown

**Reset (also at each 30-day expiry):** delete the database → sync the Blueprint to create a new one → set the bootstrap values again by hand on the API service → manual deploy → run a demo seed script **[to build]** that creates fictitious data.

**Teardown:** delete static site, web service, database, then the Blueprint; remove any DNS entries; revoke demo credentials.

## 11. Acceptance

A clean workspace reaches a working demo from the repository in under 30 minutes by following this document; banner visible; no real data; reset rehearsed once.
