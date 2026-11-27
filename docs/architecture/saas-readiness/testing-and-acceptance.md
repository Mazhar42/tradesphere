# Testing and acceptance

Starting point: 235 backend tests on real PostgreSQL and 17 frontend tests, all passing on 2026-11-27; no end-to-end tests. Coverage percentage is reported but is not a goal: each test must assert behaviour.

## 1. Harness changes (do first)

- The application session in tests connects as `tradesphere_app`; migrations as the owner. A test fails if the app role is superuser or can bypass RLS (TS-05).
- Fixtures create **two** tenants by default; the shared `Shop` helper takes a tenant.
- Tests that commit real data (concurrency) use a throwaway tenant. Today's cleanup disables triggers with `session_replication_role`, which needs a superuser: give that to a test-only maintenance connection.
- A controllable clock for billing, tokens and fiscal-year tests.

## 2. Backend

| Layer | Content |
|---|---|
| Unit | Money, dates, fiscal year, numbering format, parsers, entitlement resolution |
| Integration | Each endpoint's success, validation, permission and state errors |
| Database | Constraints, triggers, RLS, composite keys, grants; upgrade, downgrade, `alembic check` |
| Financial invariants | §3 |
| Concurrency | Real threads and committed data: post vs reverse (TS-02), last units, numbering, limits, period lock vs posting (TS-18), two workers claiming jobs |
| Idempotency | Same key replays; different body conflicts; failure releases; keys separate per tenant |
| Isolation | §4 |
| Subscription | Every transition and allow/deny cell |
| Import | Fixtures per rule; repeat and retry |
| Security | Authentication flows, rate limits, upload attacks, SSRF, header trust |

## 3. Accounting invariants

Each has an automated test and a nightly `reconcile` check that **fails on deliberately corrupted data**.

| Invariant | Check exists |
|---|---|
| Debits equal credits, per entry and in total | Yes |
| Movements sum to stock balances and cost rows | Yes |
| Inventory account equals stock value | Yes |
| Daily sales projection equals documents | Yes |
| Receivables account per customer equals invoices − receipts − credits + opening | **No** |
| Payables account per supplier equals receipts − payments − returns + opening | **No** |
| Claims receivable equals unsettled claimable items | **No** |
| COGS account equals sales movements net of returns | **No** |
| A reversal returns every account, party and stock balance to its prior value | Per type, partly |
| A repeated request has one effect | Yes |
| Rounding: allocated parts sum to the whole | Partly |
| Documents near 30 June / 1 July number in the right fiscal year per tenant | **No** |
| Business date follows the tenant timezone around midnight | **No** |

## 4. Isolation suite (release blocker)

1. **Schema:** every table classified; tenant tables have `tenant_id`, forced RLS, a policy, composite keys (queries in `database-transformation.md` §9).
2. **Database as app role:** no context returns nothing and rejects writes; tenant A cannot select, update, delete, insert or reference B's rows.
3. **API, generated from the route table:** for every route with an id or body reference, A's token with B's ids returns 404 or 422 and changes nothing. A new route without a case fails the suite.
4. **Aggregates:** every report, dashboard, search, export and print shows only own data, checked with distinctive amounts in both tenants.
5. **Other paths:** jobs, outbox, imports, files, integrations, CLI, cached frontend state after switching.
6. **Layers separately:** run with the ORM filter off (RLS must hold) and with RLS off (filter must hold).
7. **Membership:** removed member, suspended tenant, platform operator without a grant.

## 5. Frontend

| Type | Content |
|---|---|
| Component | Forms (validation, server field errors), tables, permission and subscription states |
| State | Auth, tenant switch clears cache, refresh names the right tenant |
| API integration | Client retry, idempotency key reuse, error mapping (mock server) |
| Accessibility | Automated checks on each page, keyboard paths for sale entry |
| Language | Key parity (exists as a fact; make it a test), screenshots in Bangla |

## 6. End to end (Playwright)

Run in English; key journeys also in Bangla; two browser contexts for two tenants.

1. Registration → verification → onboarding → trial.
2. Invitation → acceptance → role limits.
3. Import products, suppliers; opening stock; reconciliation sign-off.
4. Purchase order → payment → partial receipt → remaining receipt.
5. Cash and credit sale → receipt → print.
6. Sales return → claim → settlement.
7. Reversal of each correctable document.
8. Month close: reports, period lock, reconcile.
9. Trial expiry → read-only → payment → active.
10. Tenant isolation by URL and id manipulation.
11. Two tabs, two dealerships: a sale lands in the shown one.

## 7. Infrastructure

Production images build; compose file validates; migrations from empty and from the previous release with data; downgrade while single-tenant; backup, `verify`, restore and reconcile; deploy smoke test; rollback to the previous image; port scan and header check.

## 8. Required checks on every pull request

```text
backend : ruff check · ruff format --check · mypy app · pytest (app role)
          alembic upgrade head · alembic check · downgrade round trip
contract: regenerate schema.d.ts and fail on difference · import boundaries
frontend: typecheck · lint · test · build · bundle size budget
security: dependency audit · secret scan · image scan
e2e     : critical journeys (1, 4, 5, 10)
```

All journeys and a load smoke run nightly. Branch protection requires these on `main` and `saas-transformation`.

## 9. Gates

| Gate | Conditions |
|---|---|
| Milestone | Its criteria marked passed, failed or not verified, with commands and output |
| Phase | Full suite; docs updated; no skipped test without an issue |
| Release | Migration rehearsal on staging, smoke, rollback plan |
| Second tenant allowed | Isolation suite green |
| Pilot | Restore drill, alerts, MFA, import rehearsal |
| Commercial launch | `executive-assessment.md` launch criteria |

Rules: never delete or weaken a test to pass; a flaky test is a defect with an owner; every fixed bug gets a test that failed before the fix.
