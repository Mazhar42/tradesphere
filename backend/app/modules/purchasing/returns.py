"""Purchase returns: goods sent back to the company (docs/plan.md section 3.2, event 12).

Stock leaves its location at the weighted average cost. The agreed value V is either credited to
the supplier account (Dr Accounts Payable) or claimed (Dr Claims Receivable, with a claimable
item). The difference between V and the book cost is Scheme Income (4300) when V is higher and
Rejected Claims Write-off (5300) when it is lower.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.accounting.journal import (
    CLAIMS_RECEIVABLE,
    INVENTORY,
    SUPPLIER_PAYABLE,
    JLine,
    post_journal,
)
from app.modules.claims.service import create_claimable
from app.modules.inventory.documents import check_product_and_batch
from app.modules.inventory.stock import get_location_id, lock_costs, stock_out
from app.modules.parties.models import Supplier
from app.modules.purchasing.models import PurchaseReturn, PurchaseReturnItem
from app.modules.purchasing.return_schemas import (
    PurchaseReturnCreate,
    PurchaseReturnItemRead,
    PurchaseReturnRead,
)
from app.modules.settings.service import ensure_period_open
from app.shared.audit import record_audit
from app.shared.dates import business_date as today
from app.shared.dates import now_utc
from app.shared.money import ZERO, round_money
from app.shared.sequences import next_doc_number

DOC_TYPE = "purchase_return"
DOC_PREFIX = "PR"
SCHEME_INCOME = "4300"
REJECTED_WRITE_OFF = "5300"
CLAIM_TYPE_BY_LOCATION = {"DAMAGED": "damage", "EXPIRED": "expiry", "SELLABLE": "other"}


def get_return(session: Session, return_id: int) -> PurchaseReturn:
    ret = session.get(PurchaseReturn, return_id)
    if ret is None:
        raise NotFoundError("Purchase return not found.")
    return ret


def create_return(
    session: Session, data: PurchaseReturnCreate, actor: CurrentUser
) -> PurchaseReturn:
    supplier = session.get(Supplier, data.supplier_id)
    if supplier is None or not supplier.is_active:
        raise BusinessRuleError(
            "Unknown or inactive company.",
            errors=[{"field": "supplier_id", "message": "Unknown company"}],
        )
    ret = PurchaseReturn(
        supplier_id=supplier.id,
        grn_id=data.grn_id,
        business_date=data.business_date or today(),
        status="draft",
        notes=data.notes,
        created_by_id=actor.id,
    )
    items = []
    for index, line in enumerate(data.items):
        check_product_and_batch(session, line.product_id, line.batch_id, index)
        items.append(
            PurchaseReturnItem(
                product_id=line.product_id,
                batch_id=line.batch_id,
                location_id=get_location_id(session, line.location),
                qty_base=line.qty_base,
                unit_value=line.unit_value,
                settlement=line.settlement,
                value=round_money(line.qty_base * line.unit_value),
            )
        )
    ret.items = items
    ret.total_value = sum((i.value for i in items), ZERO)
    session.add(ret)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="create",
        entity_type="purchase_return",
        entity_id=ret.id,
        after={"supplier_id": supplier.id, "total_value": ret.total_value},
    )
    return ret


def delete_return(session: Session, return_id: int, actor: CurrentUser) -> None:
    ret = get_return(session, return_id)
    if ret.status != "draft":
        raise ConflictError("Only draft returns can be deleted.", code="not_draft")
    session.delete(ret)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="delete",
        entity_type="purchase_return",
        entity_id=return_id,
    )


def post_return(  # noqa: PLR0912
    session: Session, return_id: int, actor: CurrentUser
) -> PurchaseReturn:
    ret = session.scalar(
        select(PurchaseReturn)
        .where(PurchaseReturn.id == return_id)
        .with_for_update(of=PurchaseReturn)
        .execution_options(populate_existing=True)
    )
    if ret is None:
        raise NotFoundError("Purchase return not found.")
    if ret.status != "draft":
        raise ConflictError("This return is already posted.", code="not_draft")
    ensure_period_open(session, ret.business_date)

    ret.doc_no = next_doc_number(session, DOC_PREFIX, ret.business_date)
    session.flush()
    lock_costs(session, {i.product_id for i in ret.items})

    credit_value = ZERO  # Dr Accounts Payable
    claim_value = ZERO  # Dr Claims Receivable
    cost_total = ZERO
    income = ZERO
    write_off = ZERO
    for item in ret.items:
        move = stock_out(
            session,
            product_id=item.product_id,
            location_id=item.location_id,
            qty=item.qty_base,
            batch_id=item.batch_id,
            business_date=ret.business_date,
            movement_type="purchase_return",
            source_doc_type=DOC_TYPE,
            source_doc_id=ret.id,
            source_doc_no=ret.doc_no,
            source_line_id=item.id,
            actor_id=actor.id,
        )
        item.cost = -move.value
        cost_total += item.cost
        difference = item.value - item.cost
        if difference > 0:
            income += difference
        else:
            write_off += -difference
        if item.settlement == "claim":
            claim_value += item.value
            create_claimable(
                session,
                supplier_id=ret.supplier_id,
                claim_type=CLAIM_TYPE_BY_LOCATION.get(item.location.code, "other"),
                source_doc_type=DOC_TYPE,
                source_doc_id=ret.id,
                source_line_id=item.id,
                business_date=ret.business_date,
                book_amount=item.value,  # what was debited to claims receivable
                claim_amount=item.value,
                actor_id=actor.id,
            )
        else:
            credit_value += item.value

    lines: list[JLine] = []
    if credit_value > 0:
        lines.append(
            JLine(
                SUPPLIER_PAYABLE,
                debit=credit_value,
                party_type="supplier",
                party_id=ret.supplier_id,
                memo=f"Goods returned {ret.doc_no}",
            )
        )
    if claim_value > 0:
        lines.append(
            JLine(
                CLAIMS_RECEIVABLE,
                debit=claim_value,
                party_type="supplier",
                party_id=ret.supplier_id,
                memo=f"Goods returned {ret.doc_no} (claim)",
            )
        )
    if write_off > 0:
        lines.append(JLine(REJECTED_WRITE_OFF, debit=write_off, memo=f"Return {ret.doc_no} short"))
    if cost_total > 0:
        lines.append(JLine(INVENTORY, credit=cost_total, memo=f"Stock returned {ret.doc_no}"))
    if income > 0:
        lines.append(JLine(SCHEME_INCOME, credit=income, memo=f"Return {ret.doc_no} above cost"))
    if lines:
        post_journal(
            session,
            business_date=ret.business_date,
            source_doc_type=DOC_TYPE,
            source_doc_id=ret.id,
            source_doc_no=ret.doc_no,
            memo=f"Purchase return {ret.doc_no}",
            lines=lines,
            actor_id=actor.id,
        )
    ret.total_value = sum((i.value for i in ret.items), ZERO)
    ret.total_cost = cost_total
    ret.status = "posted"
    ret.posted_at = now_utc()
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="post",
        entity_type="purchase_return",
        entity_id=ret.id,
        after={"doc_no": ret.doc_no, "value": ret.total_value, "cost": cost_total},
    )
    return ret


def return_to_read(ret: PurchaseReturn) -> PurchaseReturnRead:
    return PurchaseReturnRead(
        id=ret.id,
        doc_no=ret.doc_no,
        supplier_id=ret.supplier_id,
        supplier_name=ret.supplier.name,
        grn_id=ret.grn_id,
        business_date=ret.business_date,
        status=ret.status,
        notes=ret.notes,
        total_value=ret.total_value,
        total_cost=ret.total_cost,
        posted_at=ret.posted_at,
        items=[
            PurchaseReturnItemRead(
                id=i.id,
                product_id=i.product_id,
                sku=i.product.sku,
                name_en=i.product.name_en,
                name_bn=i.product.name_bn,
                base_uom_code=i.product.base_uom.code,
                batch_id=i.batch_id,
                batch_no=i.batch.batch_no if i.batch else None,
                location=i.location.code,
                qty_base=i.qty_base,
                unit_value=i.unit_value,
                settlement=i.settlement,
                value=i.value,
                cost=i.cost,
            )
            for i in ret.items
        ],
    )
