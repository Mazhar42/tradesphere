from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.db import DbSession
from app.core.permissions import CurrentUser, require
from app.modules.catalog import service
from app.modules.catalog.models import Brand, Category, Uom
from app.modules.catalog.schemas import (
    BrandCreate,
    BrandRead,
    BrandUpdate,
    CategoryCreate,
    CategoryRead,
    CategoryUpdate,
    ProductCreate,
    ProductRead,
    ProductUpdate,
    UomCreate,
    UomRead,
    UomUpdate,
)
from app.shared.pagination import PageParamsDep
from app.shared.schemas import Page

router = APIRouter(tags=["catalog"])

CanRead = Annotated[CurrentUser, Depends(require("catalog:read"))]
CanWrite = Annotated[CurrentUser, Depends(require("catalog:write"))]
SearchQ = Annotated[str | None, Query(max_length=100)]


# --- Categories -------------------------------------------------------------
@router.get("/categories", response_model=list[CategoryRead])
def list_categories(
    db: DbSession, _: CanRead, q: SearchQ = None, is_active: bool | None = None
) -> list[Category]:
    return list(service.list_simple(db, Category, q, is_active))


@router.post("/categories", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
def create_category(db: DbSession, actor: CanWrite, data: CategoryCreate) -> Category:
    obj = service.create_simple(db, Category, data.model_dump(), actor)
    db.commit()
    return obj


@router.patch("/categories/{category_id}", response_model=CategoryRead)
def update_category(
    db: DbSession, actor: CanWrite, category_id: int, data: CategoryUpdate
) -> Category:
    obj = service.update_simple(
        db, Category, category_id, data.model_dump(exclude_unset=True), actor
    )
    db.commit()
    return obj


# --- Brands -----------------------------------------------------------------
@router.get("/brands", response_model=list[BrandRead])
def list_brands(
    db: DbSession, _: CanRead, q: SearchQ = None, is_active: bool | None = None
) -> list[Brand]:
    return list(service.list_simple(db, Brand, q, is_active))


@router.post("/brands", response_model=BrandRead, status_code=status.HTTP_201_CREATED)
def create_brand(db: DbSession, actor: CanWrite, data: BrandCreate) -> Brand:
    obj = service.create_simple(db, Brand, data.model_dump(), actor)
    db.commit()
    return obj


@router.patch("/brands/{brand_id}", response_model=BrandRead)
def update_brand(db: DbSession, actor: CanWrite, brand_id: int, data: BrandUpdate) -> Brand:
    obj = service.update_simple(db, Brand, brand_id, data.model_dump(exclude_unset=True), actor)
    db.commit()
    return obj


# --- Units of measure ---------------------------------------------------------
@router.get("/uoms", response_model=list[UomRead])
def list_uoms(
    db: DbSession, _: CanRead, q: SearchQ = None, is_active: bool | None = None
) -> list[Uom]:
    return list(service.list_simple(db, Uom, q, is_active))


@router.post("/uoms", response_model=UomRead, status_code=status.HTTP_201_CREATED)
def create_uom(db: DbSession, actor: CanWrite, data: UomCreate) -> Uom:
    obj = service.create_simple(db, Uom, data.model_dump(), actor)
    db.commit()
    return obj


@router.patch("/uoms/{uom_id}", response_model=UomRead)
def update_uom(db: DbSession, actor: CanWrite, uom_id: int, data: UomUpdate) -> Uom:
    obj = service.update_simple(db, Uom, uom_id, data.model_dump(exclude_unset=True), actor)
    db.commit()
    return obj


# --- Products -----------------------------------------------------------------
@router.get("/products", response_model=Page[ProductRead])
def list_products(
    db: DbSession,
    _: CanRead,
    page: PageParamsDep,
    q: SearchQ = None,
    category_id: int | None = None,
    brand_id: int | None = None,
    is_active: bool | None = None,
) -> Page[ProductRead]:
    items, total = service.list_products(
        db, page, q=q, category_id=category_id, brand_id=brand_id, is_active=is_active
    )
    return Page(
        items=[service.product_to_read(p) for p in items],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post("/products", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
def create_product(db: DbSession, actor: CanWrite, data: ProductCreate) -> ProductRead:
    product = service.create_product(db, data, actor)
    db.commit()
    return service.product_to_read(product)


@router.get("/products/{product_id}", response_model=ProductRead)
def get_product(db: DbSession, _: CanRead, product_id: int) -> ProductRead:
    return service.product_to_read(service.get_product(db, product_id))


@router.patch("/products/{product_id}", response_model=ProductRead)
def update_product(
    db: DbSession, actor: CanWrite, product_id: int, data: ProductUpdate
) -> ProductRead:
    product = service.update_product(db, product_id, data, actor)
    db.commit()
    return service.product_to_read(product)
