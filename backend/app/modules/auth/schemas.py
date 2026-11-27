from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from app.modules.users.schemas import Email, Password, Username
from app.shared.schemas import ReadSchema, WriteSchema

Code = Annotated[str, Field(min_length=6, max_length=20)]


class LoginRequest(WriteSchema):
    username: Annotated[str, Field(min_length=1, max_length=50)]
    password: Annotated[str, Field(min_length=1, max_length=128)]
    code: Code | None = None  # authenticator or recovery code, when two-step sign-in is on


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
    mfa_enrolled: bool
    mfa_enrolment_required: bool
    subscription_status: str | None  # for banners; None when nothing is limited


class TokenResponse(ReadSchema):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int
    user: MeRead


Secret = Annotated[str, Field(min_length=10, max_length=200)]


class TokenOnly(WriteSchema):
    token: Secret


class InvitationPreview(ReadSchema):
    dealership: str
    email: str | None


class InvitationAccept(WriteSchema):
    token: Secret
    username: Username
    full_name: Annotated[str | None, Field(max_length=120)] = None
    password: Password


class ResetRequest(WriteSchema):
    identifier: Annotated[str, Field(min_length=1, max_length=150)]  # username or email


class ResetConfirm(WriteSchema):
    token: Secret
    new_password: Password


class EmailChange(WriteSchema):
    email: Email
    password: Annotated[str, Field(min_length=1, max_length=128)]


class PasswordChange(WriteSchema):
    current_password: Annotated[str, Field(min_length=1, max_length=128)]
    new_password: Password


class CodeOnly(WriteSchema):
    code: Code


class MfaStart(WriteSchema):
    password: Annotated[str, Field(min_length=1, max_length=128)]


class MfaSetup(ReadSchema):
    secret: str
    uri: str


class RecoveryCodes(ReadSchema):
    recovery_codes: list[str]


class MfaDisable(WriteSchema):
    password: Annotated[str, Field(min_length=1, max_length=128)]
    code: Code


class SessionRead(ReadSchema):
    id: str
    signed_in_at: datetime
    last_used_at: datetime
    user_agent: str | None
    ip: str | None
    current: bool
