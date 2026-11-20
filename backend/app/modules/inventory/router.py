from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.db import DbSession
from app.core.permissions import CurrentUser, require
from app.modules.inventory.models import Location
from app.shared.schemas import ReadSchema

router = APIRouter(tags=["inventory"])


class LocationRead(ReadSchema):
    id: int
    code: str
    name_en: str
    name_bn: str | None
    is_sellable: bool
    is_active: bool


@router.get("/locations", response_model=list[LocationRead])
def list_locations(
    db: DbSession, _: Annotated[CurrentUser, Depends(require("inventory:read"))]
) -> list[Location]:
    return list(db.scalars(select(Location).order_by(Location.id)).all())
