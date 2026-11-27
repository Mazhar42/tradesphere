from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from pydantic import Field
from sqlalchemy import select

from app.core.db import DbSession
from app.core.errors import ConflictError, NotFoundError
from app.core.permissions import STEP_UP, CurrentUser, CurrentUserDep, require
from app.core.tenant_context import tenant_id_of
from app.modules.billing import entitlements
from app.modules.catalog.models import Product
from app.modules.imports import parsing
from app.modules.integrations import exports, keys, ownership, service
from app.modules.integrations.models import (
    ApiKey,
    ExternalSnapshot,
    IntegrationConnection,
    SyncRun,
)
from app.modules.inventory.models import ProductCost
from app.modules.jobs.service import enqueue
from app.shared.audit import record_audit
from app.shared.dates import now_utc
from app.shared.pagination import PageParamsDep, paginate
from app.shared.schemas import Page, ReadSchema, WriteSchema

router = APIRouter(tags=["integrations"])

CanManage = Annotated[CurrentUser, Depends(require("integrations:manage"))]
CanSetOwner = Annotated[CurrentUser, Depends(require("settings:write"))]
CanStock = Annotated[CurrentUser, Depends(require("inventory:read"))]
CanBooks = Annotated[CurrentUser, Depends(require("finance:read"))]

CSV = "text/csv; charset=utf-8"
Name = Annotated[str, Field(min_length=1, max_length=80)]
Source = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9 _.-]+$")]


class DomainRead(ReadSchema):
    domain: str
    owner: str


class DomainWrite(WriteSchema):
    domain: str
    owner: Literal["external", "tradesphere"]


class Feed(WriteSchema):
    data: str
    url: str = Field(max_length=500)


class ConnectionWrite(WriteSchema):
    name: Name
    source_system: Source
    feeds: list[Feed] = Field(min_length=1, max_length=10)
    every_minutes: int | None = Field(default=None, le=60 * 24 * 7)
    enabled: bool = True
    header_name: str | None = Field(default=None, max_length=60, pattern=r"^[A-Za-z0-9-]+$")
    header_value: str | None = Field(default=None, max_length=1000)


class ConnectionRead(ReadSchema):
    id: int
    name: str
    source_system: str
    feeds: list[dict[str, str]]
    every_minutes: int | None
    enabled: bool
    has_credentials: bool
    next_run_at: datetime | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    failures: int


class RunRead(ReadSchema):
    id: int
    started_at: datetime
    finished_at: datetime
    status: str
    results: list[dict[str, Any]]


class KeyWrite(WriteSchema):
    name: Name
    source_system: Source
    scopes: list[Literal["files:write", "snapshots:write"]] = Field(min_length=1)


class KeyRead(ReadSchema):
    id: int
    name: str
    source_system: str
    hint: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class KeyCreated(KeyRead):
    key: str  # shown this once


class SnapshotRead(ReadSchema):
    id: int
    source_system: str
    code: str
    detail: str
    value: Decimal
    ours: Decimal | None  # our quantity, to compare (stock without a location only)
    matched: bool
    as_of: datetime


class SnapshotPush(WriteSchema):
    kind: Literal["stock", "balance"]
    rows: list[dict[str, str]] = Field(min_length=1, max_length=5000)


class FeedKinds(ReadSchema):
    kinds: list[str]


class PushResult(ReadSchema):
    result: dict[str, Any]


# --- Ownership -------------------------------------------------------------------------------


@router.get("/integrations/domains", response_model=list[DomainRead])
def list_domains(db: DbSession, _: CurrentUserDep) -> list[DomainRead]:
    return [DomainRead(domain=d, owner=o) for d, o in ownership.owners(db).items()]


@router.put("/integrations/domains", response_model=list[DomainRead], dependencies=STEP_UP)
def set_domain(db: DbSession, actor: CanSetOwner, data: DomainWrite) -> list[DomainRead]:
    service.set_owner(db, data.domain, data.owner, actor)
    result = [DomainRead(domain=d, owner=o) for d, o in ownership.owners(db).items()]
    db.commit()
    return result


@router.get("/integrations/feed-kinds", response_model=FeedKinds)
def list_feed_kinds(_: CanManage) -> FeedKinds:
    return FeedKinds(kinds=service.feed_kinds())


# --- Connections ------------------------------------------------------------------------------


def _connection(row: IntegrationConnection) -> ConnectionRead:
    return ConnectionRead(
        id=row.id,
        name=row.name,
        source_system=row.source_system,
        feeds=row.feeds,
        every_minutes=row.every_minutes,
        enabled=row.enabled,
        has_credentials=row.secret is not None,
        next_run_at=row.next_run_at,
        last_attempt_at=row.last_attempt_at,
        last_success_at=row.last_success_at,
        failures=row.failures or 0,
    )


def _save(
    db: DbSession,
    existing: IntegrationConnection | None,
    data: ConnectionWrite,
    actor: CurrentUser,
) -> ConnectionRead:
    taken = db.scalar(
        select(IntegrationConnection.id).where(IntegrationConnection.name == data.name)
    )
    if taken is not None and (existing is None or taken != existing.id):
        raise ConflictError("A connection with this name exists.", code="duplicate_name")
    values = data.model_dump(exclude={"header_name", "header_value"})
    row = service.save_connection(
        db,
        existing,
        values=values,
        header_name=data.header_name,
        header_value=data.header_value,
        actor=actor,
    )
    db.refresh(row)
    result = _connection(row)
    db.commit()
    return result


@router.get("/integrations/connections", response_model=list[ConnectionRead])
def list_connections(db: DbSession, _: CanManage) -> list[ConnectionRead]:
    rows = db.scalars(select(IntegrationConnection).order_by(IntegrationConnection.id))
    return [_connection(r) for r in rows]


@router.post(
    "/integrations/connections",
    response_model=ConnectionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=STEP_UP,
)
def create_connection(db: DbSession, actor: CanManage, data: ConnectionWrite) -> ConnectionRead:
    return _save(db, None, data, actor)


@router.put(
    "/integrations/connections/{connection_id}",
    response_model=ConnectionRead,
    dependencies=STEP_UP,
)
def update_connection(
    db: DbSession, actor: CanManage, connection_id: int, data: ConnectionWrite
) -> ConnectionRead:
    existing = service.get_connection(db, connection_id, lock=True)
    return _save(db, existing, data, actor)


@router.post(
    "/integrations/connections/{connection_id}/sync",
    status_code=status.HTTP_202_ACCEPTED,
)
def sync_now(db: DbSession, _: CanManage, connection_id: int) -> None:
    row = service.get_connection(db, connection_id)
    if not row.enabled:
        raise ConflictError("The connection is switched off.", code="disabled")
    enqueue(
        db,
        "integration.sync",
        {"connection_id": row.id, "due": None},
        tenant_id=tenant_id_of(db),
        # At most one asked-for run a minute.
        dedupe_key=f"integration:{row.id}:now:{now_utc():%Y%m%d%H%M}",
    )
    db.commit()


@router.get("/integrations/connections/{connection_id}/runs", response_model=list[RunRead])
def list_runs(db: DbSession, _: CanManage, connection_id: int) -> list[RunRead]:
    service.get_connection(db, connection_id)
    rows = db.scalars(
        select(SyncRun)
        .where(SyncRun.connection_id == connection_id)
        .order_by(SyncRun.id.desc())
        .limit(30)
    )
    return [RunRead.model_validate(r) for r in rows]


# --- API keys ---------------------------------------------------------------------------------


@router.get("/integrations/api-keys", response_model=list[KeyRead])
def list_keys(db: DbSession, _: CanManage) -> list[KeyRead]:
    return [KeyRead.model_validate(k) for k in db.scalars(select(ApiKey).order_by(ApiKey.id))]


@router.post(
    "/integrations/api-keys",
    response_model=KeyCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=STEP_UP,
)
def create_key(db: DbSession, actor: CanManage, data: KeyWrite) -> KeyCreated:
    row, token = keys.create(
        db,
        name=data.name,
        source_system=data.source_system,
        scopes=list(data.scopes),
        actor_id=actor.id,
    )
    db.refresh(row)
    result = KeyCreated(**KeyRead.model_validate(row).model_dump(), key=token)
    db.commit()
    return result


@router.delete("/integrations/api-keys/{api_key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_key(db: DbSession, actor: CanManage, api_key_id: int) -> None:
    row = db.scalar(select(ApiKey).where(ApiKey.id == api_key_id).with_for_update())
    if row is None:
        raise NotFoundError("Key not found.")
    row.revoked_at = row.revoked_at or now_utc()
    record_audit(db, actor_id=actor.id, action="revoke", entity_type="api_key", entity_id=row.id)
    db.commit()


# --- Snapshots and exports ----------------------------------------------------------------------


def _snapshots(db: DbSession, kind: str, page: PageParamsDep) -> Page[SnapshotRead]:
    statement = (
        select(ExternalSnapshot)
        .where(ExternalSnapshot.kind == kind)
        .order_by(ExternalSnapshot.code, ExternalSnapshot.id)
    )
    rows, total = paginate(db, statement, page)
    ours: dict[int, Decimal] = {}
    if kind == "stock":
        wanted = {r.internal_id for r in rows if r.internal_id and not r.detail}
        ours = dict(
            db.execute(
                select(Product.id, ProductCost.qty_on_hand)
                .join(ProductCost, ProductCost.product_id == Product.id)
                .where(Product.id.in_(wanted))
            ).all()
        )
    return Page(
        items=[
            SnapshotRead(
                id=r.id,
                source_system=r.source_system,
                code=r.code,
                detail=r.detail,
                value=r.value,
                ours=(
                    ours.get(r.internal_id, Decimal(0))
                    if kind == "stock" and r.internal_id and not r.detail
                    else None
                ),
                matched=r.internal_id is not None,
                as_of=r.as_of,
            )
            for r in rows
        ],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.get("/integrations/snapshots/stock", response_model=Page[SnapshotRead])
def stock_snapshots(db: DbSession, _: CanStock, page: PageParamsDep) -> Page[SnapshotRead]:
    return _snapshots(db, "stock", page)


@router.get("/integrations/snapshots/balances", response_model=Page[SnapshotRead])
def balance_snapshots(db: DbSession, _: CanBooks, page: PageParamsDep) -> Page[SnapshotRead]:
    return _snapshots(db, "balance", page)


def _download(content: bytes, name: str) -> Response:
    return Response(
        content,
        media_type=CSV,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/exports/journal.csv")
def export_journal(db: DbSession, _: CanBooks, date_from: date, date_to: date) -> Response:
    entitlements.require_feature(db, "data_export")
    return _download(exports.journal(db, date_from, date_to), f"journal-{date_from}-{date_to}.csv")


@router.get("/exports/stock.csv")
def export_stock(db: DbSession, user: CanStock) -> Response:
    entitlements.require_feature(db, "data_export")
    return _download(exports.stock(db, with_cost=user.has("reports:profit")), "stock.csv")


# --- Push (API keys, not user sessions) -----------------------------------------------------------


@router.post("/ingest/files", response_model=PushResult, status_code=status.HTTP_201_CREATED)
def push_file(
    db: DbSession,
    key: keys.KeyDep,
    file: Annotated[UploadFile, File()],
    data: Annotated[str, Form(max_length=30)],
) -> PushResult:
    """A CSV or XLSX file. Records wait for a person to approve them."""
    is_snapshot = data in service.SNAPSHOTS
    keys.require_scope(key, "snapshots:write" if is_snapshot else "files:write")
    actor = CurrentUser(
        id=key.created_by_id or 0,
        username="",
        full_name="",
        roles=(),
        permissions=frozenset(),
    )
    outcome = service.stage_file(
        db,
        data_kind=data,
        source=key.source_system,
        content=file.file.read(parsing.MAX_BYTES + 1),
        actor=actor,
    )
    db.commit()
    return PushResult(result=outcome)


@router.post("/ingest/snapshots", response_model=PushResult)
def push_snapshot(db: DbSession, key: keys.KeyDep, data: SnapshotPush) -> PushResult:
    keys.require_scope(key, "snapshots:write")
    outcome = service.save_snapshot(db, data.kind, key.source_system, data.rows)
    db.commit()
    return PushResult(result=outcome)
