# Security and data protection

Verification target: [OWASP ASVS 5.0.0](https://asvs.owasp.org/) Level 2, plus the OWASP API Security Top 10 (2023). **No compliance or certification is claimed.** Current gaps are in `current-state-audit.md`.

## 1. Assets, actors, boundaries

**Assets:** dealership money and stock records, customer and supplier details, credentials and MFA secrets, backups, billing records, uploaded files.

**Actors:** anonymous visitor · dealership user (by role) · a malicious user of another tenant · platform operator · integration with an API key · payment provider · someone holding a stolen device, token or backup.

**Boundaries:** internet → Cloudflare → Caddy → API → PostgreSQL; worker → database, storage, external hosts; operator → platform routes; server → off-site storage.

## 2. Threats

| # | Threat | Controls | Test |
|---|---|---|---|
| T1 | Reading or writing another tenant's data by id | Three layers (ADR-003) | Isolation suite |
| T2 | Cross-tenant reference through a foreign key | Composite keys | Schema test, negative inserts |
| T3 | Missing tenant context in a job, CLI or report | Fail-closed policy; context required | Jobs without context error |
| T4 | Isolation silently off because of a privileged role | App refuses to start in production if its role is superuser or can bypass RLS | Startup test |
| T5 | Account takeover by password guessing | Rate limit, breached-password rejection, MFA | Tests |
| T6 | Tenant owner takes over a shared user | Reset restrictions (ADR-004) | Negative test |
| T7 | Stolen refresh token | Rotation, family revocation, TS-15 fix, session list and revoke | Tests |
| T8 | Forged client IP or headers | Trusted proxy list (TS-04) | Header test |
| T9 | Privilege escalation through roles | Only holders of `users:write` assign; cannot grant permissions they lack; last-owner rule | Tests |
| T10 | Malicious upload | `data-import-and-coexistence.md` §13 | Hostile fixtures |
| T11 | Formula injection in exports | Escape leading `= + - @` in CSV; XLSX writes text as inline strings | Tests |
| T12 | SSRF through integrations | Address blocking, allow-list | Tests |
| T13 | Forged payment notification | Server-side confirmation, amount check | Tests |
| T14 | Insider with platform access | No business-table grant; grants with approval; audit | Role test |
| T15 | Duplicate or replayed financial request | Idempotency keys per tenant and user | Exists; extend |
| T16 | Script injection stealing the session | React escaping, CSP; access token only in memory | Header and E2E tests |
| T17 | Stolen backup | Client-side encryption, separate credentials | Restore drill |
| T18 | Ransom or deletion of backups | Object lock or versioning; server credentials cannot delete history | Attempted delete fails |
| T19 | Resource exhaustion | Body, row, page and time limits; per-tenant job concurrency | Load and abuse tests |
| T20 | Vulnerable dependency or image | Scanning in CI, pinned versions | CI |

Review this table when a phase adds an entry point.

## 3. Authentication

| Topic | Design |
|---|---|
| Passwords | Argon2id (exists). Minimum 10 characters for owners and operators, 8 otherwise; reject common and breached passwords from a local list; no composition rules |
| Verification | Email link, single use, 24 h. Staff without email: owner-created username, invitation shared as a link (single use, 72 h) |
| Recovery | Email reset, 30 minutes, single use, revokes sessions; same response whether the account exists |
| Owner lockout | Second owner, recovery codes, or a documented platform identity check with a delay and notification |
| MFA | TOTP. Mandatory for operators; required for owners at activation. Secrets encrypted with a key outside the database; 10 hashed recovery codes; replayed codes refused |
| Step-up | Re-enter MFA for: user and role changes, period lock, support approval, API keys, billing changes, deletion |
| Access token | 15 min, with `sub`, `tid`, `aud`, `iss`, `jti`; algorithm pinned |
| Refresh | Opaque, hashed, rotated; `HttpOnly`, `Secure`, `SameSite=Strict`; grace only while the family is alive |
| Sessions | User can list and revoke; idle expiry 14 days, absolute 90 days |

## 4. Authorisation

RBAC remains the base (30 permissions, per-tenant roles). It is sufficient except for these attribute rules, which live in services:

| Rule | Status |
|---|---|
| Cost and profit visibility | Exists (`reports:profit`) |
| Selling at another price | Exists (`sales:price_override`) |
| Period lock | Exists |
| Cash refund above a tenant limit needs a second permission | New, optional |
| Approve an import or enable posting for an integration | New permissions `imports:approve`, `integrations:manage` |
| Tenant status and entitlement | New |

New permissions are added to the catalog and system roles only. A route-enumeration test fails if any non-public route lacks a permission dependency.

## 5. Web and API controls

| Control | Design |
|---|---|
| CSRF | Cookie is used only by refresh and logout, is `SameSite=Strict`, and those routes also check `Origin`. All else needs a bearer token |
| CORS | Exact origins only |
| Headers | Existing four, plus `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'` and `Permissions-Policy`. Run report-only first; the invoice print page has inline styles |
| Request limits | Caddy `request_body max_size` (1 MB; 12 MB on upload routes); server timeouts |
| Rate limits | Database-backed for login, registration, reset, invitation and API keys; keyed on account **and** IP. Coarse per-IP limits at Cloudflare |
| Injection | Parameterised SQLAlchemy; raw SQL reviewed; escape `LIKE` wildcards |
| Validation | Pydantic on input; reject unknown fields on write schemas |
| Errors | No stack traces or constraint names in production (exists) |
| IDs | Sequential ids stay; access control, not secrecy, protects them. Note they reveal platform-wide volume |

## 6. Secrets and data

- Separate secrets per environment; never in git; files readable only by the service user; rotation procedure for the JWT key (accept old and new briefly), database passwords, encryption key.
- Secret scanning in CI.
- Encrypt in application: MFA secrets, integration credentials.
- TLS everywhere outside the private Docker network; Cloudflare mode "Full (strict)".
- Logs: ids only; never passwords, tokens, amounts, phone numbers or file contents.
- Audit: existing tenant audit, platform audit, auth events; all append-only; export for the tenant; retention to be decided.

**Data protection law.** Bangladesh issued a Personal Data Protection Ordinance in November 2025, later amended; reports say the broad data-localisation requirement was removed, and one source says a 2026 Act replaced the ordinance. **I could not confirm the current law from an official source.** Obtain legal advice before commercial launch on: lawful basis, hosting location, cross-border transfer, breach notification, deletion rights, and financial record retention. Choose hosting after that advice.

## 7. OWASP API Top 10 (2023)

| Risk | Main answer |
|---|---|
| API1 Object-level authorisation | RLS, composite keys, isolation suite |
| API2 Authentication | §3 |
| API3 Object property authorisation | Explicit response schemas; cost fields hidden by permission; strict write schemas |
| API4 Resource consumption | §5 limits; jobs for heavy work (including reconcile, TS-24) |
| API5 Function-level authorisation | Route enumeration test; separate platform routes |
| API6 Sensitive business flows | Registration, invitation and reset limits; refund permissions |
| API7 SSRF | T12 |
| API8 Misconfiguration | Production config guard; header and container checks |
| API9 Inventory management | Docs off in production; OpenAPI diff in CI |
| API10 Unsafe consumption | Provider adapters validate, time out and distrust responses |

## 8. ASVS mapping

Create `docs/security/asvs-5.0-l2.md` in the hardening phase: every Level 2 requirement with status, evidence and test. Chapters expected to apply: encoding and sanitisation, validation and business logic, web frontend, API, file handling, authentication, session management, authorisation, tokens, cryptography, secure communication, configuration, data protection, secure coding, logging and error handling. OAuth and WebRTC are not used. Chapter list is from memory: confirm against the standard.

## 9. Security testing

| When | What |
|---|---|
| Every PR | Isolation suite, permission tests, linting (`ruff` security rules, already on), dependency audit, secret scan |
| Every release | Image scan, header check, authenticated dynamic scan on staging |
| Before launch, then yearly | Independent penetration test covering tenancy, authentication and billing |

## 10. Incident response

Detect (alert, report) → assess severity and affected tenants → contain (revoke sessions, rotate secrets, suspend an integration or tenant) → preserve logs → recover → notify affected dealerships, and authorities if law requires → written review with actions. Name an on-call person and a backup before the pilot.

## 11. Acceptance

No open Critical or High finding; isolation suite green as the app role; MFA enforced; every threat above has a passing test or recorded accepted risk; independent test done.
