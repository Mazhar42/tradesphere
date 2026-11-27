# Architecture decision records

All records are **Accepted** (2026-11-27, with the recommended defaults). ADR-008 was amended afterwards on measured evidence. An implementer must not depart from an accepted record without raising it first.

## ADR-001 · Keep the modular monolith
**Context.** One FastAPI application, one database, domain packages, 235 passing tests.
**Decision.** Keep it. Add `tenancy`, `platform`, `billing`, `jobs`, `imports`, `integrations` and `search` modules. One image runs as API or worker. Enforce import direction in CI (TS-21).
**Alternatives.** Microservices: posting is atomic across sales, stock, journal and claims, so splitting would need distributed transactions. Rewrite: no evidence supports it.
**Consequences.** One deployment unit. Boundaries depend on tooling and review.

## ADR-002 · Shared database, shared schema, `tenant_id`
**Decision.** As specified in `database-transformation.md`: `tenant_id` on every tenant table, composite foreign keys, tenant-scoped uniques and forced RLS.

| Option | Isolation | Operations at 50 tenants | Verdict |
|---|---|---|---|
| Shared schema + RLS + composite FKs | Database-enforced | One migration, one backup, one pool | **Chosen** |
| Schema per tenant | Stronger separation, `search_path` mistakes still possible | 50 × 56 tables; every migration fans out and can half-fail | Rejected |
| Database per tenant | Strongest | 50 migrations, pools and restore procedures | Rejected for now |

**Consequences.** One bug in policy setup affects everyone, so isolation tests are release blockers. A single tenant cannot be restored alone from a physical backup; it needs a restore to a side database and a tenant export. A tenant needing dedicated infrastructure would require an export tool.

## ADR-003 · Isolation in three layers
**Decision.**
1. **Application:** the tenant comes only from the verified membership in the access token. A SQLAlchemy `do_orm_execute` hook adds the tenant criterion to every tenant entity.
2. **Constraints:** composite foreign keys and tenant uniques.
3. **Database:** forced RLS and four non-superuser roles, with context set transaction-locally and re-applied after every commit.

Each layer is tested with the others switched off, so one cannot hide another's failure.
**Alternatives.** Application filtering only: one raw query leaks. RLS only: foreign keys ignore it, and failures are harder to diagnose.
**Consequences.** Tests must connect as the runtime role. Backups need a `BYPASSRLS` role. Data migrations must set context.

## ADR-004 · Identity, membership and authority
**Decision.**
- One global `users` row per person. Login identifier: email or phone for owners; globally unique username still accepted for staff.
- `tenant_memberships` joins people to dealerships. Roles are per tenant, copied from templates; tenants may add custom roles from the fixed permission catalog.
- The access token carries `sub`, `tid`, `aud`, `iss`; membership and permissions are re-read on every request, as today.
- **The refresh cookie is not tenant-bound.** Each browser tab asks to refresh for the tenant it is showing, and the server verifies membership. This stops a tab silently switching dealership and posting into the wrong books.
- A tenant owner may reset a password only for an account their dealership created that has exactly one membership. Otherwise the user resets it themself.
- TOTP MFA: mandatory for platform operators; required for dealership owners at activation, with recovery codes.
- **Platform operators are not tenant members.** They use separate routes and a database role with no grant on business tables.
- RBAC stays. Attribute rules are added only where already implied: cost visibility (`reports:profit`), price override, period lock, and later an optional maximum amount for cash refunds and write-offs.

**Alternatives.** Separate accounts per dealership: confusing for owners of several businesses. External identity provider: cost and a dependency for little gain at this size.

## ADR-005 · Support access
**Decision.** Operators see subscription, usage and health data only. Access to a dealership's records needs a grant approved by that dealership's owner, with a reason and expiry. It runs in a `READ ONLY` transaction, and every request is written to both the tenant's and the platform's audit log.
**Consequences.** Support cannot silently fix data. Corrections are made by the dealership.

## ADR-006 · PostgreSQL-backed jobs and outbox
**Context.** No background execution exists; imports, exports, reconciliation, billing and notifications need it.
**Decision.** A `jobs` table claimed with `FOR UPDATE SKIP LOCKED` by a worker process from the same image. Business events that must trigger work write an `outbox_events` row in the same transaction. Jobs are idempotent, carry `tenant_id`, and set tenant context before running. On the Render demo the worker runs as a thread inside the single web process.
**Alternatives.** Celery or RQ with Redis: another service to secure, back up and monitor, and enqueueing is no longer atomic with the posting.
**Revisit when** job latency or database load is measured to be a problem. Redis is not introduced before then.

## ADR-007 · Import through staging, never direct
**Decision.** Upload → staging rows → mapping → validation → dry run → commit through the **same service functions the API uses**. Conflict policy is explicit and defaults to *skip*. External identifiers are recorded in `external_refs(tenant_id, source_system, entity, external_id)`. A tenant cutover date separates opening positions from live transactions. Files are stored in the database on the demo and in S3-compatible storage in production, behind one interface.
**Alternatives.** SQL `COPY` into live tables: bypasses validation, numbering, journals and audit.
**Consequences.** Slower than bulk load, but every imported fact obeys the same rules as typed data.

## ADR-008 · Search stays in PostgreSQL, fully under RLS
**Context (measured).** Under forced RLS, PostgreSQL does not use trigram indexes for `ILIKE` or similarity, because those operators are not leakproof. For a tenant with 25,000 products, partial matching took 19 ms and fuzzy ranking 114 ms without them.
**Decision.** Exact indexes for SKU, barcode and document number; other matching filters the tenant's rows through the `tenant_id` index; fuzzy only when nothing else matches; Unicode NFC normalisation; keyset pagination. No trigram indexes and no role that bypasses RLS for search.
**Alternatives.** A `SECURITY DEFINER` search function that bypasses RLS: faster, but adds a privileged path. A search engine: another copy of tenant data to isolate.
**Revisit when** a measured budget fails; see `search-and-discovery.md` §1.

## ADR-009 · Billing behind a provider port, manual first
**Decision.** Subscription state lives in TradeSphere. A `PaymentProvider` interface handles collection. The first provider is **manual**: an operator records a bank or mobile-money payment against an invoice. A Bangladeshi gateway follows, with signature-verified, idempotent webhooks. Plan limits are checked only through one `entitlements` service; no business code names a plan.
**Lapse behaviour.** Trial or paid → grace → **read-only** → suspended. A lapsed dealership can always view and export its own data. Posted business records are never removed for non-payment.
**Alternatives.** Gateway first: delays pilots on merchant onboarding. Gateway-hosted subscriptions: availability for local providers is unverified, and it moves state out of the application.

## ADR-010 · Opening positions per domain
**Decision.** Replace the single active opening document with independent opening loads for stock, receivables, payables, claims and cash, still posted through the stock and journal engines against Opening Balance Equity. Existing opening documents remain valid and unchanged.
**Consequences.** Needs rules preventing the same item being opened twice, and a combined cutover reconciliation report.

## ADR-011 · Two deployment profiles
**Decision.** The Render profile is a disposable demonstration, labelled non-production in the UI and holding test data only. Production is an independent Ubuntu VPS stack with Docker Compose and Caddy. Neither depends on the other. Production safety checks are not relaxed for the demo; differences are configuration only.

## ADR-012 · Preserve financial semantics
**Decision.** The transformation changes no posting rule, account mapping, costing method or historical value. Corrections found in the audit (TS-02, TS-03, TS-18, TS-19) are separate, individually reviewed changes. A change that alters how a figure is computed needs a written migration and business-impact note.
