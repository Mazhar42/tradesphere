"""Append-only audit trail, written in the same transaction as the change."""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, func, inspect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.context import client_ip_var, request_id_var
from app.shared.models import Base, IdMixin, TenantScoped

SENSITIVE_FIELDS = frozenset({"password_hash", "token_hash"})


class AuditLog(TenantScoped, IdMixin, Base):
    __tablename__ = "audit_logs"

    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    actor_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(50))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(50))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_audit_logs_occurred_at", "tenant_id", "occurred_at"),
        Index("ix_audit_logs_entity", "tenant_id", "entity_type", "entity_id"),
    )


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    return value


def snapshot(obj: Any, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Column values of an ORM object as a JSON-safe dict (sensitive fields removed)."""
    mapper = inspect(obj).mapper
    data: dict[str, Any] = {
        attr.key: json_safe(getattr(obj, attr.key))
        for attr in mapper.column_attrs
        if attr.key not in SENSITIVE_FIELDS and attr.key not in ("created_at", "updated_at")
    }
    if extra:
        data.update(json_safe(extra))
    return data


def record_audit(
    session: Session,
    *,
    actor_id: int | None,
    action: str,
    entity_type: str,
    entity_id: int | str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Add an audit row. For updates, only the changed keys are stored."""
    before = json_safe(before) if before is not None else None
    after = json_safe(after) if after is not None else None
    if before is not None and after is not None:
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        before = {k: v for k, v in before.items() if k in changed}
        after = {k: v for k, v in after.items() if k in changed}
    session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            before=before,
            after=after,
            ip=client_ip_var.get(),
            request_id=request_id_var.get(),
        )
    )
