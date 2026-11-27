# Subscriptions, billing and platform administration

Decision: ADR-009 (provider port, manual first). **Plan names, limits and prices are still needed from the business.** Values below are structure, not offers.

## 1. Model

| Table | Content | Rule |
|---|---|---|
| `plans` | code, name, interval (`month`, `year`), price, currency, version, is_public | A used plan's price is never edited; publish a new version |
| `plan_entitlements` | plan_id, key, limit (null = unlimited) or flag | |
| `subscriptions` | tenant, plan, status, trial_ends_at, period_start, period_end, cancel_at_period_end, pending_plan | One live row per tenant |
| `subscription_events` | from, to, reason, actor, at | Append-only |
| `tenant_entitlement_overrides` | key, value, reason, expires_at | Operator only, audited |
| `billing_invoices` | number (platform sequence), tenant, period, lines, amount, status (`open`, `paid`, `void`) | Immutable once issued; void and reissue |
| `billing_payments` | invoice, method, provider, provider_ref, amount, received_at, recorded_by | **unique (provider, provider_ref)** |
| `payment_provider_events` | provider, event_id, payload, processed_at, outcome | unique (provider, event_id) |
| `tenant_usage` | tenant, key, value, as_of | Written by jobs, so platform screens never read business tables |

Platform billing is separate from the dealership's own accounting: no TradeSphere subscription entry is ever posted into a tenant's books.

## 2. Lifecycle

States and diagram: `target-architecture.md` §5. Agreed timings: **14-day trial, 7-day grace, 90 days read-only, then suspension.**

| From → to | Trigger |
|---|---|
| pending → trialing | Contact verified |
| trialing → active | Invoice paid |
| trialing → read_only | Trial end, unpaid |
| active → past_due | Period end, renewal invoice unpaid |
| past_due → active | Paid |
| past_due → read_only | Grace ends |
| read_only → active | Paid |
| read_only → suspended | 90 days |
| active → cancelled → read_only | Owner cancels; applies at period end |
| suspended → deletion_scheduled | Owner request or retention policy |

- Registration creates user and tenant. Activation of a paid plan is a **separate** operation.
- A daily job applies time-driven transitions, idempotently, using the tenant's timezone.
- Reminders at 7 and 1 days before trial or period end, and on each state change.
- Renewal invoice is issued 7 days before period end.

**What each state allows**

| State | Read, report, export | Business writes | Invite users | Billing |
|---|---|---|---|---|
| trialing, active, past_due (banner) | Yes | Yes | Yes | Yes |
| read_only | Yes | **No** (402) | No | Yes |
| suspended | Export on request | No | No | Yes (owner) |

Enforced in one request dependency: every non-`GET` business route checks it; authentication, tenant switching, billing and export are exempt. A route-enumeration test ensures no write route is missed. Background posting (imports, integrations) checks the same function.

## 3. Changes

- **Upgrade:** immediate; a prorated invoice for the rest of the period; new limits apply when issued if the tenant is in good standing.
- **Downgrade:** at period end. If usage exceeds the new limits, show what exceeds; existing data stays usable, new creation is blocked.
- **Yearly ↔ monthly:** at period end.
- **Cancellation:** at period end, reversible until then.
- **Reactivation:** pay an invoice for a new period; no back-charging of the lapsed time.
- **Failed gateway payment:** invoice stays open; the lifecycle above handles it. No automatic retries of charges, because no automatic debit is designed.

## 4. Entitlements

| Key | Kind | When it blocks |
|---|---|---|
| `max_users` | count | Invite or add |
| `max_products` | count | Create or import |
| `max_invoices_per_month` | count | **Warn only.** Never stop a sale at the counter |
| `max_import_rows` | per job | Upload |
| `advanced_reports`, `data_export`, `api_access`, `advanced_inventory` | flag | Route |
| `max_locations` | reserved | Branches are not in the first release |

A dealership can always export its own data when lapsed, whatever `data_export` says.

```python
# billing/entitlements.py — the only place plan data is read
def require_feature(db, tenant_id, key) -> None
def require_capacity(db, tenant_id, key, adding: int = 1) -> None   # locks the subscription row
```

- Routes declare features next to permissions: `require("reports:read")`, `feature("advanced_reports")`.
- No business code compares a plan code. A lint test forbids importing `plans` outside `billing`.
- Capacity checks lock the subscription row, so parallel requests cannot exceed a limit.
- Effective value = override if unexpired, else plan.

## 5. Manual provider (launch)

1. Invoice issued; owner sees it with payment instructions (bank, bKash, Nagad).
2. Owner may submit the transaction reference.
3. Operator confirms against the statement and records method, reference, amount, date.
4. Paid in full → period activates; receipt generated.

Partial payments keep the invoice open. Mistakes are corrected by a reversing record, never edits. Every action is in the platform audit log.

## 6. Provider port

```python
class PaymentProvider(Protocol):
    code: str
    def create_checkout(self, invoice) -> CheckoutRedirect
    def verify_notification(self, headers, body) -> ProviderEvent
    def query(self, provider_ref) -> PaymentStatus
```

The domain knows invoices and payments; adapters know gateways. A fake adapter drives tests.

**Webhook handling**
1. Store the raw event (unique per provider) and answer quickly.
2. In a job, **confirm with the provider's server API**; never trust the notification alone.
3. Check transaction id, amount, currency and invoice state.
4. Insert the payment (unique reference) and apply the transition in one transaction.
5. Duplicates and out-of-order events change nothing.
6. A daily job queries invoices left pending.

## 7. Gateways

Checked 2026-11-27. Fees, onboarding requirements and settlement terms were **not** researched.

| Provider | Finding | Use |
|---|---|---|
| [SSLCOMMERZ v4](https://developer.sslcommerz.com/doc/v4/) | Hosted checkout; IPN to a public URL; the documentation requires calling the Order Validation API and checking amount and currency. Recurring billing **not documented**. How to verify the notification signature: not stated. Page last updated 2019 | **First candidate.** Pay-per-invoice, no auto-debit |
| [bKash](https://developer.bka.sh/) | Tokenized checkout with agreements appears in community libraries; official recurring details **not verified** | Evaluate with bKash merchant onboarding |
| ShurjoPay, Nagad and others | Not researched | Compare fees before committing |
| [Stripe](https://stripe.com/global) | Bangladesh is not in the supported list | Not for a Bangladesh-registered seller |
| [Paddle](https://developer.paddle.com/concepts/sell/supported-countries-locales) | Bangladesh buyers listed, in USD; BDT not seen in the currency list (not all rows read); seller eligibility not stated | Possible for international sales; confirm with Paddle |

Local and international are separate adapters. International expansion also needs multi-currency plans and tax handling, neither of which is designed.

## 8. Platform administration

Separate routes, separate database role (no access to business tables), mandatory MFA (ADR-004).

| Area | Actions |
|---|---|
| Dealerships | List, view profile and usage, create, suspend, reactivate, schedule deletion |
| Subscriptions and plans | View, change, extend trial, overrides, publish plan versions |
| Billing | Invoices, record and reverse payments, provider events |
| Health | Job queue, failures, integration health, error rate, backup status |
| Support | `support_requests` (tenant, subject, messages, status) |
| Configuration | Signup on/off, default trial, announcement banner |
| Support access | Request a grant → owner approves (scope, expiry ≤ 72 h) → read-only, audited on both sides (ADR-005) |

Roles: `platform_admin`, `billing_operator`, `support`. No operator can see financial records without a grant.

## 9. Tenant deletion

Request (owner, with MFA) → 30-day waiting period, cancellable → export offered → a reviewed operator procedure removes tenant rows in dependency order, then files. Disabling the append-only triggers needs elevated privilege, so it is not an application or job role and never runs from a web request. Platform invoices are retained. **Retention periods and legal duties for financial records need legal advice before this is built.** Backups age out on their own schedule; say so to the customer.

## 10. Tests and acceptance

- Every transition, with a controllable clock, including timezone edges and repeated job runs.
- Each state's allow/deny table, enumerated over all routes.
- Parallel creation cannot exceed a limit.
- Duplicate, forged, wrong-amount and out-of-order notifications change nothing.
- Invoices and payments reconcile; plan price changes do not alter issued invoices.
- **Accepted when** a pilot tenant completes trial → invoice → payment → active → lapse → read-only → reactivation.
