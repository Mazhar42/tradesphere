"""Running the service: operators, dealerships' status, and the platform audit.

Works on the platform connection, which can see no dealership's business records. Never
imports the business domains.
"""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import mfa_codes
from app.core.context import client_ip_var, request_id_var
from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
)
from app.core.security import create_platform_token, create_support_token, verify_password
from app.modules.auth import mfa
from app.modules.auth.service import TOO_MANY_ATTEMPTS, record_event, sign_in_blocked
from app.modules.jobs.models import Job
from app.modules.platform.models import PlatformAuditLog, PlatformOperator, SupportGrant
from app.modules.tenancy.models import Tenant, TenantMembership
from app.modules.users.models import MfaFactor, MfaRecoveryCode, User
from app.modules.users.service import get_by_username
from app.shared.dates import now_utc

SUPPORT_MINUTES = 15


def audit(
    session: Session,
    action: str,
    *,
    operator_id: int | None,
    tenant_id: int | None = None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        PlatformAuditLog(
            operator_id=operator_id,
            action=action,
            tenant_id=tenant_id,
            reason=reason,
            details=details,
            ip=client_ip_var.get(),
            request_id=request_id_var.get(),
        )
    )


def active_operator(session: Session, user_id: int) -> User:
    operator = session.get(PlatformOperator, user_id, populate_existing=True)
    user = session.get(User, user_id, populate_existing=True)
    if operator is None or operator.status != "active" or user is None or not user.is_active:
        raise UnauthorizedError("Not a platform operator.", code="not_operator")
    return user


def login(session: Session, username: str, password: str, code: str, ip: str | None) -> str:
    """Operators always need a second factor. Every failure looks the same."""
    if sign_in_blocked(session, username, ip):
        raise RateLimitedError(TOO_MANY_ATTEMPTS, code="login_rate_limited")
    user = get_by_username(session, username)
    valid = verify_password(password, user.password_hash if user else None)
    ok = (
        user is not None
        and valid
        and user.is_active
        and session.get(PlatformOperator, user.id) is not None
        and mfa.verify(session, user.id, code)
    )
    if user is not None and ok:
        ok = session.get(PlatformOperator, user.id).status == "active"  # type: ignore[union-attr]
    if user is None or not ok:
        record_event(session, "login_failed", user_id=None, identifier=username.lower())
        session.commit()
        raise UnauthorizedError("Sign-in failed.", code="invalid_credentials")
    record_event(session, "platform_login", user_id=user.id, identifier=user.username)
    audit(session, "login", operator_id=user.id)
    return create_platform_token(user.id)


def add_operator(session: Session, username: str) -> tuple[str | None, list[str]]:
    """From the command line only. Enrols a second factor if the person has none; returns
    the authenticator setup address and recovery codes to hand over, once."""
    user = get_by_username(session, username)
    if user is None:
        raise NotFoundError("No such user. Create the account first.")
    operator = session.get(PlatformOperator, user.id)
    if operator is None:
        session.add(PlatformOperator(user_id=user.id))
    else:
        operator.status = "active"
    session.flush()
    audit(session, "operator_added", operator_id=user.id)
    if mfa.is_enrolled(session, user.id):
        return None, []
    secret = mfa_codes.new_secret()
    factor = session.get(MfaFactor, user.id) or MfaFactor(user_id=user.id, last_step=0)
    factor.secret_encrypted = mfa_codes.encrypt(secret)
    factor.confirmed_at = now_utc()
    session.add(factor)
    codes = mfa_codes.new_recovery_codes()
    session.add_all(
        MfaRecoveryCode(user_id=user.id, code_hash=mfa_codes.hash_recovery_code(c)) for c in codes
    )
    session.flush()
    return mfa_codes.setup_uri(secret, user.username), codes


def tenants(session: Session) -> list[tuple[Tenant, int]]:
    members = (
        select(TenantMembership.tenant_id, func.count().label("members"))
        .where(TenantMembership.status == "active")
        .group_by(TenantMembership.tenant_id)
        .subquery()
    )
    rows = session.execute(
        select(Tenant, func.coalesce(members.c.members, 0))
        .outerjoin(members, members.c.tenant_id == Tenant.id)
        .order_by(Tenant.id)
    ).all()
    return [(tenant, int(count)) for tenant, count in rows]


def set_tenant_status(
    session: Session, tenant_id: int, status: str, reason: str, operator_id: int
) -> Tenant:
    tenant = session.get(Tenant, tenant_id, with_for_update=True)
    if tenant is None:
        raise NotFoundError("Dealership not found.")
    if tenant.status == "deleted":
        raise ConflictError("A deleted dealership cannot change.", code="tenant_deleted")
    before = tenant.status
    tenant.status = status
    session.flush()
    audit(
        session,
        "tenant_status",
        operator_id=operator_id,
        tenant_id=tenant_id,
        reason=reason,
        details={"from": before, "to": status},
    )
    return tenant


def audit_log(session: Session, limit: int = 100) -> list[PlatformAuditLog]:
    return list(
        session.scalars(select(PlatformAuditLog).order_by(PlatformAuditLog.id.desc()).limit(limit))
    )


def my_grants(session: Session, operator_id: int) -> list[SupportGrant]:
    return list(
        session.scalars(
            select(SupportGrant)
            .where(
                SupportGrant.operator_id == operator_id,
                SupportGrant.revoked_at.is_(None),
                SupportGrant.expires_at > now_utc(),
            )
            .order_by(SupportGrant.id)
        )
    )


def support_token(session: Session, grant_id: int, operator_id: int) -> str:
    grant = next((g for g in my_grants(session, operator_id) if g.id == grant_id), None)
    if grant is None:
        raise NotFoundError("No active grant.")
    audit(
        session,
        "support_access",
        operator_id=operator_id,
        tenant_id=grant.tenant_id,
        reason=grant.reason,
        details={"grant_id": grant.id},
    )
    lifetime = min(grant.expires_at - now_utc(), timedelta(minutes=SUPPORT_MINUTES))
    return create_support_token(operator_id, grant.tenant_id, grant.id, lifetime)


Provisioner = Callable[..., Tenant]


def validate_reason(reason: str) -> str:
    if len(reason.strip()) < 5:
        raise BusinessRuleError("Say why.", errors=[{"field": "reason", "message": "Too short"}])
    return reason.strip()


def job_health(session: Session) -> dict[str, Any]:
    counts = dict(session.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    oldest = session.scalar(
        select(func.min(Job.run_at)).where(Job.status == "queued", Job.run_at <= now_utc())
    )
    failures = session.execute(
        select(Job.id, Job.kind, Job.last_error)
        .where(Job.status == "failed")
        .order_by(Job.id.desc())
        .limit(10)
    ).all()
    return {
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "failed": counts.get("failed", 0),
        "oldest_queued_seconds": int((now_utc() - oldest).total_seconds()) if oldest else 0,
        "recent_failures": [f"#{i} {kind}: {error}" for i, kind, error in failures],
    }
