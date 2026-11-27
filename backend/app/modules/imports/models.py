"""Staging for imports (ADR-007): nothing reaches business tables unvalidated."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, CreatedByMixin, IdMixin, TenantScoped
from app.shared.money import MONEY


class ImportFile(TenantScoped, IdMixin, CreatedByMixin, Base):
    __tablename__ = "import_files"

    original_name: Mapped[str] = mapped_column(String(200))
    size: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes | None] = mapped_column(LargeBinary)  # removed after 90 days
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ImportJob(TenantScoped, IdMixin, CreatedByMixin, Base):
    __tablename__ = "import_jobs"

    entity: Mapped[str] = mapped_column(String(30))
    source_system: Mapped[str] = mapped_column(String(40))
    file_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_files.id"))
    status: Mapped[str] = mapped_column(String(20))
    headers: Mapped[list[str]] = mapped_column(JSONB)
    mapping: Mapped[dict[str, str]] = mapped_column(JSONB)  # our column -> file header
    options: Mapped[dict[str, Any]] = mapped_column(JSONB)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB)
    dry_run_digest: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str | None] = mapped_column(String(300))
    approved_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    approved_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (Index("ix_import_jobs_tenant_created", "tenant_id", "created_at"),)


class ImportRow(TenantScoped, IdMixin, Base):
    __tablename__ = "import_rows"

    job_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("import_jobs.id"))
    row_no: Mapped[int]
    raw: Mapped[dict[str, str]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(10), default="new")
    errors: Mapped[list[str]] = mapped_column(JSONB, default=list)
    action: Mapped[str | None] = mapped_column(String(10))
    target_id: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (Index("ix_import_rows_job", "job_id", "row_no"),)


class ImportMapping(TenantScoped, IdMixin, Base):
    """Remembered so the same export is mapped by itself next time."""

    __tablename__ = "import_mappings"

    entity: Mapped[str] = mapped_column(String(30))
    source_system: Mapped[str] = mapped_column(String(40))
    mapping: Mapped[dict[str, str]] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint("tenant_id", "entity", "source_system", name="uq_import_mappings_entity"),
    )


class ExternalRef(TenantScoped, IdMixin, Base):
    """What a record is called in the system it came from."""

    __tablename__ = "external_refs"

    source_system: Mapped[str] = mapped_column(String(40))
    entity: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(100))
    internal_id: Mapped[int] = mapped_column(BigInteger)
    source_hash: Mapped[str] = mapped_column(String(64))
    last_synced_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source_system", "entity", "external_id", name="uq_external_refs_source"
        ),
    )


class OpeningLoad(TenantScoped, IdMixin, CreatedByMixin, Base):
    """Opening positions of one kind (ADR-010): several loads per kind are allowed, and each
    can be reversed on its own."""

    __tablename__ = "opening_loads"

    domain: Mapped[str] = mapped_column(String(15))
    doc_no: Mapped[str] = mapped_column(String(30))
    business_date: Mapped[date]
    total: Mapped[Decimal] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(10), server_default="posted")
    job_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_jobs.id"))
    reversed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "doc_no", name="uq_opening_loads_doc_no"),
        CheckConstraint("status IN ('posted','reversed')", name="status_valid"),
    )


class OpeningLine(TenantScoped, IdMixin, Base):
    __tablename__ = "opening_lines"

    load_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("opening_loads.id"))
    domain: Mapped[str] = mapped_column(String(15))
    item_key: Mapped[str] = mapped_column(String(200))  # what must not be opened twice
    party_id: Mapped[int | None] = mapped_column(BigInteger)
    amount: Mapped[Decimal] = mapped_column(MONEY)  # signed: an advance is negative payable
    active: Mapped[bool] = mapped_column(server_default="true")

    __table_args__ = (
        Index(
            "uq_opening_lines_active",
            "tenant_id",
            "domain",
            "item_key",
            unique=True,
            postgresql_where=text("active"),
        ),
        Index("ix_opening_lines_load", "load_id"),
    )


class ImportedDocument(TenantScoped, IdMixin, Base):
    """History kept for reference. It posts no journal entry and moves no stock."""

    __tablename__ = "imported_documents"

    source_system: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(100))
    doc_type: Mapped[str] = mapped_column(String(20))
    doc_date: Mapped[date]
    number: Mapped[str | None] = mapped_column(String(60))
    party: Mapped[str | None] = mapped_column(String(150))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    note: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source_system", "external_id", name="uq_imported_documents_source"
        ),
        Index("ix_imported_documents_date", "tenant_id", "doc_date", "id"),
    )


class CutoverSignoff(TenantScoped, IdMixin, CreatedByMixin, Base):
    __tablename__ = "cutover_signoffs"

    cutover_date: Mapped[date]
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)
    signed_at: Mapped[datetime] = mapped_column(server_default=func.now())
