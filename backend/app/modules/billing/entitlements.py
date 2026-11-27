"""The only place plan data is read by the rest of the application (ADR-009).

Business code asks "may this dealership use X / add N of Y / write at all", never which plan
it has. With `BILLING_ENFORCEMENT` off, or without a subscription, the answer is always yes.
Effective value: an unexpired override, else the plan.
"""

from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.tenant_context import tenant_id_of
from app.modules.billing.models import EntitlementOverride, PlanEntitlement, Subscription
from app.shared.dates import business_date

FEATURES = ("advanced_reports", "data_export", "api_access", "advanced_inventory")
LIMITS = ("max_users", "max_products", "max_invoices_per_month", "max_import_rows", "max_locations")


class SubscriptionRequiredError(AppError):
    status = 402
    code = "subscription_required"
    title = "Subscription required"


@dataclass(frozen=True)
class Value:
    limit: int | None = None  # None: unlimited
    enabled: bool = True


def subscription(session: Session) -> Subscription | None:
    return session.scalar(
        select(Subscription).where(Subscription.tenant_id == tenant_id_of(session))
    )


def value(session: Session, key: str) -> Value:
    if not settings.billing_enforcement:
        return Value()
    current = subscription(session)
    if current is None:
        return Value()
    override = session.scalar(
        select(EntitlementOverride)
        .where(
            EntitlementOverride.tenant_id == tenant_id_of(session),
            EntitlementOverride.key == key,
            (EntitlementOverride.expires_on.is_(None))
            | (EntitlementOverride.expires_on >= business_date()),
        )
        .order_by(EntitlementOverride.id.desc())
        .limit(1)
    )
    if override is not None:
        return Value(override.limit_value, override.enabled is not False)
    granted = session.get(PlanEntitlement, (current.plan_id, key))
    if granted is None:
        return Value()
    return Value(granted.limit_value, granted.enabled is not False)


def require_feature(session: Session, key: str) -> None:
    if not value(session, key).enabled:
        raise SubscriptionRequiredError(
            "Your plan does not include this.", code="feature_not_included"
        )


def require_capacity(session: Session, key: str, used: int, adding: int = 1) -> None:
    """`used` must be counted by the caller **after** this lock would be taken, so call it as
    `require_capacity(session, key, count_after_lock(session), adding)` via `lock`."""
    limit = value(session, key).limit
    if limit is not None and used + adding > limit:
        raise SubscriptionRequiredError(
            f"Your plan allows {limit}. You have {used}.", code="plan_limit_reached"
        )


def lock(session: Session, key: str) -> None:
    """Serialise additions of one kind in one dealership until the transaction ends, so
    parallel requests cannot both take the last place."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key), :tenant)"),
        {"key": f"capacity:{key}", "tenant": tenant_id_of(session) % 2_000_000_000},
    )


# --- What the subscription state allows ----------------------------------------------------------

# Reachable whatever the state: signing in, switching, paying.
ALWAYS_OPEN = ("/api/v1/auth/", "/api/v1/billing")
READS = ("GET", "HEAD", "OPTIONS")


def ensure_writable(session: Session) -> None:
    """Also for background work that changes a dealership's records."""
    if not settings.billing_enforcement:
        return
    current = subscription(session)
    if current is not None and current.status in ("read_only", "suspended"):
        raise SubscriptionRequiredError(
            "The subscription has lapsed: records can be read and exported, not changed."
        )


def check_access(session: Session, method: str, path: str) -> None:
    if not settings.billing_enforcement or path.startswith(ALWAYS_OPEN):
        return
    current = subscription(session)
    if current is None:
        return
    if current.status == "suspended":
        raise SubscriptionRequiredError(
            "The account is suspended for non-payment. Pay to restore access.",
            code="subscription_suspended",
        )
    if current.status == "read_only" and method not in READS:
        ensure_writable(session)
