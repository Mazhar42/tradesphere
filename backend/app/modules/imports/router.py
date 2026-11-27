from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from pydantic import Field
from sqlalchemy import func, select

from app.core.db import DbSession
from app.core.errors import BusinessRuleError
from app.core.permissions import STEP_UP, CurrentUser, require
from app.core.tenant_context import tenant_id_of
from app.modules.catalog.models import Product
from app.modules.imports import openings, parsing, service
from app.modules.imports.entities import ENTITIES
from app.modules.imports.models import ImportedDocument, ImportJob, ImportRow, OpeningLoad
from app.modules.jobs.service import enqueue
from app.modules.parties.models import Customer, Supplier
from app.shared.money import Money
from app.shared.pagination import PageParamsDep, paginate
from app.shared.schemas import Page, ReadSchema, WriteSchema

router = APIRouter(prefix="/imports", tags=["imports"])

CanImport = Annotated[CurrentUser, Depends(require("imports:write"))]
CanApprove = Annotated[CurrentUser, Depends(require("imports:approve"))]
CanOpen = Annotated[CurrentUser, Depends(require("finance:opening"))]

CSV = "text/csv; charset=utf-8"


class ColumnRead(ReadSchema):
    name: str
    bangla: str
    required: bool


class EntityRead(ReadSchema):
    name: str
    opening: bool
    columns: list[ColumnRead]


class JobRead(ReadSchema):
    id: int
    entity: str
    source_system: str
    status: str
    headers: list[str]
    mapping: dict[str, str]
    options: dict[str, Any]
    counts: dict[str, Any]
    dry_run_digest: str | None
    message: str | None
    created_at: datetime


class RowRead(ReadSchema):
    row_no: int
    status: str
    action: str | None
    errors: list[str]
    raw: dict[str, str]


class MappingUpdate(WriteSchema):
    mapping: dict[str, str]
    options: dict[str, Any] | None = None


class Approval(WriteSchema):
    dry_run_digest: str


class OpeningLoadRead(ReadSchema):
    id: int
    domain: str
    doc_no: str
    business_date: date
    total: Money
    status: str


class CutoverRead(ReadSchema):
    cutover_date: date | None
    imported: dict[str, str]
    reconcile_ok: bool
    reconcile_issues: list[str]
    signed_at: datetime | None


class SignOff(WriteSchema):
    declared: dict[
        Literal["stock_value", "receivables", "payables", "advances", "claims", "cash"], Decimal
    ]
    reasons: dict[str, str] = Field(default_factory=dict)


class Checklist(ReadSchema):
    products: int
    suppliers: int
    customers: int
    opening_domains: list[str]
    signed_off: bool


class HistoryRead(ReadSchema):
    id: int
    doc_type: str
    doc_date: date
    number: str | None
    party: str | None
    amount: Money
    source_system: str


@router.get("/entities", response_model=list[EntityRead])
def list_entities(_: CanImport) -> list[EntityRead]:
    return [
        EntityRead(
            name=e.name,
            opening=e.opening_domain is not None,
            columns=[ColumnRead.model_validate(c, from_attributes=True) for c in e.columns],
        )
        for e in ENTITIES.values()
    ]


@router.get("/templates/{entity}")
def template(_: CanImport, entity: str, lang: Literal["en", "bn"] = "en") -> Response:
    return Response(
        service.template(entity, lang),
        media_type=CSV,
        headers={"Content-Disposition": f'attachment; filename="{entity}-template.csv"'},
    )


@router.post("", response_model=JobRead, status_code=status.HTTP_201_CREATED)
def upload(
    db: DbSession,
    actor: CanImport,
    file: Annotated[UploadFile, File()],
    entity: Annotated[str, Form(max_length=30)],
    source_system: Annotated[str, Form(max_length=40, pattern=r"^[A-Za-z0-9 _.-]+$")] = "file",
    conflict_policy: Annotated[str, Form()] = "skip",
    date_format: Annotated[str, Form()] = "YYYY-MM-DD",
    update_prices: Annotated[bool, Form()] = False,
    update_credit_limit: Annotated[bool, Form()] = False,
    cutover_date: Annotated[date | None, Form()] = None,
) -> JobRead:
    data = file.file.read(parsing.MAX_BYTES + 1)
    job = service.upload(
        db,
        entity_name=entity,
        source_system=source_system,
        file_name=file.filename or "upload",
        data=data,
        options={
            "conflict_policy": conflict_policy,
            "date_format": date_format,
            "update_prices": update_prices,
            "update_credit_limit": update_credit_limit,
            "cutover_date": cutover_date,
        },
        actor=actor,
    )
    result = JobRead.model_validate(job)
    db.commit()
    return result


@router.get("", response_model=Page[JobRead])
def list_jobs(db: DbSession, _: CanImport, page: PageParamsDep) -> Page[JobRead]:
    rows, total = paginate(db, select(ImportJob).order_by(ImportJob.id.desc()), page)
    return Page(
        items=[JobRead.model_validate(j) for j in rows],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.get("/checklist", response_model=Checklist)
def checklist(db: DbSession, _: CanImport) -> Checklist:
    def count(model: Any) -> int:
        return db.scalar(select(func.count()).select_from(model)) or 0

    domains = db.scalars(
        select(OpeningLoad.domain).where(OpeningLoad.status == "posted").distinct()
    ).all()
    return Checklist(
        products=count(Product),
        suppliers=count(Supplier),
        customers=max(count(Customer) - 1, 0),  # the walk-in is not the dealer's own
        opening_domains=sorted(domains),
        signed_off=service.cutover_report(db)["signed_at"] is not None,
    )


@router.get("/cutover", response_model=CutoverRead)
def cutover(db: DbSession, _: CanImport) -> CutoverRead:
    return CutoverRead(**service.cutover_report(db))


@router.post("/cutover/sign-off", status_code=status.HTTP_204_NO_CONTENT, dependencies=STEP_UP)
def sign_off(db: DbSession, actor: CanOpen, data: SignOff) -> None:
    declared = {str(name): value for name, value in data.declared.items()}
    service.sign_off(db, declared, data.reasons, actor)
    db.commit()


@router.get("/openings", response_model=list[OpeningLoadRead])
def list_openings(db: DbSession, _: CanImport) -> list[OpeningLoadRead]:
    rows = db.scalars(select(OpeningLoad).order_by(OpeningLoad.id.desc()).limit(100))
    return [OpeningLoadRead.model_validate(r) for r in rows]


@router.post("/openings/{load_id}/reverse", response_model=OpeningLoadRead, dependencies=STEP_UP)
def reverse_opening(db: DbSession, actor: CanOpen, load_id: int) -> OpeningLoadRead:
    result = OpeningLoadRead.model_validate(openings.reverse_load(db, load_id, actor))
    db.commit()
    return result


@router.get("/history", response_model=Page[HistoryRead])
def history(
    db: DbSession,
    _: Annotated[CurrentUser, Depends(require("reports:read"))],
    page: PageParamsDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[HistoryRead]:
    statement = select(ImportedDocument).order_by(
        ImportedDocument.doc_date.desc(), ImportedDocument.id.desc()
    )
    if q:
        like = f"%{q}%"
        statement = statement.where(
            ImportedDocument.number.ilike(like) | ImportedDocument.party.ilike(like)
        )
    rows, total = paginate(db, statement, page)
    return Page(
        items=[HistoryRead.model_validate(r) for r in rows],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.get("/{job_id}", response_model=JobRead)
def get_job(db: DbSession, _: CanImport, job_id: int) -> JobRead:
    return JobRead.model_validate(service.get_job(db, job_id))


@router.get("/{job_id}/rows", response_model=Page[RowRead])
def get_rows(
    db: DbSession,
    _: CanImport,
    job_id: int,
    page: PageParamsDep,
    only: Annotated[str | None, Query(pattern="^(error|valid|committed)$")] = None,
) -> Page[RowRead]:
    service.get_job(db, job_id)
    statement = select(ImportRow).where(ImportRow.job_id == job_id).order_by(ImportRow.row_no)
    if only:
        statement = statement.where(ImportRow.status == only)
    rows, total = paginate(db, statement, page)
    return Page(
        items=[RowRead.model_validate(r) for r in rows],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.get("/{job_id}/errors")
def errors_csv(db: DbSession, _: CanImport, job_id: int) -> Response:
    return Response(
        service.error_report(db, job_id),
        media_type=CSV,
        headers={"Content-Disposition": f'attachment; filename="import-{job_id}-errors.csv"'},
    )


@router.put("/{job_id}/mapping", response_model=JobRead)
def set_mapping(db: DbSession, _: CanImport, job_id: int, data: MappingUpdate) -> JobRead:
    result = JobRead.model_validate(service.set_mapping(db, job_id, data.mapping, data.options))
    db.commit()
    return result


@router.post("/{job_id}/approve", response_model=JobRead)
def approve(db: DbSession, actor: CanApprove, job_id: int, data: Approval) -> JobRead:
    job = service.approve(db, job_id, data.dry_run_digest, actor)
    enqueue(db, "import.commit", {"job_id": job.id}, tenant_id=tenant_id_of(db))
    result = JobRead.model_validate(job)
    db.commit()
    return result


@router.post("/{job_id}/retry", response_model=JobRead)
def retry(db: DbSession, _: CanApprove, job_id: int) -> JobRead:
    job = service.get_job(db, job_id, lock=True)
    if job.status not in ("approved", "committing", "partially_completed"):
        raise BusinessRuleError("Nothing to retry.", code="not_retryable")
    enqueue(db, "import.commit", {"job_id": job.id}, tenant_id=tenant_id_of(db))
    result = JobRead.model_validate(job)
    db.commit()
    return result


@router.post("/{job_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
def cancel_import(db: DbSession, _: CanImport, job_id: int) -> None:
    service.cancel(db, job_id)
    db.commit()
