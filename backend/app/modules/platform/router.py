"""The platform administration API. Separate tokens, separate database connection."""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field

from app.core.db import PlatformDb
from app.core.errors import UnauthorizedError
from app.core.security import decode_platform_token
from app.modules.auth.schemas import Code
from app.modules.platform import service
from app.modules.users.schemas import Username
from app.shared.schemas import ReadSchema, WriteSchema

router = APIRouter(prefix="/platform", tags=["platform"])
_bearer = HTTPBearer(auto_error=False)


def get_operator(
    db: PlatformDb,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> int:
    if credentials is None:
        raise UnauthorizedError("Authentication required.", code="not_authenticated")
    user_id = decode_platform_token(credentials.credentials)
    service.active_operator(db, user_id)  # checked on every request
    return user_id


Operator = Annotated[int, Depends(get_operator)]


class PlatformLogin(WriteSchema):
    username: Username
    password: Annotated[str, Field(min_length=1, max_length=128)]
    code: Code


class PlatformToken(ReadSchema):
    access_token: str


class TenantRow(ReadSchema):
    id: int
    slug: str
    legal_name: str
    status: str
    created_at: datetime
    members: int


class TenantStatusChange(WriteSchema):
    status: Literal["active", "suspended"]
    reason: Annotated[str, Field(min_length=5, max_length=300)]


class PlatformAuditRow(ReadSchema):
    id: int
    occurred_at: datetime
    operator_id: int | None
    action: str
    tenant_id: int | None
    reason: str | None
    details: dict[str, Any] | None


class GrantRow(ReadSchema):
    id: int
    tenant_id: int
    reason: str
    expires_at: datetime


@router.post("/login", response_model=PlatformToken)
def platform_login(db: PlatformDb, data: PlatformLogin, request: Request) -> PlatformToken:
    ip = request.client.host if request.client else None
    token = service.login(db, data.username, data.password, data.code, ip)
    db.commit()
    return PlatformToken(access_token=token)


@router.get("/tenants", response_model=list[TenantRow])
def list_tenants(db: PlatformDb, _: Operator) -> list[TenantRow]:
    return [
        TenantRow(
            id=t.id,
            slug=t.slug,
            legal_name=t.legal_name,
            status=t.status,
            created_at=t.created_at,
            members=members,
        )
        for t, members in service.tenants(db)
    ]


@router.patch("/tenants/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
def change_tenant_status(
    db: PlatformDb, operator: Operator, tenant_id: int, data: TenantStatusChange
) -> None:
    service.set_tenant_status(db, tenant_id, data.status, data.reason, operator)
    db.commit()


@router.get("/audit", response_model=list[PlatformAuditRow])
def platform_audit(db: PlatformDb, _: Operator) -> list[PlatformAuditRow]:
    return [PlatformAuditRow.model_validate(r) for r in service.audit_log(db)]


class JobHealth(ReadSchema):
    queued: int
    running: int
    failed: int
    oldest_queued_seconds: int
    recent_failures: list[str]


@router.get("/jobs", response_model=JobHealth)
def job_health(db: PlatformDb, _: Operator) -> JobHealth:
    return JobHealth(**service.job_health(db))


@router.get("/support-grants", response_model=list[GrantRow])
def my_support_grants(db: PlatformDb, operator: Operator) -> list[GrantRow]:
    return [GrantRow.model_validate(g) for g in service.my_grants(db, operator)]


@router.post("/support-grants/{grant_id}/token", response_model=PlatformToken)
def support_token(db: PlatformDb, operator: Operator, grant_id: int) -> PlatformToken:
    token = service.support_token(db, grant_id, operator)
    db.commit()
    return PlatformToken(access_token=token)
