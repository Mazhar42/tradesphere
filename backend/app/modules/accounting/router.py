from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.db import DbSession
from app.core.permissions import CurrentUser, require
from app.modules.accounting.models import Account
from app.shared.schemas import ReadSchema

router = APIRouter(tags=["accounting"])


class AccountRead(ReadSchema):
    id: int
    code: str
    name_en: str
    name_bn: str | None
    type: str
    parent_id: int | None
    is_system: bool
    is_postable: bool
    party_type: str | None
    is_active: bool


@router.get("/accounts", response_model=list[AccountRead])
def list_accounts(
    db: DbSession, _: Annotated[CurrentUser, Depends(require("finance:read"))]
) -> list[Account]:
    return list(db.scalars(select(Account).order_by(Account.code)).all())
