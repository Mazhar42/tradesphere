# Executive assessment

Date 2026-11-27. Based on `main` at `84b6326`.

## Recommendation

**Do not onboard paying dealerships yet. Do not rewrite.** Keep the application and transform it in place into a multi-tenant modular monolith.

The financial core is the strongest part: append-only ledgers, balanced-journal enforcement, gapless numbering, idempotent posting and 235 passing tests. What is missing is everything that makes it a service: tenant isolation, SaaS identity, billing, onboarding and import, and production operations.

| Use | Decision |
|---|---|
| Demonstrations on Render with test data | **Go** |
| One dealership, own server, real data | **No-go** until TS-02, TS-03, TS-06 and TS-07 are closed |
| Several dealerships on one service | **No-go** until tenant isolation passes its gate |
| Commercial launch | **No-go** until every launch criterion below is evidenced |

## Critical risks

1. **No tenant isolation** (TS-01). The largest piece of work; every other SaaS feature depends on it.
2. **Backups are not fit for customer data** (TS-06): unencrypted, off-site optional, up to a day of loss, silent failure.
3. **A sale can fail with a server error today** (TS-02, reproduced). Small fix, do first.
4. **Wrong entries often cannot be corrected** (TS-03).
5. **The current database roles would silently defeat row-level security** (TS-05).
6. **Gradual onboarding is blocked** by the one-time opening balance design (TS-08).

## Recommended order

1. **Stabilise:** fix TS-02, TS-04, TS-15 refresh grace; CI gates; error tracking.
2. **Database foundations:** separate roles, tests as the runtime role.
3. **Tenancy:** staged migration, context, RLS, isolation test suite.
4. **Identity and platform administration.**
5. **Jobs, then subscriptions and entitlements** (manual billing).
6. **Import and onboarding**, then corrections (TS-03) and per-domain opening balances.
7. **Search and discovery**, then integrations.
8. **Performance, security hardening, production operations**, restore drills.
9. **Pilot**, then launch decision.

This is the outline; `implementation-roadmap.md` (batch 2) will be authoritative and may move TS-03 earlier if a single-dealer pilot comes first.

## Scorecard

Scores are judgement against a commercial multi-tenant SaaS, supported by the audit. **10 means appropriate for this scale, secure, tested, observable, documented and proven in operation.** It does not mean unlimited scale or no risk.

| # | Category | Now | Target | Main gap and blockers | Acceptance criteria | Evidence required |
|---|---|---|---|---|---|---|
| 1 | Backend architecture | 7 | 10 | No tenancy or jobs; two import cycles | Import contract passes; every long task is a job | CI report |
| 2 | Database architecture | 6 | 10 | Single tenant; superuser migrations (TS-01, 05) | All checks in `database-transformation.md` §9 return nothing | Query output in CI |
| 3 | Business and financial integrity | 7 | 10 | Deadlock, missing reversals, partial reconciliation (TS-02, 03, 14) | Every document correctable; every invariant has a failing-on-corruption check; no deadlock in race tests | Test run, nightly reconcile history |
| 4 | Authentication and authorisation | 5 | 10 | No memberships, recovery, MFA (TS-09, 15) | MFA enforced for owners and operators; no token after logout; role matrix tested | Tests, review |
| 5 | Frontend architecture | 6 | 10 | No SaaS journeys, tenant context or error boundary | All journeys covered by Playwright in English and Bangla; automated accessibility check clean | E2E report |
| 6 | Performance engineering | 3 | 9–10 | No budgets or realistic data; not measured in this audit | Budgets met in the three load scenarios on the stated hardware | Load-test reports, query plans |
| 7 | Automated testing | 6 | 10 | No E2E, few frontend tests, CI gaps (TS-17) | Required PR checks defined and enforced; critical journeys and isolation automated | CI history |
| 8 | Multi-tenancy | 0 | 10 | Does not exist | Negative tests for every route, report, export, job and import pass as the runtime role | Isolation suite; external review |
| 9 | Subscription and billing | 0 | 10 | Does not exist | Every lifecycle transition and entitlement tested; invoices match payments | Tests, pilot billing cycle |
| 10 | Production operations | 3 | 10 | No release, rollback or alert practice proven (TS-06, 07, 16) | Scripted deploy and rollback exercised on a staging host | Runbook execution log |
| 11 | Data migration and integration | 1 | 10 | Only opening balances (TS-08) | Three onboarding scenarios rehearsed; repeat import changes nothing | Rehearsal reconciliation reports |
| 12 | Search and discovery | 2 | 10 | `ILIKE` per list (TS-13) | Latency budget met; permission- and tenant-aware; Bangla and English cases | Tests, timings |
| 13 | Security | 4 | 9 | TS-04, 05, 16; no scanning or threat model | Threat model reviewed; ASVS and API Top 10 items mapped; independent test with no open High | Scan and test reports |
| 14 | Maintainability | 7 | 10 | Documentation drift (TS-23) | Docs match behaviour; ADRs current; boundaries enforced | Review |
| 15 | Observability and disaster recovery | 2 | 10 | No alerts or point-in-time recovery | Induced failures alert; restore meets RPO and RTO in a timed drill | Drill record |

**Why two targets may stop at 9.** Security cannot be self-certified: 10 needs an independent assessment and an operating history. Performance beyond the tested scenarios is unproven by definition.

## Launch criteria

All must be evidenced, not asserted:

- [ ] No cross-tenant access in the isolation suite, run as the runtime database role.
- [ ] Stock, cost, receivables, payables and trial balance reconcile after load and migration tests.
- [ ] A rehearsed dealership import reconciles to the dealer's own figures, signed off by the dealer.
- [ ] Encrypted off-site backups; a timed restore meets RPO and RTO.
- [ ] Deployment and rollback exercised.
- [ ] Alerts proven for errors, downtime, disk and backup failure.
- [ ] Threat model reviewed; no open Critical or High security finding.
- [ ] Load results recorded for the supported scale.
- [ ] A pilot dealership has run a full month, including period lock and reconciliation.

## Open decisions

Please settle these before the batch 2 documents; the recommendation applies if you have no preference.

| # | Decision | Recommendation |
|---|---|---|
| 1 | RPO and RTO | RPO 15 minutes, RTO 4 hours |
| 2 | Trial and grace lengths | 14-day trial, 7-day grace, 90 days read-only before suspension |
| 3 | Login for staff without email | Owner-created username; invitations shared as a link, to avoid SMS cost |
| 4 | SMS or email provider | Email first; add SMS only if pilots need it |
| 5 | Branches and multiple warehouses | Not in the first release. Locations stay stock buckets per tenant |
| 6 | Currency | BDT only at launch; store the code per tenant |
| 7 | Plan limits and prices | Needed from you before billing is built |
| 8 | Data retention after cancellation or deletion, and any Bangladesh legal requirements for financial records | Needs legal advice; not assessed |
| 9 | VPS provider and region | Compare in `deployment-vps-production.md` |

## Assumptions that could invalidate the roadmap

- Only test data exists. If real data appears, the migration needs rehearsal on a copy and a cutover plan.
- One database serves the planned load. This is unproven until the load tests.
- The demo host allows the database setup RLS needs. To be checked against current Render documentation.
- A Bangladeshi gateway offers the needed recurring or invoice-payment flow. Not yet researched.

## Delivery status

| Batch | Documents | State |
|---|---|---|
| 1 | This one, `current-state-audit.md`, `target-architecture.md`, `database-transformation.md`, `architecture-decision-records.md` | Ready for review |
| 2 | Import and coexistence, search and discovery, subscription and billing, security and compliance, performance, Render deployment, VPS deployment, testing and acceptance, implementation roadmap | After approval |

No external provider facts (pricing, limits, gateway APIs) are asserted in batch 1.
