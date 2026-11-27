"""Authentication & authorization dependencies."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import DbSession
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token
from app.core.tenant_context import set_context
from app.modules.tenancy.service import enter
from app.modules.users.models import MfaFactor, User
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


def get_identity(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    """Order matters: identity, then membership and dealership status, then permissions.
    All of it is read on every request, so deactivation and role changes apply at once."""
    if credentials is None:
        raise UnauthorizedError("Authentication required.", code="not_authenticated")
    claims = decode_access_token(credentials.credentials)
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


def get_current_user(user: IdentityDep) -> CurrentUser:
    if user.must_enrol_mfa:
        raise ForbiddenError("Set up two-step sign-in to continue.", code="mfa_enrolment_required")
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
