"""Purchase orders: drafting, confirmation, closing and the paid/received/undelivered view."""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.catalog.models import Product
from app.modules.parties.models import Supplier
from app.modules.purchasing.models import (
    GoodsReceipt,
    PaymentPoAllocation,
    PurchaseOrder,
    PurchaseOrderItem,
    SupplierPayment,
)
from app.modules.purchasing.schemas import (
    PaymentRef,
    POCreate,
    PODetail,
    POItemRead,
    POLineIn,
    POListItem,
    POSummary,
    POUpdate,
    ReceiptRef,
)
from app.shared.audit import record_audit, snapshot
from app.shared.dates import business_date, now_utc
from app.shared.money import ZERO, round_money
from app.shared.pagination import PageParams, paginate
from app.shared.sequences import next_doc_number

QTY_Q = Decimal("0.001")
COST_Q = Decimal("0.0001")


# --- Helpers ----------------------------------------------------------------------------
def get_supplier(session: Session, supplier_id: int, *, must_be_active: bool = True) -> Supplier:
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise BusinessRuleError(
            "Unknown supplier.", errors=[{"field": "supplier_id", "message": "Unknown supplier"}]
        )
    if must_be_active and not supplier.is_active:
        raise BusinessRuleError(
            "This supplier is inactive.",
            errors=[{"field": "supplier_id", "message": "Inactive supplier"}],
        )
    return supplier


def lock_po(session: Session, po_id: int) -> PurchaseOrder:
    po = session.scalar(
        select(PurchaseOrder)
        .where(PurchaseOrder.id == po_id)
        .with_for_update(of=PurchaseOrder)
        .execution_options(populate_existing=True)
    )
    if po is None:
        raise NotFoundError("Purchase order not found.")
    return po


def get_po(session: Session, po_id: int) -> PurchaseOrder:
    po = session.get(PurchaseOrder, po_id)
    if po is None:
        raise NotFoundError("Purchase order not found.")
    return po


def _entry_unit(product: Product, uom_id: int | None, index: int) -> tuple[int, Decimal]:
    if uom_id is None or uom_id == product.base_uom_id:
        return product.base_uom_id, Decimal(1)
    for unit in product.uoms:
        if unit.uom_id == uom_id:
            return unit.uom_id, unit.factor_to_base
    raise BusinessRuleError(
        f"{product.sku}: that unit is not configured for this product.",
        errors=[{"field": f"items.{index}.uom_id", "message": "Unit not configured"}],
    )


def _build_items(session: Session, lines: Sequence[POLineIn]) -> list[PurchaseOrderItem]:
    ids = {line.product_id for line in lines}
    products = {p.id: p for p in session.scalars(select(Product).where(Product.id.in_(ids))).all()}
    items: list[PurchaseOrderItem] = []
    for index, line in enumerate(lines):
        product = products.get(line.product_id)
        if product is None or not product.is_active:
            raise BusinessRuleError(
                "Unknown or inactive product.",
                errors=[{"field": f"items.{index}.product_id", "message": "Unknown product"}],
            )
        uom_id, factor = _entry_unit(product, line.uom_id, index)
        ordered = line.qty * factor
        free = line.free_qty * factor
        if ordered != ordered.quantize(QTY_Q) or free != free.quantize(QTY_Q):
            raise BusinessRuleError(
                f"{product.sku}: quantity has too many decimals after unit conversion.",
                errors=[{"field": f"items.{index}.qty", "message": "Too many decimals"}],
            )
        items.append(
            PurchaseOrderItem(
                product_id=product.id,
                uom_id=uom_id,
                factor=factor,
                ordered_qty_base=ordered,
                free_qty_base=free,
                unit_price=line.unit_price,
                unit_cost=(line.unit_price / factor).quantize(COST_Q),
                line_value=round_money(line.qty * line.unit_price),
            )
        )
    return items


def _audit_view(po: PurchaseOrder) -> dict[str, object]:
    return snapshot(
        po,
        extra={
            "items": [
                {
                    "product_id": i.product_id,
                    "qty_base": i.ordered_qty_base,
                    "unit_cost": i.unit_cost,
                    "line_value": i.line_value,
                }
                for i in po.items
            ]
        },
    )


# --- Summary ----------------------------------------------------------------------------
def paid_by_po(session: Session, po_ids: Sequence[int]) -> dict[int, Decimal]:
    if not po_ids:
        return {}
    rows = session.execute(
        select(PaymentPoAllocation.po_id, func.sum(PaymentPoAllocation.amount))
        .join(SupplierPayment, SupplierPayment.id == PaymentPoAllocation.payment_id)
        .where(PaymentPoAllocation.po_id.in_(po_ids), SupplierPayment.status == "posted")
        .group_by(PaymentPoAllocation.po_id)
    ).all()
    return {po_id: Decimal(total) for po_id, total in rows}


def summarize(po: PurchaseOrder, paid: Decimal) -> POSummary:
    ordered = sum((i.line_value for i in po.items), ZERO)
    received = sum((i.received_value for i in po.items), ZERO)
    cancelled = sum((i.cancelled_value for i in po.items), ZERO)
    return POSummary(
        ordered_value=ordered,
        received_value=received,
        cancelled_value=cancelled,
        remaining_value=ordered - received - cancelled,
        paid_value=paid,
        balance_due=max(received - paid, ZERO),
        unapplied_advance=max(paid - received, ZERO),
    )


def payable_value(po: PurchaseOrder) -> Decimal:
    """The most that can sensibly be paid against this PO (ordered minus cancelled)."""
    return sum((i.line_value - i.cancelled_value for i in po.items), ZERO)


def _item_read(item: PurchaseOrderItem) -> POItemRead:
    return POItemRead(
        id=item.id,
        product_id=item.product_id,
        sku=item.product.sku,
        name_en=item.product.name_en,
        name_bn=item.product.name_bn,
        base_uom_code=item.product.base_uom.code,
        track_batch=item.product.track_batch,
        track_expiry=item.product.track_expiry,
        uom_id=item.uom_id,
        uom_code=item.uom.code,
        factor=item.factor,
        ordered_qty_base=item.ordered_qty_base,
        free_qty_base=item.free_qty_base,
        unit_price=item.unit_price,
        unit_cost=item.unit_cost,
        line_value=item.line_value,
        received_qty_base=item.received_qty_base,
        received_free_qty_base=item.received_free_qty_base,
        cancelled_qty_base=item.cancelled_qty_base,
        remaining_qty_base=item.remaining_qty_base,
        received_value=item.received_value,
        cancelled_value=item.cancelled_value,
        remaining_value=item.remaining_value,
    )


def po_to_detail(session: Session, po: PurchaseOrder) -> PODetail:
    paid = paid_by_po(session, [po.id]).get(po.id, ZERO)
    receipts = session.scalars(
        select(GoodsReceipt).where(GoodsReceipt.po_id == po.id).order_by(GoodsReceipt.id)
    ).all()
    allocations = session.execute(
        select(PaymentPoAllocation, SupplierPayment)
        .join(SupplierPayment, SupplierPayment.id == PaymentPoAllocation.payment_id)
        .where(PaymentPoAllocation.po_id == po.id)
        .order_by(SupplierPayment.business_date, SupplierPayment.id)
    ).all()
    return PODetail(
        id=po.id,
        doc_no=po.doc_no,
        supplier_id=po.supplier_id,
        supplier_name=po.supplier.name,
        business_date=po.business_date,
        expected_date=po.expected_date,
        status=po.status,
        notes=po.notes,
        created_at=po.created_at,
        confirmed_at=po.confirmed_at,
        closed_at=po.closed_at,
        items=[_item_read(i) for i in po.items],
        summary=summarize(po, paid),
        receipts=[
            ReceiptRef(
                id=g.id,
                doc_no=g.doc_no,
                business_date=g.business_date,
                status=g.status,
                total_value=g.total_value,
                challan_no=g.challan_no,
            )
            for g in receipts
        ],
        payments=[
            PaymentRef(
                payment_id=p.id,
                doc_no=p.doc_no,
                business_date=p.business_date,
                status=p.status,
                amount=a.amount,
            )
            for a, p in allocations
        ],
    )


def list_pos(
    session: Session,
    params: PageParams,
    *,
    q: str | None,
    supplier_id: int | None,
    status: str | None,
    date_from: date | None,
    date_to: date | None,
) -> tuple[list[POListItem], int]:
    stmt = (
        select(PurchaseOrder)
        .join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .order_by(PurchaseOrder.business_date.desc(), PurchaseOrder.id.desc())
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(PurchaseOrder.doc_no.ilike(like), Supplier.name.ilike(like)))
    if supplier_id is not None:
        stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
    if date_from:
        stmt = stmt.where(PurchaseOrder.business_date >= date_from)
    if date_to:
        stmt = stmt.where(PurchaseOrder.business_date <= date_to)
    pos, total = paginate(session, stmt, params)
    paid = paid_by_po(session, [p.id for p in pos])
    return [
        POListItem(
            id=p.id,
            doc_no=p.doc_no,
            supplier_id=p.supplier_id,
            supplier_name=p.supplier.name,
            business_date=p.business_date,
            expected_date=p.expected_date,
            status=p.status,
            item_count=len(p.items),
            summary=summarize(p, paid.get(p.id, ZERO)),
        )
        for p in pos
    ], total


# --- Lifecycle --------------------------------------------------------------------------
def create_po(session: Session, data: POCreate, actor: CurrentUser) -> PurchaseOrder:
    get_supplier(session, data.supplier_id)
    po = PurchaseOrder(
        supplier_id=data.supplier_id,
        business_date=data.business_date or business_date(),
        expected_date=data.expected_date,
        notes=data.notes,
        status="draft",
        created_by_id=actor.id,
        items=_build_items(session, data.items),
    )
    session.add(po)
    session.flush()
    session.refresh(po)
    record_audit(
        session,
        actor_id=actor.id,
        action="create",
        entity_type="purchase_order",
        entity_id=po.id,
        after=_audit_view(po),
    )
    return po


def update_po(session: Session, po_id: int, data: POUpdate, actor: CurrentUser) -> PurchaseOrder:
    po = lock_po(session, po_id)
    if po.status != "draft":
        raise ConflictError("Only draft purchase orders can be edited.", code="not_draft")
    get_supplier(session, data.supplier_id)
    before = _audit_view(po)
    po.supplier_id = data.supplier_id
    po.business_date = data.business_date or po.business_date
    po.expected_date = data.expected_date
    po.notes = data.notes
    po.items = _build_items(session, data.items)
    session.flush()
    session.refresh(po)
    record_audit(
        session,
        actor_id=actor.id,
        action="update",
        entity_type="purchase_order",
        entity_id=po.id,
        before=before,
        after=_audit_view(po),
    )
    return po


def delete_draft_po(session: Session, po_id: int, actor: CurrentUser) -> None:
    po = lock_po(session, po_id)
    if po.status != "draft":
        raise ConflictError("Only draft purchase orders can be deleted.", code="not_draft")
    before = _audit_view(po)
    session.delete(po)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="delete",
        entity_type="purchase_order",
        entity_id=po_id,
        before=before,
    )


def confirm_po(session: Session, po_id: int, actor: CurrentUser) -> PurchaseOrder:
    po = lock_po(session, po_id)
    if po.status != "draft":
        raise ConflictError("This purchase order is already confirmed.", code="not_draft")
    get_supplier(session, po.supplier_id)
    po.doc_no = next_doc_number(session, "PO", po.business_date)
    po.status = "confirmed"
    po.confirmed_at = now_utc()
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="confirm",
        entity_type="purchase_order",
        entity_id=po.id,
        after={"doc_no": po.doc_no, "status": po.status},
    )
    return po


def close_short(session: Session, po_id: int, actor: CurrentUser) -> PurchaseOrder:
    """The company will not ship the rest: cancel the undelivered quantity.

    Money already paid for the undelivered part stays as a supplier advance (it can be
    applied to another order or refunded) - it is never silently lost.
    """
    po = lock_po(session, po_id)
    if po.status not in ("confirmed", "partially_received"):
        raise ConflictError(
            "Only confirmed or partially received orders can be closed short.", code="bad_status"
        )
    if not any(i.received_qty_base > 0 for i in po.items):
        raise BusinessRuleError(
            "Nothing has been received on this order yet. Cancel it instead.",
            code="nothing_received",
        )
    cancelled = ZERO
    for item in po.items:
        remaining_qty = item.remaining_qty_base
        if remaining_qty > 0:
            remaining_value = item.remaining_value
            item.cancelled_qty_base += remaining_qty
            item.cancelled_value += remaining_value
            cancelled += remaining_value
    po.status = "closed_short"
    po.closed_at = now_utc()
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="close_short",
        entity_type="purchase_order",
        entity_id=po.id,
        after={"status": po.status, "cancelled_value": cancelled},
    )
    return po


def cancel_po(session: Session, po_id: int, actor: CurrentUser) -> PurchaseOrder:
    po = lock_po(session, po_id)
    if po.status != "confirmed":
        raise ConflictError(
            "Only confirmed orders with nothing received can be cancelled "
            "(delete drafts; close partially received orders short).",
            code="bad_status",
        )
    if any(i.received_qty_base > 0 for i in po.items):
        raise ConflictError("Goods were already received on this order.", code="has_receipts")
    for item in po.items:
        item.cancelled_qty_base = item.ordered_qty_base
        item.cancelled_value = item.line_value
    po.status = "cancelled"
    po.closed_at = now_utc()
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="cancel",
        entity_type="purchase_order",
        entity_id=po.id,
        after={"status": po.status},
    )
    return po


def refresh_status(po: PurchaseOrder) -> None:
    """Recompute received / partially_received / confirmed from the item quantities."""
    if po.status in ("draft", "closed_short", "cancelled"):
        return
    if all(i.received_qty_base + i.cancelled_qty_base >= i.ordered_qty_base for i in po.items):
        po.status = "received"
    elif any(i.received_qty_base > 0 for i in po.items):
        po.status = "partially_received"
    else:
        po.status = "confirmed"
