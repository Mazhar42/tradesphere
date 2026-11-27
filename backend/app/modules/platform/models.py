from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, IdMixin


class PlatformOperator(Base):
    """A person who runs the service. Not a member of any dealership by that fact."""

    __tablename__ = "platform_operators"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), primary_key=True, autoincrement=False
    )
    status: Mapped[str] = mapped_column(String(10), server_default="active")
    # admin and billing may change money matters; support may not.
    role: Mapped[str] = mapped_column(String(20), server_default="admin")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('active','inactive')", name="status_valid"),
        CheckConstraint("role IN ('admin','billing','support')", name="role_valid"),
    )


class PlatformAuditLog(IdMixin, Base):
    __tablename__ = "platform_audit_logs"

    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    operator_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    tenant_id: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(String(300))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (Index("ix_platform_audit_logs_occurred_at", "occurred_at"),)


class SupportGrant(IdMixin, Base):
    """A dealership owner's permission for one operator to read their records for a while."""

    __tablename__ = "support_access_grants"

    # Not `TenantScoped`: the platform reads grants across dealerships; row-level security
    # limits everyone else to their own.
    tenant_id: Mapped[int] = mapped_column(BigInteger, server_default=text("app_current_tenant()"))
    operator_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    approved_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(300))
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
