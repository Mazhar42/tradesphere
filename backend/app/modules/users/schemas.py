from datetime import datetime
from typing import Annotated

from pydantic import Field, StringConstraints

from app.shared.schemas import ReadSchema, WriteSchema

Username = Annotated[
    str,
    StringConstraints(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.\-]+$", to_lower=True),
]
Password = Annotated[str, Field(min_length=8, max_length=128)]


class RoleRead(ReadSchema):
    id: int
    code: str
    name: str
    description: str | None
    permissions: list[str]


class UserRead(ReadSchema):
    id: int
    username: str
    full_name: str
    phone: str | None
    is_active: bool
    last_login_at: datetime | None
    roles: list[str]
    created_at: datetime


class UserCreate(WriteSchema):
    username: Username
    full_name: Annotated[str, Field(min_length=1, max_length=120)]
    phone: Annotated[str | None, Field(max_length=30)] = None
    password: Password
    roles: Annotated[list[str], Field(min_length=1)]


class UserUpdate(WriteSchema):
    full_name: Annotated[str | None, Field(min_length=1, max_length=120)] = None
    phone: Annotated[str | None, Field(max_length=30)] = None
    is_active: bool | None = None
    roles: Annotated[list[str] | None, Field(min_length=1)] = None


class PasswordReset(WriteSchema):
    new_password: Password


Email = Annotated[
    str,
    StringConstraints(max_length=150, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", to_lower=True),
]


class InvitationCreate(WriteSchema):
    email: Email | None = None
    note: Annotated[str | None, Field(max_length=120)] = None  # who it is for, e.g. a name
    roles: Annotated[list[str], Field(min_length=1)]


class InvitationRead(ReadSchema):
    id: int
    email: str | None
    note: str | None
    roles: list[str]
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None


class InvitationCreated(ReadSchema):
    invitation: InvitationRead
    link: str | None  # shown once, only when there is no email address
