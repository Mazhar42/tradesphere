from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.db import DbSession
from app.core.permissions import CurrentUser, require
from app.modules.inventory import service
from app.modules.inventory.models import Batch, Location
from app.modules.inventory.schemas import (
    BatchRead,
    BatchStockRow,
    LocationRead,
    MovementPage,
    StockRow,
    StockTotals,
)
from app.modules.settings.schemas import Preferences
from app.modules.settings.service import get_setting
from app.shared.pagination import PageParamsDep
from app.shared.schemas import Page

router = APIRouter(tags=["inventory"])

CanRead = Annotated[CurrentUser, Depends(require("inventory:read"))]
SearchQ = Annotated[str | None, Query(max_length=100)]


@router.get("/locations", response_model=list[LocationRead])
def list_locations(db: DbSession, _: CanRead) -> list[Location]:
    return list(db.scalars(select(Location).order_by(Location.id)).all())


@router.get("/inventory/stock", response_model=Page[StockRow])
def stock_summary(
    db: DbSession,
    user: CanRead,
    page: PageParamsDep,
    q: SearchQ = None,
    category_id: int | None = None,
    low_only: bool = False,
    in_stock_only: bool = False,
) -> Page[StockRow]:
    rows, total = service.stock_summary(
        db,
        page,
        q=q,
        low_only=low_only,
        in_stock_only=in_stock_only,
        category_id=category_id,
        show_cost=user.has("reports:profit"),
    )
    return Page(items=rows, total=total, page=page.page, page_size=page.page_size)


@router.get("/inventory/stock/totals", response_model=StockTotals)
def stock_totals(db: DbSession, user: CanRead) -> StockTotals:
    days = get_setting(db, "preferences", Preferences).near_expiry_days
    return service.stock_totals(db, days, show_cost=user.has("reports:profit"))


@router.get("/inventory/stock/batches", response_model=Page[BatchStockRow])
def batch_stock(
    db: DbSession,
    _: CanRead,
    page: PageParamsDep,
    product_id: int | None = None,
    location: Annotated[str | None, Query(max_length=20)] = None,
    expiring_within_days: Annotated[int | None, Query(ge=0, le=3650)] = None,
    q: SearchQ = None,
) -> Page[BatchStockRow]:
    rows, total = service.batch_stock(
        db,
        page,
        product_id=product_id,
        location_code=location,
        expiring_within_days=expiring_within_days,
        q=q,
    )
    return Page(items=rows, total=total, page=page.page, page_size=page.page_size)


@router.get("/inventory/products/{product_id}/movements", response_model=MovementPage)
def product_movements(
    db: DbSession,
    user: CanRead,
    product_id: int,
    after: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> MovementPage:
    return service.movements(
        db, product_id, after=after, limit=limit, show_cost=user.has("reports:profit")
    )


@router.get("/inventory/batches", response_model=list[BatchRead])
def list_batches(db: DbSession, _: CanRead, product_id: Annotated[int, Query()]) -> list[Batch]:
    return list(
        db.scalars(
            select(Batch)
            .where(Batch.product_id == product_id)
            .order_by(Batch.expiry_date.desc().nulls_last(), Batch.id.desc())
            .limit(50)
        ).all()
    )
