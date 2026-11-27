from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.core.security import hash_password
from app.modules.users.models import RefreshToken, Role, User
from app.modules.users.schemas import RoleRead, UserCreate, UserRead, UserUpdate
from app.shared.audit import record_audit, snapshot
from app.shared.dates import now_utc
from app.shared.pagination import PageParams, paginate


def to_read(user: User) -> UserRead:
    return UserRead(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        phone=user.phone,
        is_active=user.is_active,
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


def list_users(
    session: Session, params: PageParams, q: str | None, is_active: bool | None
) -> tuple[Sequence[User], int]:
    stmt = select(User).order_by(User.username)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(User.username.ilike(like), User.full_name.ilike(like)))
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    return paginate(session, stmt, params)


def get_user(session: Session, user_id: int) -> User:
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
        select(func.count(func.distinct(User.id)))
        .join(User.roles)
        .where(Role.code == "owner", User.is_active.is_(True))
    )
    if excluding_user_id is not None:
        stmt = stmt.where(User.id != excluding_user_id)
    return session.scalar(stmt) or 0


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
        roles=_resolve_roles(session, data.roles),
    )
    session.add(user)
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
    before = snapshot(user, extra={"roles": user.role_codes})
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
        user.roles = _resolve_roles(session, changes.pop("roles"))
    changes.pop("roles", None)
    for field, value in changes.items():
        setattr(user, field, value)
    if changes.get("is_active") is False:
        revoke_all_tokens(session, user.id)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="update",
        entity_type="user",
        entity_id=user.id,
        before=before,
        after=snapshot(user, extra={"roles": user.role_codes}),
    )
    return user


def reset_password(session: Session, user_id: int, new_password: str, actor: CurrentUser) -> None:
    user = get_user(session, user_id)
    user.password_hash = hash_password(new_password)
    revoke_all_tokens(session, user.id)
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
