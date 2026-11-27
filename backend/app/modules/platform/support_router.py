"""A dealership owner's side of support access (ADR-005)."""

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import Field
from sqlalchemy import select, text

from app.core.db import DbSession
from app.core.errors import BusinessRuleError, NotFoundError
from app.core.permissions import STEP_UP, CurrentUser, require
from app.core.tenant_context import tenant_id_of
from app.modules.platform.models import SupportGrant
from app.shared.audit import record_audit
from app.shared.dates import now_utc
from app.shared.schemas import ReadSchema, WriteSchema

router = APIRouter(prefix="/support-grants", tags=["settings"])

CanRead = Annotated[CurrentUser, Depends(require("settings:read"))]
CanWrite = Annotated[CurrentUser, Depends(require("settings:write"))]


class SupportGrantCreate(WriteSchema):
    operator_username: Annotated[str, Field(min_length=3, max_length=50)]
    reason: Annotated[str, Field(min_length=5, max_length=300)]
    hours: Annotated[int, Field(ge=1, le=72)] = 24


class SupportGrantRead(ReadSchema):
    id: int
    operator_id: int
    reason: str
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime


@router.get("", response_model=list[SupportGrantRead])
def list_support_grants(db: DbSession, _: CanRead) -> list[SupportGrantRead]:
    rows = db.scalars(
        select(SupportGrant)
        .where(SupportGrant.tenant_id == tenant_id_of(db))
        .order_by(SupportGrant.id.desc())
        .limit(50)
    )
    return [SupportGrantRead.model_validate(g) for g in rows]


@router.post(
    "", response_model=SupportGrantRead, status_code=status.HTTP_201_CREATED, dependencies=STEP_UP
)
def create_support_grant(
    db: DbSession, actor: CanWrite, data: SupportGrantCreate
) -> SupportGrantRead:
    operator_id = db.scalar(text("SELECT app_operator_id(:name)"), {"name": data.operator_username})
    if operator_id is None:
        raise BusinessRuleError(
            "There is no support operator with that name.",
            code="unknown_operator",
            errors=[{"field": "operator_username", "message": "Unknown"}],
        )
    grant = SupportGrant(
        operator_id=operator_id,
        approved_by_id=actor.id,
        reason=data.reason,
        expires_at=now_utc() + timedelta(hours=data.hours),
    )
    db.add(grant)
    db.flush()
    record_audit(
        db,
        actor_id=actor.id,
        action="grant_support",
        entity_type="support_grant",
        entity_id=grant.id,
        after={"operator": data.operator_username, "hours": data.hours, "reason": data.reason},
    )
    result = SupportGrantRead.model_validate(grant)
    db.commit()
    return result


@router.delete("/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_support_grant(db: DbSession, actor: CanWrite, grant_id: int) -> None:
    grant = db.scalar(
        select(SupportGrant).where(
            SupportGrant.id == grant_id, SupportGrant.tenant_id == tenant_id_of(db)
        )
    )
    if grant is None:
        raise NotFoundError("Grant not found.")
    grant.revoked_at = grant.revoked_at or now_utc()
    record_audit(
        db,
        actor_id=actor.id,
        action="revoke_support",
        entity_type="support_grant",
        entity_id=grant.id,
    )
    db.commit()
