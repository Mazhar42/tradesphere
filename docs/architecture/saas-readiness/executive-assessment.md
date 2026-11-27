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
7. **Reconciliation will not scale** (TS-24): 117 s on 200,000 invoices, run inside a web request.

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

This is the outline; `implementation-roadmap.md` is authoritative and includes a faster route if a single-dealer pilot must come first.

## Scorecard

Scores are judgement against a commercial multi-tenant SaaS, supported by the audit. **10 means appropriate for this scale, secure, tested, observable, documented and proven in operation.** It does not mean unlimited scale or no risk.

| # | Category | Now | Target | Main gap and blockers | Acceptance criteria | Evidence required |
|---|---|---|---|---|---|---|
| 1 | Backend architecture | 7 | 10 | No tenancy or jobs; two import cycles | Import contract passes; every long task is a job | CI report |
| 2 | Database architecture | 6 | 10 | Single tenant; superuser migrations (TS-01, 05) | All checks in `database-transformation.md` §9 return nothing | Query output in CI |
| 3 | Business and financial integrity | 7 | 10 | Deadlock, missing reversals, partial reconciliation (TS-02, 03, 14) | Every document correctable; every invariant has a failing-on-corruption check; no deadlock in race tests | Test run, nightly reconcile history |
| 4 | Authentication and authorisation | 5 | 10 | No memberships, recovery, MFA (TS-09, 15) | MFA enforced for owners and operators; no token after logout; role matrix tested | Tests, review |
| 5 | Frontend architecture | 6 | 10 | No SaaS journeys, tenant context or error boundary | All journeys covered by Playwright in English and Bangla; automated accessibility check clean | E2E report |
| 6 | Performance engineering | 3 | 9–10 | Only a local baseline; reconcile fails its likely budget (TS-24) | Budgets met in the three load scenarios on the stated hardware | Load-test reports, query plans |
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

## Decisions taken (2026-11-27)

| Topic | Decision |
|---|---|
| RPO / RTO | 15 minutes / 4 hours |
| Trial, grace, read-only | 14 days, 7 days, 90 days |
| Staff without email | Owner-created username; invitation as a link |
| Messaging | Email first |
| Branches and warehouses | Not in the first release |
| Currency | BDT; code stored per tenant |
| Billing | Manual first, gateway later |

## Still open

| Decision | Blocks | Needed from |
|---|---|---|
| Plan names, limits, prices; tax or VAT treatment | Phase 5 | Business, accountant |
| Data protection law, hosting location, retention and deletion duties | Pilot | Lawyer. Current law **not confirmed** from an official source |
| VPS provider and region | Phase 15 | Business, after legal advice; compare current prices |
| Payment gateway and fees | Milestone 5.6 | Merchant applications |
| External product data | Milestone 8.6 | Legal review of licences |
| Opening receivable items change how receipts allocate | Milestone 7.6 | Your approval of the impact note |
| Change cost rounding to match money rounding (TS-19) | Milestone 6.6 | Accountant |
| On-call person | Pilot | Business |

## Self-review

Rechecked against the repository and the brief.

| Question | Result |
|---|---|
| Tenant isolation enforceable? | Core mechanisms tested on PostgreSQL 17. Not yet tested inside the application |
| Anything found to be wrong? | Batch 1 assumed trigram indexes; under RLS they are not used. ADR-008 and the database document are corrected |
| Anything newly found? | Reconcile scaling (TS-24); composed and decomposed Bangla text compare unequal; a Debian database image must not reuse an Alpine volume |
| Migration order safe? | Constant-default column addition leaves append-only ledgers untouched (tested). The full migration is unwritten |
| Import and coexistence covered? | Yes, including external snapshots so partial adoption never invents movements |
| Both deployment profiles independent? | Yes. Neither new runbook has been executed |
| Financial semantics preserved? | No posting rule changes, except the flagged opening receivable items |
| Overengineering? | No microservices, Redis, search engine, partitioning, two-way sync or branches |
| Weakest areas | Gateway and GS1 facts, VPS pricing and Bangladeshi law are incomplete; budgets are targets, not measurements; Render role privileges unverified |

## Assumptions that could invalidate the roadmap

- Only test data exists. If real data appears, the migration needs rehearsal on a copy and a cutover plan.
- One database serves the planned load. Unproven until the load tests.
- The demo host allows a non-superuser application role. To be checked on the demo database.
- Pay-per-invoice is acceptable: no gateway recurring debit has been confirmed.

## Documents

| Batch | Documents |
|---|---|
| 1 | This one, `current-state-audit.md`, `target-architecture.md`, `database-transformation.md`, `architecture-decision-records.md` |
| 2 | `data-import-and-coexistence.md`, `search-and-discovery.md`, `subscription-and-billing.md`, `security-and-compliance.md`, `performance-and-scalability.md`, `deployment-render-free.md`, `deployment-vps-production.md`, `testing-and-acceptance.md`, `implementation-roadmap.md` |

External provider facts carry source links and were checked on 2026-11-27.
