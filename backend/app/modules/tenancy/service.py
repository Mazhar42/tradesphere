"""Dealerships and memberships. Must not import the domain modules."""

import re

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.core.tenant_context import set_context, tenant_id_of
from app.modules.tenancy.models import Tenant, TenantMembership
from app.shared.business import BusinessConfig, default_config, use_business

# A suspended or deleted dealership can no longer be opened at all.
CLOSED_STATUSES = ("suspended", "deleted")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$")


def business_config(tenant: Tenant) -> BusinessConfig:
    return BusinessConfig(tenant.timezone, tenant.fiscal_year_start_month, tenant.currency_code)


def create_tenant(
    session: Session, *, slug: str, legal_name: str, config: BusinessConfig | None = None
) -> Tenant:
    """Insert the dealership row and make it the session's context. It has no data yet."""
    if not SLUG.match(slug):
        raise BusinessRuleError(
            "Use 3 to 50 lower-case letters, digits and hyphens.",
            code="invalid_slug",
            errors=[{"field": "slug", "message": "Invalid"}],
        )
    config = config or default_config()
    if not settings.allow_multiple_tenants and session.scalar(text("SELECT app_has_tenants()")):
        raise ConflictError(
            "This installation serves one dealership. Set ALLOW_MULTIPLE_TENANTS to add more.",
            code="multiple_tenants_disabled",
        )
    # Take the id first: row-level security only lets a session insert its own tenant.
    tenant_id = session.scalar(text("SELECT nextval(pg_get_serial_sequence('tenants', 'id'))"))
    assert tenant_id is not None
    set_context(session, tenant_id=tenant_id)
    use_business(config)
    tenant = Tenant(
        id=tenant_id,
        slug=slug,
        legal_name=legal_name,
        timezone=config.timezone,
        currency_code=config.currency_code,
        fiscal_year_start_month=config.fiscal_year_start_month,
    )
    session.add(tenant)
    session.flush()
    return tenant


def memberships_of(session: Session, user_id: int) -> list[tuple[Tenant, TenantMembership]]:
    """The dealerships a person can open, oldest membership first."""
    set_context(session, user_id=user_id)
    rows = session.execute(
        select(Tenant, TenantMembership)
        .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
        .where(
            TenantMembership.user_id == user_id,
            TenantMembership.status == "active",
            Tenant.status.not_in(CLOSED_STATUSES),
        )
        .order_by(TenantMembership.joined_at, Tenant.id)
    ).all()
    return [(tenant, membership) for tenant, membership in rows]


def enter(session: Session, user_id: int, tenant_id: int) -> Tenant:
    """Verify that the person belongs to the dealership and make it the session's context."""
    set_context(session, user_id=user_id)
    membership = session.get(TenantMembership, (tenant_id, user_id), populate_existing=True)
    if membership is None or membership.status != "active":
        raise ForbiddenError(
            "You no longer have access to this dealership.", code="membership_inactive"
        )
    tenant = session.get(Tenant, tenant_id, populate_existing=True)
    if tenant is None or tenant.status in CLOSED_STATUSES:
        raise ForbiddenError("This dealership is not available.", code="tenant_unavailable")
    set_context(session, tenant_id=tenant_id)
    use_business(business_config(tenant))
    return tenant


def membership(session: Session, user_id: int) -> TenantMembership:
    """A person's membership of the current dealership; 404 for anyone else's people."""
    row = session.get(TenantMembership, (tenant_id_of(session), user_id))
    if row is None:
        raise NotFoundError("User not found.")
    return row


def add_member(
    session: Session, user_id: int, *, managed: bool, invited_by: int | None = None
) -> TenantMembership:
    row = TenantMembership(
        tenant_id=tenant_id_of(session),
        user_id=user_id,
        status="active",
        managed_by_tenant=managed,
        invited_by_id=invited_by,
    )
    session.add(row)
    session.flush()
    return row


def tenant_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Tenant)) or 0


def all_tenants(session: Session) -> list[Tenant]:
    """Only meaningful on a platform connection: row-level security hides the others."""
    return list(session.scalars(select(Tenant).order_by(Tenant.id)))
