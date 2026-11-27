"""First-owner bootstrap for hosts without a shell (for example a free PaaS instance).

Creates the owner account from environment variables, but only while no active owner exists.
Once an owner is in the database the variables are ignored, so the start command can run this on
every start. Remove the variables afterwards to keep the password out of the host's settings.
"""

from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.tenant_context import set_context
from app.modules.tenancy import service as tenancy
from app.modules.users.schemas import UserCreate
from app.modules.users.service import count_active_owners, create_user, get_by_username
from app.seed import provision_tenant

DEFAULT_SLUG = "default"


def ensure_tenant(platform: Session, session: Session) -> int:
    """The one dealership of a single-business installation, created if there is none."""
    tenants = tenancy.all_tenants(platform)
    if len(tenants) > 1:
        raise ConflictError("There are several dealerships; name one.", code="tenant_ambiguous")
    if tenants:
        set_context(session, tenant_id=tenants[0].id)
        return tenants[0].id
    return provision_tenant(session, slug=DEFAULT_SLUG, legal_name="My Dealership").id


def ensure_owner(session: Session, *, username: str, full_name: str, password: str) -> bool:
    """Create the owner if the session's dealership has none. True when a user was created."""
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
