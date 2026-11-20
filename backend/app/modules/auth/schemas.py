from typing import Annotated, Literal

from pydantic import Field

from app.shared.schemas import ReadSchema, WriteSchema


class LoginRequest(WriteSchema):
    username: Annotated[str, Field(min_length=1, max_length=50)]
    password: Annotated[str, Field(min_length=1, max_length=128)]


class MeRead(ReadSchema):
    id: int
    username: str
    full_name: str
    roles: list[str]
    permissions: list[str]


class TokenResponse(ReadSchema):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int
    user: MeRead
