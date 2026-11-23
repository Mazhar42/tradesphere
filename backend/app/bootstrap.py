"""First-owner bootstrap for hosts without a shell (for example a free PaaS instance).

Creates the owner account from environment variables, but only while no active owner exists.
Once an owner is in the database the variables are ignored, so the start command can run this on
every start. Remove the variables afterwards to keep the password out of the host's settings.
"""

from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.modules.users.schemas import UserCreate
from app.modules.users.service import count_active_owners, create_user, get_by_username


def ensure_owner(session: Session, *, username: str, full_name: str, password: str) -> bool:
    """Create the owner if there is none. Returns True when a user was created."""
    if count_active_owners(session) > 0:
        return False
    if get_by_username(session, username) is not None:
        raise ConflictError(
            f"'{username}' exists but is not an active owner. Choose another username.",
            code="bootstrap_username_taken",
        )
    create_user(
        session,
        UserCreate(username=username, full_name=full_name, password=password, roles=["owner"]),
        actor=None,
    )
    return True
