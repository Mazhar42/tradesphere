from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.db import DbSession
from app.core.permissions import CurrentUser, require
from app.modules.users.models import User
from app.shared.audit import AuditLog
from app.shared.pagination import PageParamsDep, paginate
from app.shared.schemas import Page, ReadSchema

router = APIRouter(tags=["audit"])


class AuditLogRead(ReadSchema):
    id: int
    occurred_at: datetime
    actor_id: int | None
    actor_username: str | None
    action: str
    entity_type: str
    entity_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    ip: str | None
    request_id: str | None


@router.get("/audit-logs", response_model=Page[AuditLogRead])
def list_audit_logs(
    db: DbSession,
    _: Annotated[CurrentUser, Depends(require("audit:read"))],
    page: PageParamsDep,
    entity_type: Annotated[str | None, Query(max_length=50)] = None,
    entity_id: Annotated[str | None, Query(max_length=50)] = None,
    actor_id: int | None = None,
    action: Annotated[str | None, Query(max_length=50)] = None,
) -> Page[AuditLogRead]:
    stmt = select(AuditLog).order_by(AuditLog.id.desc())
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    items, total = paginate(db, stmt, page)
    actor_ids = {a.actor_id for a in items if a.actor_id}
    names = {
        uid: username
        for uid, username in db.execute(
            select(User.id, User.username).where(User.id.in_(actor_ids))
        )
    }
    return Page(
        items=[
            AuditLogRead(
                id=a.id,
                occurred_at=a.occurred_at,
                actor_id=a.actor_id,
                actor_username=names.get(a.actor_id) if a.actor_id else None,
                action=a.action,
                entity_type=a.entity_type,
                entity_id=a.entity_id,
                before=a.before,
                after=a.after,
                ip=a.ip,
                request_id=a.request_id,
            )
            for a in items
        ],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )
