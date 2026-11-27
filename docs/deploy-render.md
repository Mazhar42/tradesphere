# Temporary deployment on Render

This runs TradeSphere on Render's free plans: a Docker web service for the API, a static site for the
app, and a managed PostgreSQL database. The configuration is in [`render.yaml`](../render.yaml).

> **Temporary, test data only.** The free database expires after 30 days and has **no backups**. Free
> web services sleep after 15 minutes without traffic, and the first visit after that takes about a
> minute. Do not enter real business data. For production, use the VPS stack in
> [operations.md](operations.md).

## What gets created

| Resource | Name (default) | Plan | Notes |
|----------|----------------|------|-------|
| Web service (Docker) | `tradesphere-api` | free | Migrations, seed and owner bootstrap run at each start, then the API serves |
| Static site | `tradesphere-web` | free | Builds `frontend/`. `/api/*` is proxied to the API, so the app and API share one origin |
| PostgreSQL | `tradesphere-db` | free | PostgreSQL 17, connected through `DATABASE_URL` |

The browser only talks to `tradesphere-web.onrender.com`. Requests to `/api` are forwarded to the API,
so the refresh-token cookie and CORS behave as they do in production.

## Before you start: the code must be on GitHub

Render builds from a Git repository. The work is on the local branch **`phase-6-production-readiness`**,
which contains every earlier phase. The GitHub remote currently has only `main`, `phase-2-inventory-purchasing`
and `phase-3-sales`. To deploy, push this branch to a repository that Render can read:

```sh
git push -u origin phase-6-production-readiness
```

If the push is refused (the account permission problem from earlier), either fix the access or push to a
repository you own. Then connect that repository in Render. Render must be granted access to it.

## Steps

1. **Blueprint.** In the Render dashboard: **New → Blueprint**, select the repository and the branch
   `phase-6-production-readiness`, and apply. Render shows the three resources above.
2. **Fill the owner variables.** On `tradesphere-api`, set:
   - `BOOTSTRAP_OWNER_USERNAME`: the sign-in name for the owner account.
   - `BOOTSTRAP_OWNER_FULL_NAME`: the owner's full name.
   - `BOOTSTRAP_OWNER_PASSWORD`: 8 to 128 characters, unique to this deployment.
3. **Wait for the first deploy.** The API build takes a few minutes. Check the logs of `tradesphere-api`:
   you should see `Seed complete.`, `Owner created.` and `Uvicorn running`.
4. **Open the app.** Go to `https://tradesphere-web.onrender.com` and sign in with the owner account.
5. **Remove the password.** Delete `BOOTSTRAP_OWNER_PASSWORD` from `tradesphere-api` (the owner already
   exists, so the variable is no longer needed). Render restarts the service.
6. **Check.** `https://tradesphere-api-m8s0.onrender.com/api/health` (your API's actual address) shows `{"status":"ok"}`, and
   `https://tradesphere-web.onrender.com/api/docs` shows 404 (docs are off in production).

## Check the client address

`TRUSTED_PROXIES` in `render.yaml` trusts private network addresses, which is an assumption about
Render's network that has not been confirmed. After the first deploy:

1. Find your public IP address (search "what is my IP").
2. Sign in once with a wrong password, then sign in properly and open **Audit log**.
3. The failed attempt should show your public IP.

If it shows a private address (`10.…`) or an address that is not yours, a proxy in the chain is not
trusted: add its network to `TRUSTED_PROXIES`. Until then, users behind that proxy share one
per-IP sign-in limit. Never use `*`: anyone could then choose the address that is recorded, and
the app refuses to start with it.

## If the service names differ

Render adds a suffix when a name is already taken, for example `tradesphere-api-x7k2.onrender.com`. Then
edit `render.yaml` and redeploy:

- `routes` on `tradesphere-web`: the `destination` of the `/api/*` rewrite.
- `CORS_ORIGINS` on `tradesphere-api`: the web address, for example `["https://tradesphere-web-x7k2.onrender.com"]`.

Both must match the actual addresses. A mismatch shows up as failed sign-ins or a blank page.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Deploy fails at build | Docker build error | Read the build log; the production Dockerfile is `backend/Dockerfile`, target `prod` |
| API deploy fails health check | Migrations or seed failed at start | Check the `tradesphere-api` logs for the failing command |
| Sign-in works but every page is blank or errors | `/api` rewrite points at the wrong address | Fix the destination (see above) |
| Browser error about CORS | `CORS_ORIGINS` does not match the web address | Set it to the exact `https://…` address, no trailing slash |
| Sign-in refused with "Too many failed sign-in attempts" | Five failures for the same username in 15 minutes | Wait 15 minutes |
| First visit very slow | Free service was asleep | Expected; retry after a minute |

## Limits to keep in mind

- **Data is not safe.** The free database expires after 30 days and has no backups. Do not rely on it.
- **No backup or restore drill** runs here. The backup service in `deploy/` is for the VPS.
- **Rate limiting by IP** uses the client address as worked out from `TRUSTED_PROXIES` (see "Check
  the client address"). Several users behind one address share the per-IP limit. The per-username limit is unaffected.
- **Database role.** Render gives one database user, which owns the tables. The API therefore can change
  the schema. The least-privilege role from the VPS stack is not used here.
- **Secrets.** `JWT_SECRET` is generated by Render. The database password is managed by Render.

## Taking it down

In the Render dashboard, delete the `tradesphere-api`, `tradesphere-web` and `tradesphere-db` resources.
Deleting the database removes its data permanently.
