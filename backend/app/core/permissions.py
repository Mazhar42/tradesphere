"""Authentication & authorization dependencies."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.db import DbSession
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token
from app.core.tenant_context import set_context
from app.modules.tenancy.service import enter
from app.modules.users.models import User

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    full_name: str
    roles: tuple[str, ...]
    permissions: frozenset[str]
    tenant_id: int | None = None

    def has(self, permission: str) -> bool:
        return permission in self.permissions


def get_current_user(
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
    )


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


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
