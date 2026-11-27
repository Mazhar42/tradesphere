from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, IdMixin


class Tenant(Base):
    """One dealership. Its records carry `tenant_id = id`."""

    __tablename__ = "tenants"

    # BY DEFAULT, not ALWAYS: provisioning takes the id first so it can set the context.
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    slug: Mapped[str] = mapped_column(String(50), unique=True)
    legal_name: Mapped[str] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    timezone: Mapped[str] = mapped_column(String(50))
    currency_code: Mapped[str] = mapped_column(String(3), server_default="BDT")
    fiscal_year_start_month: Mapped[int]
    cutover_date: Mapped[date | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','active','suspended','deleted')", name="status_valid"
        ),
        CheckConstraint("fiscal_year_start_month BETWEEN 1 AND 12", name="fy_month_valid"),
    )


class TenantMembership(Base):
    """A person's place in a dealership. Never deleted, only deactivated: documents and audit
    rows point at it."""

    __tablename__ = "tenant_memberships"

    tenant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tenants.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(10), server_default="active")
    # True when this dealership created the account, so it may manage its credentials.
    managed_by_tenant: Mapped[bool] = mapped_column(server_default="false")
    invited_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    joined_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('active','inactive')", name="status_valid"),
        Index("ix_tenant_memberships_user_id", "user_id"),
    )


class AuthEvent(IdMixin, Base):
    """Sign-in history. It happens before a dealership is known, so it belongs to none."""

    __tablename__ = "auth_events"

    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    identifier: Mapped[str | None] = mapped_column(String(150))
    action: Mapped[str] = mapped_column(String(30))
    ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_auth_events_identifier_occurred", "identifier", "occurred_at"),
        Index("ix_auth_events_ip_occurred", "ip", "occurred_at"),
    )
