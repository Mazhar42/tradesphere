"""Stock adjustments (counts, write-offs) and transfers between locations.

Adjustment journal (docs/plan.md section 3.2, events 10, 19)
- Gain:  Dr Inventory / Cr Stock Adjustment (5400).
- Loss:  Dr Stock Adjustment (5400) / Cr Inventory.
- Reasons `damage` and `expiry` are write-offs and use Inventory Write-off (5100) instead.
Gains are valued at the current weighted average cost, losses at what was actually removed.
Transfers keep the cost and make no journal entry (same inventory account).
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.accounting.journal import INVENTORY, JLine, post_journal
from app.modules.catalog.models import Product
from app.modules.inventory.doc_schemas import (
    AdjustmentCreate,
    AdjustmentItemRead,
    AdjustmentRead,
    TransferCreate,
    TransferItemRead,
    TransferRead,
)
from app.modules.inventory.models import (
    Batch,
    StockAdjustment,
    StockAdjustmentItem,
    StockTransfer,
    StockTransferItem,
)
from app.modules.inventory.stock import get_location_id, lock_costs, stock_in, stock_out
from app.modules.settings.service import ensure_period_open
from app.shared.audit import record_audit
from app.shared.dates import business_date as today
from app.shared.dates import now_utc
from app.shared.money import ZERO, round_money
from app.shared.sequences import next_doc_number

ADJUSTMENT_DOC = "ADJ"
TRANSFER_DOC = "TRF"
WRITE_OFF = "5100"
SHRINKAGE = "5400"


def check_product_and_batch(
    session: Session, product_id: int, batch_id: int | None, index: int
) -> Product:
    product = session.get(Product, product_id)
    if product is None or not product.is_active:
        raise BusinessRuleError(
            "Unknown or inactive product.",
            errors=[{"field": f"items.{index}.product_id", "message": "Unknown product"}],
        )
    if batch_id is None:
        if product.track_batch:
            raise BusinessRuleError(
                f"{product.sku}: choose the batch.",
                errors=[{"field": f"items.{index}.batch_id", "message": "Batch required"}],
            )
    else:
        batch = session.get(Batch, batch_id)
        if batch is None or batch.product_id != product.id:
            raise BusinessRuleError(
                f"{product.sku}: that batch belongs to a different product.",
                errors=[{"field": f"items.{index}.batch_id", "message": "Wrong batch"}],
            )
    return product


# --- Adjustments ----------------------------------------------------------------------------
def get_adjustment(session: Session, adjustment_id: int) -> StockAdjustment:
    adjustment = session.get(StockAdjustment, adjustment_id)
    if adjustment is None:
        raise NotFoundError("Adjustment not found.")
    return adjustment


def create_adjustment(
    session: Session, data: AdjustmentCreate, actor: CurrentUser
) -> StockAdjustment:
    adjustment = StockAdjustment(
        business_date=data.business_date or today(),
        reason=data.reason,
        status="draft",
        notes=data.notes,
        created_by_id=actor.id,
    )
    items = []
    for index, line in enumerate(data.items):
        if line.qty_delta == 0:
            raise BusinessRuleError(
                "A quantity change cannot be zero.",
                errors=[{"field": f"items.{index}.qty_delta", "message": "Cannot be zero"}],
            )
        check_product_and_batch(session, line.product_id, line.batch_id, index)
        items.append(
            StockAdjustmentItem(
                product_id=line.product_id,
                batch_id=line.batch_id,
                location_id=get_location_id(session, line.location),
                qty_delta=line.qty_delta,
            )
        )
    adjustment.items = items
    session.add(adjustment)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="create",
        entity_type="stock_adjustment",
        entity_id=adjustment.id,
        after={"reason": data.reason, "lines": len(items)},
    )
    return adjustment


def delete_adjustment(session: Session, adjustment_id: int, actor: CurrentUser) -> None:
    adjustment = get_adjustment(session, adjustment_id)
    if adjustment.status != "draft":
        raise ConflictError("Only draft adjustments can be deleted.", code="not_draft")
    session.delete(adjustment)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="delete",
        entity_type="stock_adjustment",
        entity_id=adjustment_id,
    )


def post_adjustment(session: Session, adjustment_id: int, actor: CurrentUser) -> StockAdjustment:
    adjustment = session.scalar(
        select(StockAdjustment)
        .where(StockAdjustment.id == adjustment_id)
        .with_for_update(of=StockAdjustment)
        .execution_options(populate_existing=True)
    )
    if adjustment is None:
        raise NotFoundError("Adjustment not found.")
    if adjustment.status != "draft":
        raise ConflictError("This adjustment is already posted.", code="not_draft")
    ensure_period_open(session, adjustment.business_date)

    adjustment.doc_no = next_doc_number(session, ADJUSTMENT_DOC, adjustment.business_date)
    session.flush()
    costs = lock_costs(session, {i.product_id for i in adjustment.items})
    gains = ZERO
    losses = ZERO
    for item in adjustment.items:
        common = {
            "product_id": item.product_id,
            "location_id": item.location_id,
            "batch_id": item.batch_id,
            "business_date": adjustment.business_date,
            "movement_type": "adjustment",
            "source_doc_type": "stock_adjustment",
            "source_doc_id": adjustment.id,
            "source_doc_no": adjustment.doc_no,
            "source_line_id": item.id,
            "actor_id": actor.id,
        }
        if item.qty_delta < 0:
            move = stock_out(session, qty=-item.qty_delta, **common)  # type: ignore[arg-type]
            item.value = move.value  # negative
            losses += -move.value
        else:
            value = round_money(item.qty_delta * costs[item.product_id].avg_cost)
            stock_in(session, qty=item.qty_delta, value=value, **common)  # type: ignore[arg-type]
            item.value = value
            gains += value

    account = WRITE_OFF if adjustment.reason in ("damage", "expiry") else SHRINKAGE
    lines: list[JLine] = []
    if gains > 0:
        lines.append(JLine(INVENTORY, debit=gains, memo=f"Stock gain {adjustment.doc_no}"))
        lines.append(JLine(account, credit=gains, memo=f"Stock gain {adjustment.doc_no}"))
    if losses > 0:
        lines.append(JLine(account, debit=losses, memo=f"Stock loss {adjustment.doc_no}"))
        lines.append(JLine(INVENTORY, credit=losses, memo=f"Stock loss {adjustment.doc_no}"))
    if lines:
        post_journal(
            session,
            business_date=adjustment.business_date,
            source_doc_type="stock_adjustment",
            source_doc_id=adjustment.id,
            source_doc_no=adjustment.doc_no,
            memo=f"Stock adjustment {adjustment.doc_no} ({adjustment.reason})",
            lines=lines,
            actor_id=actor.id,
        )
    adjustment.status = "posted"
    adjustment.posted_at = now_utc()
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="post",
        entity_type="stock_adjustment",
        entity_id=adjustment.id,
        after={"doc_no": adjustment.doc_no, "gains": gains, "losses": losses},
    )
    return adjustment


def adjustment_to_read(adjustment: StockAdjustment) -> AdjustmentRead:
    return AdjustmentRead(
        id=adjustment.id,
        doc_no=adjustment.doc_no,
        business_date=adjustment.business_date,
        reason=adjustment.reason,
        status=adjustment.status,
        notes=adjustment.notes,
        posted_at=adjustment.posted_at,
        items=[
            AdjustmentItemRead(
                id=i.id,
                product_id=i.product_id,
                sku=i.product.sku,
                name_en=i.product.name_en,
                name_bn=i.product.name_bn,
                base_uom_code=i.product.base_uom.code,
                batch_id=i.batch_id,
                batch_no=i.batch.batch_no if i.batch else None,
                location=i.location.code,
                qty_delta=i.qty_delta,
                value=i.value,
            )
            for i in adjustment.items
        ],
    )


# --- Transfers -------------------------------------------------------------------------------
def get_transfer(session: Session, transfer_id: int) -> StockTransfer:
    transfer = session.get(StockTransfer, transfer_id)
    if transfer is None:
        raise NotFoundError("Transfer not found.")
    return transfer


def create_transfer(session: Session, data: TransferCreate, actor: CurrentUser) -> StockTransfer:
    if data.from_location == data.to_location:
        raise BusinessRuleError(
            "Choose two different locations.",
            errors=[{"field": "to_location", "message": "Same as the source"}],
        )
    transfer = StockTransfer(
        business_date=data.business_date or today(),
        from_location_id=get_location_id(session, data.from_location),
        to_location_id=get_location_id(session, data.to_location),
        status="draft",
        notes=data.notes,
        created_by_id=actor.id,
    )
    items = []
    for index, line in enumerate(data.items):
        check_product_and_batch(session, line.product_id, line.batch_id, index)
        items.append(
            StockTransferItem(
                product_id=line.product_id, batch_id=line.batch_id, qty_base=line.qty_base
            )
        )
    transfer.items = items
    session.add(transfer)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="create",
        entity_type="stock_transfer",
        entity_id=transfer.id,
        after={"from": data.from_location, "to": data.to_location, "lines": len(items)},
    )
    return transfer


def delete_transfer(session: Session, transfer_id: int, actor: CurrentUser) -> None:
    transfer = get_transfer(session, transfer_id)
    if transfer.status != "draft":
        raise ConflictError("Only draft transfers can be deleted.", code="not_draft")
    session.delete(transfer)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="delete",
        entity_type="stock_transfer",
        entity_id=transfer_id,
    )


def post_transfer(session: Session, transfer_id: int, actor: CurrentUser) -> StockTransfer:
    transfer = session.scalar(
        select(StockTransfer)
        .where(StockTransfer.id == transfer_id)
        .with_for_update(of=StockTransfer)
        .execution_options(populate_existing=True)
    )
    if transfer is None:
        raise NotFoundError("Transfer not found.")
    if transfer.status != "draft":
        raise ConflictError("This transfer is already posted.", code="not_draft")
    ensure_period_open(session, transfer.business_date)

    transfer.doc_no = next_doc_number(session, TRANSFER_DOC, transfer.business_date)
    session.flush()
    lock_costs(session, {i.product_id for i in transfer.items})
    for item in transfer.items:
        common = {
            "product_id": item.product_id,
            "batch_id": item.batch_id,
            "business_date": transfer.business_date,
            "source_doc_type": "stock_transfer",
            "source_doc_id": transfer.id,
            "source_doc_no": transfer.doc_no,
            "source_line_id": item.id,
            "actor_id": actor.id,
        }
        out = stock_out(
            session,
            location_id=transfer.from_location_id,
            qty=item.qty_base,
            movement_type="transfer_out",
            **common,  # type: ignore[arg-type]
        )
        value: Decimal = -out.value
        stock_in(
            session,
            location_id=transfer.to_location_id,
            qty=item.qty_base,
            value=value,
            movement_type="transfer_in",
            **common,  # type: ignore[arg-type]
        )
        item.value = value
    transfer.status = "posted"
    transfer.posted_at = now_utc()
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="post",
        entity_type="stock_transfer",
        entity_id=transfer.id,
        after={"doc_no": transfer.doc_no},
    )
    return transfer


def transfer_to_read(transfer: StockTransfer) -> TransferRead:
    return TransferRead(
        id=transfer.id,
        doc_no=transfer.doc_no,
        business_date=transfer.business_date,
        from_location=transfer.from_location.code,
        to_location=transfer.to_location.code,
        status=transfer.status,
        notes=transfer.notes,
        posted_at=transfer.posted_at,
        items=[
            TransferItemRead(
                id=i.id,
                product_id=i.product_id,
                sku=i.product.sku,
                name_en=i.product.name_en,
                name_bn=i.product.name_bn,
                base_uom_code=i.product.base_uom.code,
                batch_id=i.batch_id,
                batch_no=i.batch.batch_no if i.batch else None,
                qty_base=i.qty_base,
                value=i.value,
            )
            for i in transfer.items
        ],
    )
