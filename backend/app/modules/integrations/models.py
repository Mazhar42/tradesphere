"""Working beside another system (data-import-and-coexistence.md §11-12)."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, CreatedByMixin, IdMixin, TenantScoped
from app.shared.money import QTY


class DomainOwnership(TenantScoped, IdMixin, CreatedByMixin, Base):
    """Who is in charge of one kind of data. No row means TradeSphere."""

    __tablename__ = "domain_ownership"

    domain: Mapped[str] = mapped_column(String(15))
    owner: Mapped[str] = mapped_column(String(12))
    changed_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "domain", name="uq_domain_ownership_domain"),
        CheckConstraint("owner IN ('external','tradesphere')", name="owner_valid"),
    )


class ExternalSnapshot(TenantScoped, IdMixin, Base):
    """What the other system said, and when. Never part of the ledger or of stock."""

    __tablename__ = "external_snapshots"

    kind: Mapped[str] = mapped_column(String(10))  # stock | balance
    source_system: Mapped[str] = mapped_column(String(40))
    item_key: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(60))
    detail: Mapped[str] = mapped_column(String(60))  # location, or party type
    internal_id: Mapped[int | None] = mapped_column(BigInteger)
    value: Mapped[Decimal] = mapped_column(QTY)  # quantity, or amount
    as_of: Mapped[datetime]
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "kind", "source_system", "item_key", name="uq_external_snapshots_item"
        ),
        CheckConstraint("kind IN ('stock','balance')", name="kind_valid"),
    )


class IntegrationConnection(TenantScoped, IdMixin, CreatedByMixin, Base):
    """A place files are fetched from on a schedule."""

    __tablename__ = "integration_connections"

    name: Mapped[str] = mapped_column(String(80))
    source_system: Mapped[str] = mapped_column(String(40))
    feeds: Mapped[list[dict[str, str]]] = mapped_column(JSONB)  # [{"data", "url"}]
    secret: Mapped[str | None] = mapped_column(String(2000))  # encrypted request header
    every_minutes: Mapped[int | None]  # None: only when asked
    enabled: Mapped[bool] = mapped_column(server_default="true")
    state: Mapped[dict[str, Any]] = mapped_column(JSONB)  # last content hash per feed
    next_run_at: Mapped[datetime | None]
    last_attempt_at: Mapped[datetime | None]
    last_success_at: Mapped[datetime | None]
    failures: Mapped[int] = mapped_column(server_default="0")  # in a row
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_integration_connections_name"),
    )


class SyncRun(TenantScoped, IdMixin, Base):
    __tablename__ = "sync_runs"

    connection_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("integration_connections.id"))
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(10))  # ok | failed
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)

    __table_args__ = (Index("ix_sync_runs_connection", "tenant_id", "connection_id", "id"),)


class ApiKey(TenantScoped, IdMixin, CreatedByMixin, Base):
    """Lets another system send files. Only the hash is kept."""

    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(String(80))
    source_system: Mapped[str] = mapped_column(String(40))
    hint: Mapped[str] = mapped_column(String(8))  # last characters, to tell keys apart
    key_hash: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    window_start: Mapped[datetime | None]
    window_count: Mapped[int] = mapped_column(server_default="0")
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (UniqueConstraint("tenant_id", "key_hash", name="uq_api_keys_hash"),)
