from collections.abc import Sequence

from sqlalchemy import Select, and_, func, not_, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import BusinessRuleError, ConflictError, ForbiddenError, NotFoundError
from app.core.permissions import CurrentUser
from app.core.security import hash_password
from app.core.tenant_context import tenant_id_of
from app.modules.tenancy import service as tenancy
from app.modules.tenancy.models import TenantMembership
from app.modules.users.models import RefreshToken, Role, User
from app.modules.users.schemas import RoleRead, UserCreate, UserRead, UserUpdate
from app.shared.audit import record_audit, snapshot
from app.shared.dates import now_utc
from app.shared.pagination import PageParams, paginate


def to_read(session: Session, user: User) -> UserRead:
    """`is_active` is about this dealership: a person switched off here may work elsewhere."""
    member = tenancy.membership(session, user.id)
    return UserRead(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        phone=user.phone,
        is_active=user.is_active and member.status == "active",
        last_login_at=user.last_login_at,
        roles=user.role_codes,
        created_at=user.created_at,
    )


def role_to_read(role: Role) -> RoleRead:
    return RoleRead(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        permissions=[p.code for p in role.permissions],
    )


def list_roles(session: Session) -> Sequence[Role]:
    return session.scalars(select(Role).order_by(Role.id)).all()


def _members(session: Session) -> Select[User]:
    """The people of the current dealership. `users` itself is shared by all of them."""
    return select(User).join(
        TenantMembership,
        and_(
            TenantMembership.user_id == User.id,
            TenantMembership.tenant_id == tenant_id_of(session),
        ),
    )


def list_users(
    session: Session, params: PageParams, q: str | None, is_active: bool | None
) -> tuple[Sequence[User], int]:
    stmt = _members(session).options(selectinload(User.roles)).order_by(User.username)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(User.username.ilike(like), User.full_name.ilike(like)))
    if is_active is not None:
        active = and_(User.is_active.is_(True), TenantMembership.status == "active")
        stmt = stmt.where(active if is_active else not_(active))
    return paginate(session, stmt, params)


def get_user(session: Session, user_id: int) -> User:
    tenancy.membership(session, user_id)  # 404 for people of other dealerships
    user = session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")
    return user


def get_by_username(session: Session, username: str) -> User | None:
    return session.scalar(select(User).where(func.lower(User.username) == username.lower()))


def _resolve_roles(session: Session, codes: list[str]) -> list[Role]:
    roles = list(session.scalars(select(Role).where(Role.code.in_(codes))).all())
    unknown = set(codes) - {r.code for r in roles}
    if unknown:
        raise BusinessRuleError(
            "Unknown role.",
            code="unknown_role",
            errors=[
                {"field": "roles", "message": f"Unknown role(s): {', '.join(sorted(unknown))}"}
            ],
        )
    return roles


def _active_owner_count(session: Session, excluding_user_id: int | None = None) -> int:
    stmt = (
        _members(session)
        .join(User.roles)
        .where(
            Role.code == "owner",
            User.is_active.is_(True),
            TenantMembership.status == "active",
        )
        .with_only_columns(func.count(func.distinct(User.id)))
    )
    if excluding_user_id is not None:
        stmt = stmt.where(User.id != excluding_user_id)
    return session.scalar(stmt) or 0


# The passwords tried first by anyone guessing. Short list on purpose: length does the rest.
COMMON_PASSWORDS = frozenset(
    {
        "password", "password1", "password123", "passw0rd", "12345678", "123456789",
        "1234567890", "qwerty123", "qwertyuiop", "11111111", "admin123", "welcome1",
        "iloveyou", "abc12345", "bangladesh", "bismillah", "letmein123", "tradesphere",
    }
)  # fmt: skip
PRIVILEGED_MINIMUM = 10


def check_password(password: str, *, privileged: bool) -> None:
    problem = None
    if password.lower() in COMMON_PASSWORDS:
        problem = "This password is too common."
    elif privileged and len(password) < PRIVILEGED_MINIMUM:
        problem = f"Owners need at least {PRIVILEGED_MINIMUM} characters."
    if problem:
        raise BusinessRuleError(
            problem, code="weak_password", errors=[{"field": "password", "message": problem}]
        )


def set_password(session: Session, user: User, new_password: str) -> None:
    """Change a password and end every session of that person."""
    check_password(new_password, privileged=False)
    user.password_hash = hash_password(new_password)
    revoke_all_tokens(session, user.id)


def get_by_email(session: Session, address: str) -> User | None:
    return session.scalar(select(User).where(func.lower(User.email) == address.lower()))


def ensure_may_grant(session: Session, actor: CurrentUser, role_codes: list[str]) -> None:
    """Nobody hands out a permission they do not hold themselves."""
    granted = {p.code for role in _resolve_roles(session, role_codes) for p in role.permissions}
    if granted - actor.permissions:
        raise ForbiddenError("You cannot give permissions you do not have.", code="cannot_grant")


def assign_roles(session: Session, user: User, role_codes: list[str]) -> None:
    """Set a member's roles in the current dealership."""
    session.expire(user, ["roles"])
    user.roles = _resolve_roles(session, role_codes)
    session.flush()


def create_user(session: Session, data: UserCreate, actor: CurrentUser | None) -> User:
    if get_by_username(session, data.username):
        raise ConflictError(
            "Username already taken.",
            code="duplicate",
            errors=[{"field": "username", "message": "Already exists"}],
        )
    user = User(
        username=data.username,
        full_name=data.full_name,
        phone=data.phone,
        password_hash=hash_password(data.password),
    )
    check_password(data.password, privileged="owner" in data.roles)
    if actor is not None:
        ensure_may_grant(session, actor, data.roles)
    roles = _resolve_roles(session, data.roles)
    session.add(user)
    session.flush()
    # Membership first: roles are held by a member of the dealership.
    tenancy.add_member(session, user.id, managed=True, invited_by=actor.id if actor else None)
    user.roles = roles
    session.flush()
    record_audit(
        session,
        actor_id=actor.id if actor else None,
        action="create",
        entity_type="user",
        entity_id=user.id,
        after=snapshot(user, extra={"roles": user.role_codes}),
    )
    return user


def update_user(session: Session, user_id: int, data: UserUpdate, actor: CurrentUser) -> User:
    user = get_user(session, user_id)
    member = tenancy.membership(session, user_id)
    before = snapshot(user, extra={"roles": user.role_codes, "membership": member.status})
    changes = data.model_dump(exclude_unset=True)

    losing_owner = (
        changes.get("is_active") is False
        or (changes.get("roles") is not None and "owner" not in changes["roles"])
    ) and "owner" in user.role_codes
    if losing_owner and _active_owner_count(session, excluding_user_id=user.id) == 0:
        raise BusinessRuleError("At least one active owner must remain.", code="last_owner")
    if user.id == actor.id and changes.get("is_active") is False:
        raise BusinessRuleError("You cannot deactivate your own account.", code="self_deactivate")

    if "roles" in changes and changes["roles"] is not None:
        ensure_may_grant(session, actor, changes["roles"])
        user.roles = _resolve_roles(session, changes.pop("roles"))
    changes.pop("roles", None)
    is_active = changes.pop("is_active", None)
    for field, value in changes.items():
        if getattr(user, field) != value and not member.managed_by_tenant:
            raise ForbiddenError(
                "This person manages their own profile.", code="profile_not_managed"
            )
        setattr(user, field, value)
    if is_active is not None:
        member.status = "active" if is_active else "inactive"
    session.flush()
    if is_active is False and tenancy.active_membership_count(session, user.id) == 0:
        revoke_all_tokens(session, user.id)
    record_audit(
        session,
        actor_id=actor.id,
        action="update",
        entity_type="user",
        entity_id=user.id,
        before=before,
        after=snapshot(user, extra={"roles": user.role_codes, "membership": member.status}),
    )
    return user


def reset_password(session: Session, user_id: int, new_password: str, actor: CurrentUser) -> None:
    """Only for accounts this dealership created and that belong to no other (TS-09): a shared
    account's password would open the other dealership too."""
    user = get_user(session, user_id)
    member = tenancy.membership(session, user_id)
    if not member.managed_by_tenant or tenancy.membership_count(session, user_id) != 1:
        raise ForbiddenError(
            "This person manages their own password. They can use 'Forgot password'.",
            code="reset_not_allowed",
        )
    set_password(session, user, new_password)
    record_audit(
        session, actor_id=actor.id, action="reset_password", entity_type="user", entity_id=user.id
    )


def lock_sessions(session: Session, user_id: int, *, ending: bool) -> None:
    """Serialise issuing a user's tokens with revoking them, until the transaction ends.

    Refreshes share the lock; anything that ends sessions takes it exclusively, so it sees
    every token a refresh in progress is about to commit. Take it before any token row.
    """
    session.execute(
        select(User.id)
        .where(User.id == user_id)
        .with_for_update(read=not ending, key_share=ending)  # FOR SHARE / FOR NO KEY UPDATE
    )


def revoke_all_tokens(session: Session, user_id: int) -> None:
    lock_sessions(session, user_id, ending=True)
    tokens = session.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
    ).all()
    now = now_utc()
    for token in tokens:
        token.revoked_at = now


def count_active_owners(session: Session) -> int:
    return _active_owner_count(session)
