"""Goods receipts (shipments against a purchase order).

A receipt is a draft until posted. Posting is one atomic operation: it validates every
line against what is still undelivered, moves stock (valued at the PO price; free goods at
zero, damaged units into the DAMAGED location), posts Dr Inventory / Cr Supplier account,
and advances the PO status. Reversal undoes all of it (blocked once the stock has moved).
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.accounting.journal import INVENTORY, SUPPLIER_PAYABLE, JLine, post_journal
from app.modules.accounting.journal import reverse_entries as reverse_journal
from app.modules.inventory.batches import resolve_batch
from app.modules.inventory.stock import (
    get_location_id,
    lock_costs,
    reverse_movements,
    stock_in,
)
from app.modules.parties.models import Supplier
from app.modules.purchasing.models import GoodsReceipt, GoodsReceiptItem, PurchaseOrder
from app.modules.purchasing.schemas import GrnCreate, GrnItemRead, GrnListItem, GrnRead
from app.modules.purchasing.service import lock_po, refresh_status
from app.modules.settings.service import ensure_period_open
from app.shared.audit import record_audit
from app.shared.dates import business_date as today
from app.shared.dates import now_utc
from app.shared.money import ZERO, round_money
from app.shared.pagination import PageParams, paginate
from app.shared.search import like_pattern
from app.shared.sequences import next_doc_number

DOC_TYPE = "GRN"
RECEIVABLE_PO_STATUSES = ("confirmed", "partially_received")


# --- Reads -------------------------------------------------------------------------------
def grn_to_read(grn: GoodsReceipt) -> GrnRead:
    return GrnRead(
        id=grn.id,
        doc_no=grn.doc_no,
        po_id=grn.po_id,
        po_doc_no=grn.po.doc_no,
        supplier_id=grn.supplier_id,
        supplier_name=grn.supplier.name,
        business_date=grn.business_date,
        challan_no=grn.challan_no,
        vehicle_no=grn.vehicle_no,
        notes=grn.notes,
        status=grn.status,
        total_value=grn.total_value,
        posted_at=grn.posted_at,
        created_at=grn.created_at,
        items=[
            GrnItemRead(
                id=i.id,
                po_item_id=i.po_item_id,
                product_id=i.product_id,
                sku=i.product.sku,
                name_en=i.product.name_en,
                name_bn=i.product.name_bn,
                batch_no=i.batch_no,
                mfg_date=i.mfg_date,
                expiry_date=i.expiry_date,
                received_qty_base=i.received_qty_base,
                free_qty_base=i.free_qty_base,
                damaged_qty_base=i.damaged_qty_base,
                value=i.value,
            )
            for i in grn.items
        ],
    )


def get_grn(session: Session, grn_id: int) -> GoodsReceipt:
    grn = session.get(GoodsReceipt, grn_id)
    if grn is None:
        raise NotFoundError("Goods receipt not found.")
    return grn


def _lock_grn(session: Session, grn_id: int) -> GoodsReceipt:
    grn = session.scalar(
        select(GoodsReceipt)
        .where(GoodsReceipt.id == grn_id)
        .with_for_update(of=GoodsReceipt)
        .execution_options(populate_existing=True)
    )
    if grn is None:
        raise NotFoundError("Goods receipt not found.")
    return grn


def list_grns(
    session: Session,
    params: PageParams,
    *,
    q: str | None,
    po_id: int | None,
    supplier_id: int | None,
    status: str | None,
) -> tuple[list[GrnListItem], int]:
    stmt = (
        select(GoodsReceipt)
        .join(Supplier, Supplier.id == GoodsReceipt.supplier_id)
        .order_by(GoodsReceipt.business_date.desc(), GoodsReceipt.id.desc())
    )
    if q:
        like = like_pattern(q)
        stmt = stmt.where(
            or_(
                GoodsReceipt.doc_no.ilike(like),
                GoodsReceipt.challan_no.ilike(like),
                Supplier.name.ilike(like),
            )
        )
    if po_id is not None:
        stmt = stmt.where(GoodsReceipt.po_id == po_id)
    if supplier_id is not None:
        stmt = stmt.where(GoodsReceipt.supplier_id == supplier_id)
    if status:
        stmt = stmt.where(GoodsReceipt.status == status)
    grns, total = paginate(session, stmt, params)
    return [
        GrnListItem(
            id=g.id,
            doc_no=g.doc_no,
            po_id=g.po_id,
            po_doc_no=g.po.doc_no,
            supplier_id=g.supplier_id,
            supplier_name=g.supplier.name,
            business_date=g.business_date,
            challan_no=g.challan_no,
            status=g.status,
            total_value=g.total_value,
            item_count=len(g.items),
        )
        for g in grns
    ], total


# --- Draft -------------------------------------------------------------------------------
def _check_against_po(
    po: PurchaseOrder, data_items: list[tuple[int, Decimal]], *, field: str = "items"
) -> None:
    """Each PO line may receive at most its remaining quantity (summed over this receipt)."""
    by_id = {i.id: i for i in po.items}
    taken: dict[int, Decimal] = {}
    for index, (po_item_id, qty) in enumerate(data_items):
        item = by_id.get(po_item_id)
        if item is None:
            raise BusinessRuleError(
                "A line does not belong to this purchase order.",
                errors=[{"field": f"{field}.{index}.po_item_id", "message": "Not on this order"}],
            )
        taken[po_item_id] = taken.get(po_item_id, ZERO) + qty
        if taken[po_item_id] > item.remaining_qty_base:
            raise BusinessRuleError(
                f"{item.product.sku} - {item.product.name_en}: receiving {taken[po_item_id]:f} "
                f"but only {item.remaining_qty_base:f} is still undelivered.",
                code="over_receipt",
                errors=[
                    {
                        "field": f"{field}.{index}.received_qty_base",
                        "message": "More than undelivered",
                    }
                ],
            )


def create_draft(session: Session, data: GrnCreate, actor: CurrentUser) -> GoodsReceipt:
    po = session.get(PurchaseOrder, data.po_id)
    if po is None:
        raise BusinessRuleError(
            "Unknown purchase order.", errors=[{"field": "po_id", "message": "Unknown order"}]
        )
    if po.status not in RECEIVABLE_PO_STATUSES:
        raise ConflictError(
            f"Goods can only be received against a confirmed order (this one is {po.status}).",
            code="po_not_receivable",
        )
    _check_against_po(po, [(i.po_item_id, i.received_qty_base) for i in data.items])
    by_id = {i.id: i for i in po.items}
    grn = GoodsReceipt(
        po_id=po.id,
        supplier_id=po.supplier_id,
        business_date=data.business_date or today(),
        challan_no=data.challan_no,
        vehicle_no=data.vehicle_no,
        notes=data.notes,
        status="draft",
        created_by_id=actor.id,
        items=[
            GoodsReceiptItem(
                po_item_id=line.po_item_id,
                product_id=by_id[line.po_item_id].product_id,
                batch_no=(line.batch_no or "").strip() or None,
                mfg_date=line.mfg_date,
                expiry_date=line.expiry_date,
                received_qty_base=line.received_qty_base,
                free_qty_base=line.free_qty_base,
                damaged_qty_base=line.damaged_qty_base,
            )
            for line in data.items
        ],
    )
    session.add(grn)
    session.flush()
    session.refresh(grn)
    record_audit(
        session,
        actor_id=actor.id,
        action="create",
        entity_type="goods_receipt",
        entity_id=grn.id,
        after={"po_id": po.id, "lines": len(grn.items)},
    )
    return grn


def delete_draft(session: Session, grn_id: int, actor: CurrentUser) -> None:
    grn = _lock_grn(session, grn_id)
    if grn.status != "draft":
        raise ConflictError("Only draft receipts can be deleted.", code="not_draft")
    session.delete(grn)
    session.flush()
    record_audit(
        session, actor_id=actor.id, action="delete", entity_type="goods_receipt", entity_id=grn_id
    )


# --- Posting -------------------------------------------------------------------------------
def post_grn(session: Session, grn_id: int, actor: CurrentUser) -> GoodsReceipt:
    grn = _lock_grn(session, grn_id)
    if grn.status != "draft":
        raise ConflictError("This receipt is already posted.", code="not_draft")
    po = lock_po(session, grn.po_id)
    if po.status not in RECEIVABLE_PO_STATUSES:
        raise ConflictError(
            f"Goods can only be received against a confirmed order (this one is {po.status}).",
            code="po_not_receivable",
        )
    ensure_period_open(session, grn.business_date)
    _check_against_po(po, [(i.po_item_id, i.received_qty_base) for i in grn.items], field="items")

    grn.doc_no = next_doc_number(session, DOC_TYPE, grn.business_date)
    po_items = {i.id: i for i in po.items}
    lock_costs(session, {i.product_id for i in grn.items})
    sellable = get_location_id(session, "SELLABLE")
    damaged_location = get_location_id(session, "DAMAGED")

    total = ZERO
    for index, line in enumerate(grn.items):
        po_item = po_items[line.po_item_id]
        product = po_item.product
        qty = line.received_qty_base

        # Value of what was received: proportional to the PO line; the receipt that
        # completes the line takes the exact remainder so no paisa is lost to rounding.
        value = ZERO
        if qty > 0:
            if po_item.received_qty_base + qty == po_item.ordered_qty_base:
                value = po_item.line_value - po_item.received_value
            else:
                value = round_money(po_item.line_value * qty / po_item.ordered_qty_base)
            po_item.received_qty_base += qty
            po_item.received_value += value
        po_item.received_free_qty_base += line.free_qty_base

        batch = resolve_batch(
            session,
            product,
            batch_no=line.batch_no,
            mfg_date=line.mfg_date,
            expiry_date=line.expiry_date,
            received_on=grn.business_date,
            field_prefix=f"items.{index}.",
        )
        batch_id = batch.id if batch else None
        line.batch_id = batch_id
        line.value = value
        total += value

        common = {
            "product_id": product.id,
            "batch_id": batch_id,
            "business_date": grn.business_date,
            "source_doc_type": DOC_TYPE,
            "source_doc_id": grn.id,
            "source_doc_no": grn.doc_no,
            "source_line_id": line.id,
            "actor_id": actor.id,
        }
        good_qty = qty - line.damaged_qty_base
        good_value = round_money(value * good_qty / qty) if qty > 0 else ZERO
        if good_qty > 0:
            stock_in(
                session,
                location_id=sellable,
                qty=good_qty,
                value=good_value,
                movement_type="grn",
                **common,  # type: ignore[arg-type]
            )
        if line.damaged_qty_base > 0:
            stock_in(
                session,
                location_id=damaged_location,
                qty=line.damaged_qty_base,
                value=value - good_value,
                movement_type="grn",
                **common,  # type: ignore[arg-type]
            )
        if line.free_qty_base > 0:
            stock_in(
                session,
                location_id=sellable,
                qty=line.free_qty_base,
                value=ZERO,
                movement_type="grn_free",
                **common,  # type: ignore[arg-type]
            )

    if total > 0:
        post_journal(
            session,
            business_date=grn.business_date,
            source_doc_type=DOC_TYPE,
            source_doc_id=grn.id,
            source_doc_no=grn.doc_no,
            memo=f"Goods received {grn.doc_no} against {po.doc_no}",
            lines=[
                JLine(INVENTORY, debit=total, memo=f"Stock received {grn.doc_no}"),
                JLine(
                    SUPPLIER_PAYABLE,
                    credit=total,
                    party_type="supplier",
                    party_id=grn.supplier_id,
                    memo=f"Goods received {grn.doc_no} ({po.doc_no})",
                ),
            ],
            actor_id=actor.id,
        )

    grn.total_value = total
    grn.status = "posted"
    grn.posted_at = now_utc()
    refresh_status(po)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="post",
        entity_type="goods_receipt",
        entity_id=grn.id,
        after={"doc_no": grn.doc_no, "total_value": total, "po_status": po.status},
    )
    return grn


def reverse_grn(session: Session, grn_id: int, actor: CurrentUser) -> GoodsReceipt:
    grn = _lock_grn(session, grn_id)
    if grn.status != "posted":
        raise ConflictError("Only posted receipts can be reversed.", code="not_posted")
    po = lock_po(session, grn.po_id)
    if po.status in ("closed_short", "cancelled"):
        raise ConflictError(
            f"The purchase order is {po.status}; its receipts cannot be reversed.",
            code="po_closed",
        )
    ensure_period_open(session, grn.business_date)
    on: date = today()
    reverse_movements(
        session,
        source_doc_type=DOC_TYPE,
        source_doc_id=grn.id,
        business_date=on,
        actor_id=actor.id,
    )
    reverse_journal(
        session,
        source_doc_type=DOC_TYPE,
        source_doc_id=grn.id,
        business_date=on,
        actor_id=actor.id,
    )
    po_items = {i.id: i for i in po.items}
    for line in grn.items:
        po_item = po_items[line.po_item_id]
        po_item.received_qty_base -= line.received_qty_base
        po_item.received_value -= line.value
        po_item.received_free_qty_base -= line.free_qty_base
    grn.status = "reversed"
    grn.reversed_at = now_utc()
    refresh_status(po)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="reverse",
        entity_type="goods_receipt",
        entity_id=grn.id,
        after={"doc_no": grn.doc_no, "po_status": po.status},
    )
    return grn
