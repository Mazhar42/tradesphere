from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select

from app.core.db import DbSession
from app.core.permissions import CurrentUser, require
from app.modules.purchasing import returns
from app.modules.purchasing.models import PurchaseReturn
from app.modules.purchasing.return_schemas import PurchaseReturnCreate, PurchaseReturnRead
from app.shared.idempotency import IdempotencyKeyHeader, run_idempotent
from app.shared.pagination import PageParamsDep, paginate
from app.shared.schemas import Page

router = APIRouter(tags=["purchasing"])

CanRead = Annotated[CurrentUser, Depends(require("purchasing:read"))]
CanWrite = Annotated[CurrentUser, Depends(require("purchasing:write"))]
CanReverse = Annotated[CurrentUser, Depends(require("purchasing:reverse"))]


@router.get("/purchase-returns", response_model=Page[PurchaseReturnRead])
def list_purchase_returns(
    db: DbSession,
    _: CanRead,
    page: PageParamsDep,
    supplier_id: int | None = None,
    status_: Annotated[str | None, Query(alias="status", max_length=10)] = None,
) -> Page[PurchaseReturnRead]:
    stmt = select(PurchaseReturn).order_by(
        PurchaseReturn.business_date.desc(), PurchaseReturn.id.desc()
    )
    if supplier_id is not None:
        stmt = stmt.where(PurchaseReturn.supplier_id == supplier_id)
    if status_:
        stmt = stmt.where(PurchaseReturn.status == status_)
    rows, total = paginate(db, stmt, page)
    return Page(
        items=[returns.return_to_read(r) for r in rows],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post(
    "/purchase-returns", response_model=PurchaseReturnRead, status_code=status.HTTP_201_CREATED
)
def create_purchase_return(
    db: DbSession, actor: CanWrite, data: PurchaseReturnCreate
) -> PurchaseReturnRead:
    ret = returns.create_return(db, data, actor)
    db.commit()
    return returns.return_to_read(ret)


@router.get("/purchase-returns/{return_id}", response_model=PurchaseReturnRead)
def get_purchase_return(db: DbSession, _: CanRead, return_id: int) -> PurchaseReturnRead:
    return returns.return_to_read(returns.get_return(db, return_id))


@router.delete("/purchase-returns/{return_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_purchase_return(db: DbSession, actor: CanWrite, return_id: int) -> Response:
    returns.delete_return(db, return_id, actor)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/purchase-returns/{return_id}/post", response_model=PurchaseReturnRead)
def post_purchase_return(
    db: DbSession, actor: CanWrite, return_id: int, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint=f"POST /purchase-returns/{return_id}/post",
        payload={"id": return_id},
        handler=lambda: returns.return_to_read(returns.post_return(db, return_id, actor)),
    )
    db.commit()
    return result


@router.post("/purchase-returns/{return_id}/reverse", response_model=PurchaseReturnRead)
def reverse_purchasing_return(
    db: DbSession, actor: CanReverse, return_id: int, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint=f"POST /purchase-returns/{return_id}/reverse",
        payload={"id": return_id},
        handler=lambda: returns.return_to_read(returns.reverse_return(db, return_id, actor)),
    )
    db.commit()
    return result
