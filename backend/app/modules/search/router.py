from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.db import DbSession
from app.core.errors import NotFoundError
from app.core.permissions import CurrentUser, CurrentUserDep, require
from app.modules.parties.models import Supplier
from app.modules.purchasing.models import SupplierPrice, SupplierProduct
from app.modules.search import service
from app.shared.pagination import PageParamsDep, paginate
from app.shared.schemas import Page, ReadSchema, WriteSchema
from app.shared.search import all_words

router = APIRouter(tags=["search"])

CanCatalog = Annotated[CurrentUser, Depends(require("catalog:read"))]
CanLink = Annotated[CurrentUser, Depends(require("catalog:write"))]
CanBuy = Annotated[CurrentUser, Depends(require("purchasing:read"))]


class HitRead(ReadSchema):
    id: int
    title: str
    subtitle: str
    url: str


class GroupRead(ReadSchema):
    group: str
    hits: list[HitRead]


class CandidateRead(ReadSchema):
    source: str
    confidence: str
    product_id: int | None
    supplier_product_id: int | None
    title: str
    detail: str


class SupplierProductRead(ReadSchema):
    id: int
    supplier_id: int
    supplier_name: str
    supplier_sku: str
    description: str
    product_id: int | None
    pack_factor: Decimal
    price: Decimal | None
    min_order_qty: Decimal | None
    valid_from: date | None
    source: str
    last_seen_at: datetime


class PriceRead(ReadSchema):
    price: Decimal
    valid_from: date | None
    source: str
    recorded_at: datetime


class LinkRequest(WriteSchema):
    product_id: int


@router.get("/search", response_model=list[GroupRead])
def global_search(
    db: DbSession,
    user: CurrentUserDep,
    q: Annotated[str, Query(min_length=2, max_length=100)],
) -> list[GroupRead]:
    """Up to five hits per kind, only for kinds this person may read."""
    return [
        GroupRead(group=name, hits=[HitRead.model_validate(h, from_attributes=True) for h in hits])
        for name, hits in service.search(db, q, user)
    ]


@router.get("/search/discover", response_model=list[CandidateRead])
def discover(
    db: DbSession, _: CanCatalog, q: Annotated[str, Query(min_length=2, max_length=100)]
) -> list[CandidateRead]:
    return [CandidateRead.model_validate(c, from_attributes=True) for c in service.discover(db, q)]


def _read(item: SupplierProduct, supplier: str) -> SupplierProductRead:
    return SupplierProductRead(
        id=item.id,
        supplier_id=item.supplier_id,
        supplier_name=supplier,
        supplier_sku=item.supplier_sku,
        description=item.description,
        product_id=item.product_id,
        pack_factor=item.pack_factor,
        price=item.price,
        min_order_qty=item.min_order_qty,
        valid_from=item.valid_from,
        source=item.source,
        last_seen_at=item.last_seen_at,
    )


@router.get("/supplier-products", response_model=Page[SupplierProductRead])
def list_supplier_products(
    db: DbSession,
    _: CanBuy,
    page: PageParamsDep,
    supplier_id: int | None = None,
    product_id: int | None = None,
    unlinked: bool = False,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[SupplierProductRead]:
    """`unlinked` is the review queue; with a product, what each company calls it and charges."""
    statement = select(SupplierProduct).order_by(SupplierProduct.id.desc())
    if supplier_id is not None:
        statement = statement.where(SupplierProduct.supplier_id == supplier_id)
    if product_id is not None:
        statement = statement.where(SupplierProduct.product_id == product_id)
    if unlinked:
        statement = statement.where(SupplierProduct.product_id.is_(None))
    if q:
        statement = statement.where(
            all_words(q, SupplierProduct.description, SupplierProduct.supplier_sku)
        )
    rows, total = paginate(db, statement, page)
    names = dict(
        db.execute(
            select(Supplier.id, Supplier.name).where(Supplier.id.in_({r.supplier_id for r in rows}))
        ).all()
    )
    return Page(
        items=[_read(r, names.get(r.supplier_id, "")) for r in rows],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.get("/supplier-products/{supplier_product_id}/prices", response_model=list[PriceRead])
def price_history(db: DbSession, _: CanBuy, supplier_product_id: int) -> list[PriceRead]:
    if (
        db.scalar(select(SupplierProduct.id).where(SupplierProduct.id == supplier_product_id))
        is None
    ):
        raise NotFoundError("Supplier line not found.")
    rows = db.scalars(
        select(SupplierPrice)
        .where(SupplierPrice.supplier_product_id == supplier_product_id)
        .order_by(SupplierPrice.id.desc())
        .limit(100)
    )
    return [PriceRead.model_validate(r) for r in rows]


@router.post("/supplier-products/{supplier_product_id}/link", response_model=SupplierProductRead)
def link_supplier_product(
    db: DbSession, actor: CanLink, supplier_product_id: int, data: LinkRequest
) -> SupplierProductRead:
    item = service.link(db, supplier_product_id, data.product_id, actor)
    supplier = db.scalar(select(Supplier.name).where(Supplier.id == item.supplier_id)) or ""
    result = _read(item, supplier)
    db.commit()
    return result
