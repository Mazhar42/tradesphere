from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.db import DbSession
from app.core.permissions import CurrentUser, require
from app.modules.integrity.reconcile import reconcile as run_reconcile
from app.modules.inventory.schemas import ReconcileRead

router = APIRouter(tags=["inventory"])


@router.get("/inventory/reconcile", response_model=ReconcileRead)
def reconcile(
    db: DbSession, _: Annotated[CurrentUser, Depends(require("finance:read"))]
) -> ReconcileRead:
    return run_reconcile(db)
