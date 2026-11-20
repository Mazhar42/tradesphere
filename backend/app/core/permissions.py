"""Authentication & authorization dependencies."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.core.db import DbSession
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token
from app.modules.users.models import User

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    full_name: str
    roles: tuple[str, ...]
    permissions: frozenset[str]

    def has(self, permission: str) -> bool:
        return permission in self.permissions


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    if credentials is None:
        raise UnauthorizedError("Authentication required.", code="not_authenticated")
    user_id = decode_access_token(credentials.credentials)
    # Loaded on every request so deactivation and role changes apply immediately.
    user = db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise UnauthorizedError("User is inactive or no longer exists.", code="inactive_user")
    return CurrentUser(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        roles=tuple(user.role_codes),
        permissions=user.permission_codes,
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
