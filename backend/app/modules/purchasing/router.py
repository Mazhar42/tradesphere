from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.db import DbSession
from app.core.permissions import CurrentUser, require
from app.modules.accounting.schemas import LedgerEntryRead
from app.modules.accounting.service import party_ledger
from app.modules.purchasing import payments, receiving, service
from app.modules.purchasing.schemas import (
    AllocateRequest,
    GrnCreate,
    GrnListItem,
    GrnRead,
    POCreate,
    PODetail,
    POListItem,
    POUpdate,
    SupplierPaymentCreate,
    SupplierPaymentRead,
    SupplierSummary,
)
from app.shared.idempotency import IdempotencyKeyHeader, run_idempotent
from app.shared.pagination import PageParamsDep
from app.shared.schemas import Page

router = APIRouter(tags=["purchasing"])

CanRead = Annotated[CurrentUser, Depends(require("purchasing:read"))]
CanWrite = Annotated[CurrentUser, Depends(require("purchasing:write"))]
CanPay = Annotated[CurrentUser, Depends(require("purchasing:pay"))]
CanReverse = Annotated[CurrentUser, Depends(require("purchasing:reverse"))]
SearchQ = Annotated[str | None, Query(max_length=100)]


# --- Purchase orders -----------------------------------------------------------------------
@router.get("/purchase-orders", response_model=Page[POListItem])
def list_purchase_orders(
    db: DbSession,
    _: CanRead,
    page: PageParamsDep,
    q: SearchQ = None,
    supplier_id: int | None = None,
    status_: Annotated[str | None, Query(alias="status", max_length=20)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[POListItem]:
    items, total = service.list_pos(
        db,
        page,
        q=q,
        supplier_id=supplier_id,
        status=status_,
        date_from=date_from,
        date_to=date_to,
    )
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.post("/purchase-orders", response_model=PODetail, status_code=status.HTTP_201_CREATED)
def create_purchase_order(db: DbSession, actor: CanWrite, data: POCreate) -> PODetail:
    po = service.create_po(db, data, actor)
    db.commit()
    return service.po_to_detail(db, po)


@router.get("/purchase-orders/{po_id}", response_model=PODetail)
def get_purchase_order(db: DbSession, _: CanRead, po_id: int) -> PODetail:
    return service.po_to_detail(db, service.get_po(db, po_id))


@router.put("/purchase-orders/{po_id}", response_model=PODetail)
def update_purchase_order(db: DbSession, actor: CanWrite, po_id: int, data: POUpdate) -> PODetail:
    po = service.update_po(db, po_id, data, actor)
    db.commit()
    return service.po_to_detail(db, po)


@router.delete("/purchase-orders/{po_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_purchase_order(db: DbSession, actor: CanWrite, po_id: int) -> Response:
    service.delete_draft_po(db, po_id, actor)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/purchase-orders/{po_id}/confirm", response_model=PODetail)
def confirm_purchase_order(db: DbSession, actor: CanWrite, po_id: int) -> PODetail:
    po = service.confirm_po(db, po_id, actor)
    db.commit()
    return service.po_to_detail(db, po)


@router.post("/purchase-orders/{po_id}/close-short", response_model=PODetail)
def close_purchase_order_short(db: DbSession, actor: CanWrite, po_id: int) -> PODetail:
    po = service.close_short(db, po_id, actor)
    db.commit()
    return service.po_to_detail(db, po)


@router.post("/purchase-orders/{po_id}/cancel", response_model=PODetail)
def cancel_purchase_order(db: DbSession, actor: CanWrite, po_id: int) -> PODetail:
    po = service.cancel_po(db, po_id, actor)
    db.commit()
    return service.po_to_detail(db, po)


# --- Goods receipts ------------------------------------------------------------------------
@router.get("/goods-receipts", response_model=Page[GrnListItem])
def list_goods_receipts(
    db: DbSession,
    _: CanRead,
    page: PageParamsDep,
    q: SearchQ = None,
    po_id: int | None = None,
    supplier_id: int | None = None,
    status_: Annotated[str | None, Query(alias="status", max_length=10)] = None,
) -> Page[GrnListItem]:
    items, total = receiving.list_grns(
        db, page, q=q, po_id=po_id, supplier_id=supplier_id, status=status_
    )
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.post("/goods-receipts", response_model=GrnRead, status_code=status.HTTP_201_CREATED)
def create_goods_receipt(db: DbSession, actor: CanWrite, data: GrnCreate) -> GrnRead:
    grn = receiving.create_draft(db, data, actor)
    db.commit()
    return receiving.grn_to_read(grn)


@router.get("/goods-receipts/{grn_id}", response_model=GrnRead)
def get_goods_receipt(db: DbSession, _: CanRead, grn_id: int) -> GrnRead:
    return receiving.grn_to_read(receiving.get_grn(db, grn_id))


@router.delete("/goods-receipts/{grn_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_goods_receipt(db: DbSession, actor: CanWrite, grn_id: int) -> Response:
    receiving.delete_draft(db, grn_id, actor)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/goods-receipts/{grn_id}/post", response_model=GrnRead)
def post_goods_receipt(
    db: DbSession, actor: CanWrite, grn_id: int, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint=f"POST /goods-receipts/{grn_id}/post",
        payload={"id": grn_id},
        handler=lambda: receiving.grn_to_read(receiving.post_grn(db, grn_id, actor)),
    )
    db.commit()
    return result


@router.post("/goods-receipts/{grn_id}/reverse", response_model=GrnRead)
def reverse_goods_receipt(
    db: DbSession, actor: CanReverse, grn_id: int, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint=f"POST /goods-receipts/{grn_id}/reverse",
        payload={"id": grn_id},
        handler=lambda: receiving.grn_to_read(receiving.reverse_grn(db, grn_id, actor)),
    )
    db.commit()
    return result


# --- Supplier payments -------------------------------------------------------------------------
@router.get("/supplier-payments", response_model=Page[SupplierPaymentRead])
def list_supplier_payments(
    db: DbSession,
    _: CanPay,
    page: PageParamsDep,
    q: SearchQ = None,
    supplier_id: int | None = None,
    po_id: int | None = None,
    status_: Annotated[str | None, Query(alias="status", max_length=10)] = None,
) -> Page[SupplierPaymentRead]:
    items, total = payments.list_payments(
        db, page, q=q, supplier_id=supplier_id, po_id=po_id, status=status_
    )
    return Page(
        items=[payments.payment_to_read(p) for p in items],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post(
    "/supplier-payments", response_model=SupplierPaymentRead, status_code=status.HTTP_201_CREATED
)
def create_supplier_payment(
    db: DbSession, actor: CanPay, data: SupplierPaymentCreate, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint="POST /supplier-payments",
        payload=data.model_dump(mode="json"),
        status_code=201,
        handler=lambda: payments.payment_to_read(payments.create_payment(db, data, actor)),
    )
    db.commit()
    return result


@router.get("/supplier-payments/{payment_id}", response_model=SupplierPaymentRead)
def get_supplier_payment(db: DbSession, _: CanPay, payment_id: int) -> SupplierPaymentRead:
    return payments.payment_to_read(payments.get_payment(db, payment_id))


@router.post("/supplier-payments/{payment_id}/allocate", response_model=SupplierPaymentRead)
def allocate_supplier_payment(
    db: DbSession, actor: CanPay, payment_id: int, data: AllocateRequest
) -> SupplierPaymentRead:
    payment = payments.allocate_payment(db, payment_id, data.allocations, actor)
    db.commit()
    return payments.payment_to_read(payment)


@router.post("/supplier-payments/{payment_id}/reverse", response_model=SupplierPaymentRead)
def reverse_supplier_payment(
    db: DbSession, actor: CanReverse, payment_id: int, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint=f"POST /supplier-payments/{payment_id}/reverse",
        payload={"id": payment_id},
        handler=lambda: payments.payment_to_read(payments.reverse_payment(db, payment_id, actor)),
    )
    db.commit()
    return result


# --- Supplier account ----------------------------------------------------------------------------
@router.get("/suppliers/{supplier_id}/summary", response_model=SupplierSummary)
def supplier_summary(db: DbSession, _: CanPay, supplier_id: int) -> SupplierSummary:
    return payments.supplier_summary(db, supplier_id)


@router.get("/suppliers/{supplier_id}/ledger", response_model=Page[LedgerEntryRead])
def supplier_ledger(
    db: DbSession, _: CanPay, supplier_id: int, page: PageParamsDep
) -> Page[LedgerEntryRead]:
    service.get_supplier(db, supplier_id, must_be_active=False)
    items, total = party_ledger(
        db, page, account_code="2000", party_type="supplier", party_id=supplier_id
    )
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)
