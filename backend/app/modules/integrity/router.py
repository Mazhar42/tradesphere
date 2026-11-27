from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.db import DbSession
from app.core.permissions import CurrentUser, require
from app.modules.integrity import reconcile as checks
from app.modules.inventory.schemas import ReconcileRead

router = APIRouter(tags=["inventory"])


@router.get("/inventory/reconcile", response_model=ReconcileRead)
def reconcile(
    db: DbSession,
    _: Annotated[CurrentUser, Depends(require("finance:read"))],
    fresh: bool = False,
) -> ReconcileRead:
    """The last stored result (nightly, or the last time someone asked). `fresh` checks now."""
    if not fresh:
        stored = checks.last_result(db)
        if stored is not None:
            return stored
    result = checks.run_and_store(db)
    db.commit()
    return result
