from typing import Annotated, Literal

from pydantic import Field

from app.shared.schemas import ReadSchema, WriteSchema


class LoginRequest(WriteSchema):
    username: Annotated[str, Field(min_length=1, max_length=50)]
    password: Annotated[str, Field(min_length=1, max_length=128)]


class RefreshRequest(WriteSchema):
    # The dealership the calling tab is showing. The cookie is not tied to one: a tab never
    # switches dealership on its own. Empty: the oldest membership.
    tenant_id: int | None = None


class TenantSummary(ReadSchema):
    id: int
    slug: str
    name: str


class MeRead(ReadSchema):
    id: int
    username: str
    full_name: str
    roles: list[str]
    permissions: list[str]
    tenant: TenantSummary | None  # the one this access token is for
    tenants: list[TenantSummary]


class TokenResponse(ReadSchema):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int
    user: MeRead
