from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.db import DbSession
from app.core.permissions import STEP_UP, CurrentUser, require
from app.modules.users import service
from app.modules.users.schemas import PasswordReset, RoleRead, UserCreate, UserRead, UserUpdate
from app.shared.pagination import PageParamsDep
from app.shared.schemas import Page

router = APIRouter(tags=["users"])

CanRead = Annotated[CurrentUser, Depends(require("users:read"))]
CanWrite = Annotated[CurrentUser, Depends(require("users:write"))]


@router.get("/roles", response_model=list[RoleRead])
def list_roles(db: DbSession, _: CanRead) -> list[RoleRead]:
    return [service.role_to_read(r) for r in service.list_roles(db)]


@router.get("/users", response_model=Page[UserRead])
def list_users(
    db: DbSession,
    _: CanRead,
    page: PageParamsDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    is_active: bool | None = None,
) -> Page[UserRead]:
    items, total = service.list_users(db, page, q, is_active)
    return Page(
        items=[service.to_read(db, u) for u in items],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post(
    "/users", response_model=UserRead, status_code=status.HTTP_201_CREATED, dependencies=STEP_UP
)
def create_user(db: DbSession, actor: CanWrite, data: UserCreate) -> UserRead:
    user = service.create_user(db, data, actor)
    db.commit()
    return service.to_read(db, user)


@router.get("/users/{user_id}", response_model=UserRead)
def get_user(db: DbSession, _: CanRead, user_id: int) -> UserRead:
    return service.to_read(db, service.get_user(db, user_id))


@router.patch("/users/{user_id}", response_model=UserRead, dependencies=STEP_UP)
def update_user(db: DbSession, actor: CanWrite, user_id: int, data: UserUpdate) -> UserRead:
    user = service.update_user(db, user_id, data, actor)
    db.commit()
    return service.to_read(db, user)


@router.post(
    "/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT, dependencies=STEP_UP
)
def reset_password(db: DbSession, actor: CanWrite, user_id: int, data: PasswordReset) -> None:
    service.reset_password(db, user_id, data.new_password, actor)
    db.commit()
