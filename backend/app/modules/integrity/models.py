from datetime import datetime
from typing import Any

from sqlalchemy import Index, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, IdMixin, TenantScoped


class ReconcileRun(TenantScoped, IdMixin, Base):
    """The outcome of one reconciliation, so screens need not wait for a new one."""

    __tablename__ = "reconcile_runs"

    ran_at: Mapped[datetime] = mapped_column(server_default=func.now())
    ok: Mapped[bool]
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)

    __table_args__ = (Index("ix_reconcile_runs_tenant_ran", "tenant_id", "ran_at"),)
