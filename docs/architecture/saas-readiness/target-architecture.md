# Target architecture

A modular monolith serving many independent dealerships from one database with database-enforced isolation. Rationale is in `architecture-decision-records.md`; the schema is in `database-transformation.md`.

## 1. Modules

| Module | Owns | Status |
|---|---|---|
| `core` | Config, database session and tenant context, security, errors, logging | Extend |
| `shared` | Money, dates, numbering, idempotency, audit, pagination | Extend |
| `tenancy` | Tenants, memberships, invitations, provisioning, tenant settings | New |
| `auth`, `users` | Login, sessions, MFA, recovery, per-tenant roles | Extend |
| `platform` | Operators, support grants, platform audit, platform admin API | New |
| `billing` | Plans, subscriptions, entitlements, billing records, payment providers | New |
| `jobs` | Queue, outbox, worker, schedules | New |
| `catalog`, `parties`, `inventory`, `purchasing`, `sales`, `claims`, `accounting`, `finance`, `settings`, `audit` | The existing business domains | Keep |
| `imports` | Staging, mapping, validation, commit, reports | New |
| `integrations` | External references, connections, sync runs | New |
| `search` | Search endpoints and product discovery | New |

**Dependency direction** (enforced in CI): `core/shared` ← `tenancy/auth` ← business domains ← `imports/integrations/search`. `billing` exposes only the entitlement check to business domains. `platform` never imports business domains. Reconciliation and opening balances move above the domains they combine, removing today's two cycles.

**Conventions kept:** services never commit; one transaction per operation; routes declare permissions; money is `Decimal`; the server computes totals.

## 2. SaaS platform

```mermaid
flowchart LR
  subgraph Clients
    D["Dealership users<br/>web app"]
    O["Platform operators<br/>admin area"]
    X["External systems"]
  end
  P["Reverse proxy<br/>TLS, headers, limits"]
  subgraph App["One application image"]
    A["API process<br/>tenant routes"]
    PA["API process<br/>platform routes"]
    W["Worker process"]
  end
  DB[("PostgreSQL<br/>RLS")]
  S[("Object storage<br/>imports, exports")]
  G["Payment gateway<br/>later phase"]
  M["Email / SMS"]

  D --> P
  O --> P
  X --> P
  P --> A
  P --> PA
  A -->|"tradesphere_app"| DB
  PA -->|"tradesphere_platform"| DB
  W -->|"claim jobs: platform role<br/>run jobs: app role"| DB
  A --> S
  W --> S
  W --> M
  G -->|"signed webhooks"| P
```

Tenant and platform routes live in the same service but use different dependencies and database connections.

## 3. Tenant isolation

```mermaid
sequenceDiagram
  participant C as Browser tab
  participant API
  participant S as DB session
  participant PG as PostgreSQL
  C->>API: request + access token (sub, tid)
  API->>S: session
  API->>PG: verify active membership and tenant status
  API->>S: info.tenant_id, info.user_id
  Note over S,PG: after_begin runs set_config(..., true) in every transaction
  S->>PG: query (ORM adds tenant criterion)
  PG->>PG: RLS policy filters rows
  S->>PG: insert (tenant_id from column default)
  PG->>PG: WITH CHECK and composite FKs
  API->>PG: COMMIT, context discarded
  API->>PG: read-back opens a new transaction, context re-applied
  API-->>C: response
```

**Rules for every path that is not an ordinary request:**

| Path | Rule |
|---|---|
| Jobs | Payload carries `tenant_id`; the worker sets context before calling any service |
| Imports and exports | Files are keyed `tenant/{id}/…`; downloads go through an authorised endpoint, never a public URL |
| Reports and search | Run on the same session, so RLS applies |
| Caches | None server-side today. Any future cache key starts with the tenant id. The frontend clears its query cache on tenant switch |
| Logs | Tenant id and user id in context; no amounts or personal data |
| Idempotency | Keys are unique per tenant and user |
| CLI | Commands take `--tenant` or `--all-tenants`; none runs without a context |

## 4. Authentication and authorisation

```mermaid
flowchart TD
  L["Login: identifier + password"] --> MFA{"MFA enrolled<br/>or required?"}
  MFA -->|yes| T["Verify TOTP"]
  MFA -->|no| R
  T --> R["Issue refresh cookie"]
  R --> SEL["Choose dealership"]
  SEL --> AT["Access token: sub, tid, aud, iss<br/>15 minutes, in memory"]
  AT --> Q{"Each request"}
  Q --> U{"User active?"}
  U -->|no| E401["401"]
  U -->|yes| MB{"Membership active?"}
  MB -->|no| E403["403"]
  MB -->|yes| TS{"Tenant status"}
  TS -->|suspended| E403
  TS -->|read only and writing| E402["402 subscription required"]
  TS -->|ok| PERM{"Permission held?"}
  PERM -->|no| E403
  PERM -->|yes| ENT{"Entitlement allows?"}
  ENT -->|no| E402
  ENT -->|yes| H["Handler"]
```

Order matters: identity, membership, tenant status, permission, entitlement, then business rules.

## 5. Subscription lifecycle

```mermaid
stateDiagram-v2
  [*] --> pending: registered
  pending --> trialing: email or phone verified
  trialing --> active: payment recorded
  trialing --> read_only: trial ended
  active --> past_due: renewal unpaid
  past_due --> active: payment recorded
  past_due --> read_only: grace ended
  read_only --> active: payment recorded
  read_only --> suspended: retention period ended
  suspended --> active: reactivated
  active --> cancelled: owner cancels, effective at period end
  cancelled --> read_only: period ends
  suspended --> deletion_scheduled: owner request or policy
  deletion_scheduled --> deleted: waiting period and export offered
  deletion_scheduled --> suspended: request withdrawn
  deleted --> [*]
```

Registration creates the account and tenant; it does not require payment. **Subscription status** (billing) is separate from **tenant status** (operator action, for example abuse), and the stricter one applies. Durations are open decisions in `executive-assessment.md`.

## 6. Import pipeline

```mermaid
flowchart LR
  U["Upload CSV or XLSX"] --> F["File checks<br/>type, size, rows"]
  F --> ST["Parse into staging rows"]
  ST --> MAP["Map columns<br/>saved templates"]
  MAP --> V["Validate rows<br/>duplicates, references"]
  V -->|errors| ER["Error report download"]
  ER --> U
  V --> DR["Dry run<br/>what would change"]
  DR --> AP{"Authorised approval"}
  AP -->|no| X["Discard"]
  AP -->|yes| CM["Commit job<br/>domain services, batches"]
  CM --> RP["Import report<br/>reconciliation"]
  CM -->|batch failed| RT["Safe retry from last batch"]
  RT --> CM
```

Master data may be corrected by a further import. Anything that posts stock or money is corrected only by reversal, subject to the rules in TS-03.

## 7. External integration

```mermaid
flowchart LR
  SRC["Dealer's system<br/>Excel, ERP, accounting"]
  subgraph TS["TradeSphere"]
    IN["Inbox: files or API pull"]
    STG["Staging"]
    REF[("external_refs")]
    REV{"Needs approval?"}
    DOM["Domain services"]
    REC["Reconciliation report"]
  end
  SRC -->|"one direction"| IN --> STG
  STG <--> REF
  STG --> REV
  REV -->|"master data, by policy"| DOM
  REV -->|"stock or money: always"| DOM
  DOM --> REC
  DOM -->|"CSV export"| SRC
```

Launch scope is **read-only synchronisation into TradeSphere** plus exports. Each data domain has one owning system per tenant at any time. Two-way synchronisation is out of scope until conflict ownership is designed. No integration posts stock or money without explicit authorisation and idempotency keys.

## 8. Production infrastructure

```mermaid
flowchart TB
  I(("Internet")) --> CF["Cloudflare DNS and protection"]
  CF -->|"80, 443 only"| C
  subgraph VPS["Ubuntu LTS VPS"]
    subgraph edge["edge network"]
      C["Caddy + static SPA"]
    end
    subgraph internal["internal network, no published ports"]
      API["API x N workers"]
      WK["Worker"]
      MIG["Migrate job, runs once per release"]
      PG[("PostgreSQL 17")]
      BK["Backup agent"]
    end
  end
  OBJ[("Off-site object storage<br/>encrypted backups, files")]
  MON["Error tracking, uptime, metrics, alerts"]
  C --> API
  API --> PG
  WK --> PG
  MIG --> PG
  BK --> PG
  BK --> OBJ
  API --> OBJ
  WK --> OBJ
  API -.-> MON
  WK -.-> MON
  BK -.-> MON
```

Migrations run as a single release step before new application containers start, never from API workers. Running several API instances is safe because sessions, locks, idempotency and numbering all live in PostgreSQL; the login limiter already does. Sizing and runbooks: `deployment-vps-production.md`. The separate demo profile: `deployment-render-free.md`.

## 9. Frontend

Keep React 19, Vite, TanStack Router and Query, and the generated client.

| Area | Change |
|---|---|
| Auth state | Add current tenant and membership list; refresh names the tab's tenant; switching clears the query cache |
| Routes | Public (landing, register, verify, reset, accept invite), onboarding, dealership app, platform admin as a separately loaded bundle |
| New screens | Tenant switcher, invitations, billing, import wizard and history, reconciliation, integrations, global search, product discovery |
| Resilience | Error boundary, not-found page, subscription-state banners, read-only mode |
| Quality | Component and Playwright tests; English and Bangla for every new string |
