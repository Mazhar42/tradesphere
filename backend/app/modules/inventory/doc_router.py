from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select

from app.core.db import DbSession
from app.core.permissions import CurrentUser, require
from app.modules.inventory import documents
from app.modules.inventory.doc_schemas import (
    AdjustmentCreate,
    AdjustmentRead,
    TransferCreate,
    TransferRead,
)
from app.modules.inventory.models import StockAdjustment, StockTransfer
from app.shared.idempotency import IdempotencyKeyHeader, run_idempotent
from app.shared.pagination import PageParamsDep, paginate
from app.shared.schemas import Page

router = APIRouter(tags=["inventory"])

CanAdjust = Annotated[CurrentUser, Depends(require("inventory:adjust"))]
CanRead = Annotated[CurrentUser, Depends(require("inventory:read"))]


# --- Stock adjustments (including write-offs) --------------------------------------------------
@router.get("/stock-adjustments", response_model=Page[AdjustmentRead])
def list_stock_adjustments(db: DbSession, _: CanRead, page: PageParamsDep) -> Page[AdjustmentRead]:
    rows, total = paginate(
        db,
        select(StockAdjustment).order_by(
            StockAdjustment.business_date.desc(), StockAdjustment.id.desc()
        ),
        page,
    )
    return Page(
        items=[documents.adjustment_to_read(r) for r in rows],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post(
    "/stock-adjustments", response_model=AdjustmentRead, status_code=status.HTTP_201_CREATED
)
def create_stock_adjustment(
    db: DbSession, actor: CanAdjust, data: AdjustmentCreate
) -> AdjustmentRead:
    adjustment = documents.create_adjustment(db, data, actor)
    db.commit()
    return documents.adjustment_to_read(adjustment)


@router.get("/stock-adjustments/{adjustment_id}", response_model=AdjustmentRead)
def get_stock_adjustment(db: DbSession, _: CanRead, adjustment_id: int) -> AdjustmentRead:
    return documents.adjustment_to_read(documents.get_adjustment(db, adjustment_id))


@router.delete("/stock-adjustments/{adjustment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_stock_adjustment(db: DbSession, actor: CanAdjust, adjustment_id: int) -> Response:
    documents.delete_adjustment(db, adjustment_id, actor)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/stock-adjustments/{adjustment_id}/post", response_model=AdjustmentRead)
def post_stock_adjustment(
    db: DbSession, actor: CanAdjust, adjustment_id: int, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint=f"POST /stock-adjustments/{adjustment_id}/post",
        payload={"id": adjustment_id},
        handler=lambda: documents.adjustment_to_read(
            documents.post_adjustment(db, adjustment_id, actor)
        ),
    )
    db.commit()
    return result


# --- Stock transfers ---------------------------------------------------------------------------
@router.get("/stock-transfers", response_model=Page[TransferRead])
def list_stock_transfers(db: DbSession, _: CanRead, page: PageParamsDep) -> Page[TransferRead]:
    rows, total = paginate(
        db,
        select(StockTransfer).order_by(StockTransfer.business_date.desc(), StockTransfer.id.desc()),
        page,
    )
    return Page(
        items=[documents.transfer_to_read(r) for r in rows],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post("/stock-transfers", response_model=TransferRead, status_code=status.HTTP_201_CREATED)
def create_stock_transfer(db: DbSession, actor: CanAdjust, data: TransferCreate) -> TransferRead:
    transfer = documents.create_transfer(db, data, actor)
    db.commit()
    return documents.transfer_to_read(transfer)


@router.get("/stock-transfers/{transfer_id}", response_model=TransferRead)
def get_stock_transfer(db: DbSession, _: CanRead, transfer_id: int) -> TransferRead:
    return documents.transfer_to_read(documents.get_transfer(db, transfer_id))


@router.delete("/stock-transfers/{transfer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_stock_transfer(db: DbSession, actor: CanAdjust, transfer_id: int) -> Response:
    documents.delete_transfer(db, transfer_id, actor)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/stock-transfers/{transfer_id}/post", response_model=TransferRead)
def post_stock_transfer(
    db: DbSession, actor: CanAdjust, transfer_id: int, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint=f"POST /stock-transfers/{transfer_id}/post",
        payload={"id": transfer_id},
        handler=lambda: documents.transfer_to_read(documents.post_transfer(db, transfer_id, actor)),
    )
    db.commit()
    return result
