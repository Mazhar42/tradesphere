# TradeSphere

Dealership inventory, sales, purchasing, company-claims and accounts system for an FMCG dealership in Bangladesh.

- **Backend:** FastAPI · SQLAlchemy 2 · PostgreSQL 17 · Alembic · Pydantic v2
- **Frontend:** React 19 · TypeScript · Vite · Tailwind CSS v4 · TanStack Router / Query / Table · i18next (English + বাংলা)

The full product and technical specification is in **[docs/plan.md](docs/plan.md)**. It covers the data model, posting rules, API, screens and the phase roadmap. Phases 1 (foundation), 2 (inventory core and purchasing), 3 (sales), 4 (returns and company claims) and 5 (finance and reporting) are implemented. Phase 6 (production readiness) is in progress: the deployment stack, backups and restore drill are in `deploy/`, and the owner's runbook is **[docs/operations.md](docs/operations.md)**. For a temporary test deployment on Render's free plans, see **[docs/deploy-render.md](docs/deploy-render.md)**.

## Quick start (development)

Requirements: Docker Desktop, Node 22+ with pnpm.

```bash
# 1. Database + API (the API runs in a container with live reload)
docker compose up -d
docker compose exec api alembic upgrade head
docker compose exec api python -m app.cli seed
docker compose exec api python -m app.cli create-owner   # prompts for username/password

# 2. Frontend
cd frontend
pnpm install
pnpm dev            # http://localhost:5173  (proxies /api to :8000)
```

- API docs: http://localhost:8000/api/docs
- Health: http://localhost:8000/api/health

> **Why does the backend run in Docker?** It keeps development identical to production. It also avoids Windows Smart App Control, which blocks many freshly built Python wheels (e.g. `pydantic-core`, SQLAlchemy) when run on the host.

## Common tasks

| Task | Command |
|---|---|
| Backend tests | `docker compose exec api pytest` |
| Lint / format / types | `docker compose exec api sh -c "ruff check . && ruff format --check . && mypy app"` |
| New migration | `docker compose exec api alembic revision --autogenerate -m "describe change"` |
| Apply migrations | `docker compose exec api alembic upgrade head` |
| Regenerate TS API types | `cd frontend && pnpm gen:api` (API must be running) |
| Frontend checks | `cd frontend && pnpm typecheck && pnpm lint && pnpm test && pnpm build` |

## Project layout

```
backend/
  app/
    core/        config, db session, errors (problem+json), security, permissions
    shared/      base models, money types, BD dates/fiscal year, sequences, audit, pagination
    modules/     auth, users, catalog, parties, inventory, purchasing, sales, claims, finance, accounting, settings, audit
    seed.py      permissions, roles, units, locations, chart of accounts
    cli.py       seed / create-owner
  alembic/       migrations
  tests/         pytest against real PostgreSQL
frontend/
  src/
    api/         generated OpenAPI types + typed client (token refresh, problem+json errors)
    components/  ui primitives, data table, layout
    features/    auth, dashboard, products, catalog, parties, stock, purchases, sales, returns, claims, finance, reports, accounts, users, settings, audit
    i18n/        en.json, bn.json
    lib/         money/date formatting (lakh/crore, Bangla digits), unit conversion
docs/plan.md     master specification
```

## Conventions

- Every business event is its own document. Posted documents are never edited; corrections are reversals.
- Money is `Decimal`/`NUMERIC` everywhere and is sent over the API as **strings**.
- Services never commit. Each router commits once per business operation.
- Every change writes an `audit_logs` row in the same transaction (the table is append-only at the database level).
- Business dates use `Asia/Dhaka`. The fiscal year runs July–June, and document numbers look like `INV-2526-000001`.
