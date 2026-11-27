from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select

from app.core.db import DbSession
from app.core.permissions import CurrentUser, require
from app.modules.claims import reads, reversals, service
from app.modules.claims.models import CommissionPayment
from app.modules.claims.schemas import (
    ClaimableRead,
    CommissionCreate,
    CommissionRead,
    CompanyClaimCreate,
    CompanyClaimListItem,
    CompanyClaimRead,
    CompanyClaimSubmit,
    RejectionCreate,
    SettlementCreate,
)
from app.shared.idempotency import IdempotencyKeyHeader, run_idempotent
from app.shared.pagination import PageParamsDep, paginate
from app.shared.schemas import Page

router = APIRouter(tags=["claims"])

CanRead = Annotated[CurrentUser, Depends(require("claims:read"))]
CanWrite = Annotated[CurrentUser, Depends(require("claims:write"))]
CanReverse = Annotated[CurrentUser, Depends(require("claims:reverse"))]


@router.get("/claimable-items", response_model=Page[ClaimableRead])
def list_claimable_items(
    db: DbSession,
    _: CanRead,
    page: PageParamsDep,
    supplier_id: int | None = None,
    status_: Annotated[str | None, Query(alias="status", max_length=20)] = None,
    claim_type: Annotated[str | None, Query(max_length=20)] = None,
) -> Page[ClaimableRead]:
    rows, total = service.list_claimables(
        db, page, supplier_id=supplier_id, status=status_, claim_type=claim_type
    )
    items = []
    for row in rows:
        item, supplier_name = row
        items.append(reads.claimable_read(item, supplier_name))
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.get("/company-claims", response_model=Page[CompanyClaimListItem])
def list_company_claims(
    db: DbSession,
    _: CanRead,
    page: PageParamsDep,
    supplier_id: int | None = None,
    status_: Annotated[str | None, Query(alias="status", max_length=20)] = None,
) -> Page[CompanyClaimListItem]:
    rows, total = service.list_claims(db, page, status=status_, supplier_id=supplier_id)
    items = []
    for row in rows:
        claim, supplier_name = row
        items.append(reads.claim_list_item(claim, supplier_name))
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.post(
    "/company-claims", response_model=CompanyClaimRead, status_code=status.HTTP_201_CREATED
)
def create_company_claim(
    db: DbSession, actor: CanWrite, data: CompanyClaimCreate
) -> CompanyClaimRead:
    claim = service.create_claim(db, data, actor)
    db.commit()
    return reads.claim_to_read(db, claim)


@router.get("/company-claims/{claim_id}", response_model=CompanyClaimRead)
def get_company_claim(db: DbSession, _: CanRead, claim_id: int) -> CompanyClaimRead:
    return reads.claim_to_read(db, service.get_claim(db, claim_id))


@router.delete("/company-claims/{claim_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_company_claim(db: DbSession, actor: CanWrite, claim_id: int) -> Response:
    service.delete_claim(db, claim_id, actor)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/company-claims/{claim_id}/submit", response_model=CompanyClaimRead)
def submit_company_claim(
    db: DbSession,
    actor: CanWrite,
    claim_id: int,
    data: CompanyClaimSubmit,
    key: IdempotencyKeyHeader,
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint=f"POST /company-claims/{claim_id}/submit",
        payload={"id": claim_id, **data.model_dump(mode="json")},
        handler=lambda: reads.claim_to_read(db, service.submit_claim(db, claim_id, data, actor)),
    )
    db.commit()
    return result


@router.post(
    "/company-claims/{claim_id}/settlements",
    response_model=CompanyClaimRead,
    status_code=status.HTTP_201_CREATED,
)
def settle_company_claim(
    db: DbSession,
    actor: CanWrite,
    claim_id: int,
    data: SettlementCreate,
    key: IdempotencyKeyHeader,
) -> object:
    def handler() -> CompanyClaimRead:
        service.settle_claim(db, claim_id, data, actor)
        return reads.claim_to_read(db, service.get_claim(db, claim_id))

    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint=f"POST /company-claims/{claim_id}/settlements",
        payload={"id": claim_id, **data.model_dump(mode="json")},
        status_code=201,
        handler=handler,
    )
    db.commit()
    return result


@router.post("/company-claims/{claim_id}/reject-lines", response_model=CompanyClaimRead)
def reject_company_claim_lines(
    db: DbSession,
    actor: CanWrite,
    claim_id: int,
    data: RejectionCreate,
    key: IdempotencyKeyHeader,
) -> object:
    def handler() -> CompanyClaimRead:
        service.reject_claim_lines(db, claim_id, data, actor)
        return reads.claim_to_read(db, service.get_claim(db, claim_id))

    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint=f"POST /company-claims/{claim_id}/reject-lines",
        payload={"id": claim_id, **data.model_dump(mode="json")},
        handler=handler,
    )
    db.commit()
    return result


@router.get("/commission-payments", response_model=Page[CommissionRead])
def list_commission_payments(
    db: DbSession, _: CanRead, page: PageParamsDep
) -> Page[CommissionRead]:
    rows, total = paginate(
        db,
        select(CommissionPayment).order_by(
            CommissionPayment.business_date.desc(), CommissionPayment.id.desc()
        ),
        page,
    )
    return Page(
        items=[reads.commission_read(r) for r in rows],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post(
    "/commission-payments", response_model=CommissionRead, status_code=status.HTTP_201_CREATED
)
def create_commission_payment(
    db: DbSession, actor: CanWrite, data: CommissionCreate, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint="POST /commission-payments",
        payload=data.model_dump(mode="json"),
        status_code=201,
        handler=lambda: reads.commission_read(service.create_commission(db, data, actor)),
    )
    db.commit()
    return result


# --- Reversals ------------------------------------------------------------------------------
@router.post(
    "/company-claims/{claim_id}/settlements/{settlement_id}/reverse",
    response_model=CompanyClaimRead,
)
def reverse_claim_settlement(
    db: DbSession,
    actor: CanReverse,
    claim_id: int,
    settlement_id: int,
    key: IdempotencyKeyHeader,
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint=f"POST /company-claims/{claim_id}/settlements/{settlement_id}/reverse",
        payload={},
        handler=lambda: reads.claim_to_read(
            db, reversals.reverse_settlement(db, claim_id, settlement_id, actor)
        ),
    )
    db.commit()
    return result


@router.post("/commission-payments/{commission_id}/reverse", response_model=CommissionRead)
def reverse_commission_payment(
    db: DbSession, actor: CanReverse, commission_id: int, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint=f"POST /commission-payments/{commission_id}/reverse",
        payload={},
        handler=lambda: reads.commission_read(
            reversals.reverse_commission(db, commission_id, actor)
        ),
    )
    db.commit()
    return result
