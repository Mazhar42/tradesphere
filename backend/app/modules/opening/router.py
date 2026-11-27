"""Opening balances: the one-off load of the books, built on the stock and journal engines."""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.db import DbSession
from app.core.permissions import CurrentUser, require
from app.modules.accounting.schemas import OpeningBalanceRead, OpeningBalancesCreate
from app.modules.opening import service as opening
from app.shared.idempotency import IdempotencyKeyHeader, run_idempotent

router = APIRouter(tags=["accounting"])

CanOpen = Annotated[CurrentUser, Depends(require("finance:opening"))]


@router.get("/opening-balances", response_model=list[OpeningBalanceRead])
def list_opening_balances(db: DbSession, _: CanOpen) -> list[OpeningBalanceRead]:
    return [OpeningBalanceRead.model_validate(o) for o in opening.list_all(db)]


@router.post(
    "/opening-balances", response_model=OpeningBalanceRead, status_code=status.HTTP_201_CREATED
)
def post_opening_balances(
    db: DbSession, actor: CanOpen, data: OpeningBalancesCreate, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint="POST /opening-balances",
        payload=data.model_dump(mode="json"),
        status_code=201,
        handler=lambda: OpeningBalanceRead.model_validate(opening.post_opening(db, data, actor)),
    )
    db.commit()
    return result


@router.post("/opening-balances/{opening_id}/reverse", response_model=OpeningBalanceRead)
def reverse_opening_balances(
    db: DbSession, actor: CanOpen, opening_id: int, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint=f"POST /opening-balances/{opening_id}/reverse",
        payload={"id": opening_id},
        handler=lambda: OpeningBalanceRead.model_validate(
            opening.reverse_opening(db, opening_id, actor)
        ),
    )
    db.commit()
    return result
