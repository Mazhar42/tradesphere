"""Authentication & authorization dependencies."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import DbSession
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import AccessClaims, decode_access_token
from app.core.tenant_context import read_only, set_context
from app.modules.billing.entitlements import check_access
from app.modules.integrations.ownership import check_ownership
from app.modules.platform.models import SupportGrant
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.service import business_config, enter
from app.modules.users.models import MfaFactor, User
from app.modules.users.permissions_catalog import PERMISSIONS
from app.shared.audit import record_audit
from app.shared.business import use_business
from app.shared.dates import now_utc

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    full_name: str
    roles: tuple[str, ...]
    permissions: frozenset[str]
    tenant_id: int | None = None
    mfa_enrolled: bool = False
    mfa_at: float | None = None

    @property
    def must_enrol_mfa(self) -> bool:
        return settings.owner_mfa_required and "owner" in self.roles and not self.mfa_enrolled

    def has(self, permission: str) -> bool:
        return permission in self.permissions


SUPPORT_ROLE = "support"
READ_ONLY_METHODS = ("GET", "HEAD", "OPTIONS")


def _support_identity(db: Session, claims: AccessClaims, request: Request) -> CurrentUser:
    """An operator reading a dealership under its owner's grant: logged, and unable to write."""
    if request.method not in READ_ONLY_METHODS:
        raise ForbiddenError("Support access is read-only.", code="support_read_only")
    set_context(db, tenant_id=claims.tenant_id, user_id=claims.user_id)
    grant = db.get(SupportGrant, claims.support_grant, populate_existing=True)
    if (
        grant is None
        or grant.tenant_id != claims.tenant_id
        or grant.operator_id != claims.user_id
        or grant.revoked_at is not None
        or grant.expires_at <= now_utc()
        or not db.scalar(text("SELECT app_is_operator(:u)"), {"u": claims.user_id})
    ):
        raise ForbiddenError("This support access has ended.", code="support_ended")
    tenant = db.get(Tenant, claims.tenant_id, populate_existing=True)
    user = db.get(User, claims.user_id)
    assert tenant is not None and user is not None
    use_business(business_config(tenant))
    details = {"grant_id": grant.id, "method": request.method, "path": request.url.path}
    # Both the dealership and the platform see every request.
    record_audit(
        db,
        actor_id=None,
        action="support_access",
        entity_type="support_grant",
        entity_id=grant.id,
        after={**details, "operator": user.username},
    )
    db.execute(
        text(
            "INSERT INTO platform_audit_logs (operator_id, action, tenant_id, details) "
            "VALUES (:o, 'support_request', :t, cast(:d AS jsonb))"
        ),
        {"o": user.id, "t": claims.tenant_id, "d": json.dumps(details)},
    )
    db.commit()
    read_only(db)
    return CurrentUser(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        roles=(SUPPORT_ROLE,),
        permissions=frozenset(p for p in PERMISSIONS if p.endswith(":read")),
        tenant_id=claims.tenant_id,
    )


def get_identity(
    db: DbSession,
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    """Order matters: identity, then membership and dealership status, then permissions.
    All of it is read on every request, so deactivation and role changes apply at once."""
    if credentials is None:
        raise UnauthorizedError("Authentication required.", code="not_authenticated")
    claims = decode_access_token(credentials.credentials)
    if claims.support_grant is not None:
        return _support_identity(db, claims, request)
    set_context(db, user_id=claims.user_id)
    user = db.get(User, claims.user_id, populate_existing=True)
    if user is None or not user.is_active:
        raise UnauthorizedError("User is inactive or no longer exists.", code="inactive_user")
    enter(db, user.id, claims.tenant_id)
    db.expire(user, ["roles"])  # load this dealership's roles, now that it is the context
    return CurrentUser(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        roles=tuple(user.role_codes),
        permissions=user.permission_codes,
        tenant_id=claims.tenant_id,
        mfa_enrolled=_enrolled(db, user.id),
        mfa_at=claims.mfa_at,
    )


def _enrolled(db: Session, user_id: int) -> bool:
    factor = db.get(MfaFactor, user_id, populate_existing=True)
    return factor is not None and factor.confirmed_at is not None


# For the few routes a person needs before they have set up a required second factor.
IdentityDep = Annotated[CurrentUser, Depends(get_identity)]


def get_current_user(user: IdentityDep, db: DbSession, request: Request) -> CurrentUser:
    """Identity and membership (above), then enrolment and the subscription's state. Permissions
    are checked by `require`, plan features and limits where they apply."""
    if user.must_enrol_mfa:
        raise ForbiddenError("Set up two-step sign-in to continue.", code="mfa_enrolment_required")
    if SUPPORT_ROLE not in user.roles:
        check_access(db, request.method, request.url.path)
        check_ownership(db, request.method, request.url.path)
    return user


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_step_up(user: CurrentUserDep) -> CurrentUser:
    """For changes that hand out access or move the books' boundaries: a person with a second
    factor must have shown it recently."""
    recent = user.mfa_at is not None and (
        now_utc().timestamp() - user.mfa_at <= settings.step_up_minutes * 60
    )
    if user.mfa_enrolled and not recent:
        raise ForbiddenError("Confirm with your authenticator code.", code="step_up_required")
    return user


STEP_UP = [Depends(require_step_up)]


def require(*permissions: str) -> Callable[[CurrentUser], CurrentUser]:
    """Dependency factory: the current user must hold *all* listed permissions."""

    def checker(user: CurrentUserDep) -> CurrentUser:
        missing = [p for p in permissions if not user.has(p)]
        if missing:
            raise ForbiddenError(
                f"You do not have permission to perform this action ({', '.join(missing)}).",
                code="permission_denied",
            )
        return user

    return checker
