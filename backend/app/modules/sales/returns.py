"""Sales returns: goods a customer brings back against a posted invoice.

Posting (docs/plan.md section 3.2, event 9)
- Stock comes back into the location of its condition (good: SELLABLE, damaged: DAMAGED,
  expired: EXPIRED) at the cost the line was sold at, so the cost of goods is reversed.
- The journal reverses the sale: Dr Sales Returns (4100) gross, Cr Sales Discounts (4200) the
  dealer discount, Cr Claims Receivable for the company-funded discount, and Cr the customer
  (credit) or the payment account (cash refund) for the net. Dr Inventory / Cr COGS at cost.
- Amounts are shared out per invoice line: each return takes its proportion, and the last
  return on a line takes the remainder, so the totals always match the invoice exactly.
"""

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.accounting.journal import (
    CLAIMS_RECEIVABLE,
    INVENTORY,
    RECEIVABLE,
    JLine,
    post_journal,
)
from app.modules.accounting.models import PaymentAccount
from app.modules.accounting.service import require_active_payment_account
from app.modules.claims.service import reduce_trade_discount
from app.modules.inventory.stock import get_location_id, lock_costs, stock_in
from app.modules.sales.ledger import lock_customer
from app.modules.sales.models import (
    CONDITION_LOCATION,
    SalesInvoice,
    SalesInvoiceItem,
    SalesReturn,
    SalesReturnItem,
)
from app.modules.sales.projections import apply_return_summary
from app.modules.sales.schemas import (
    ReturnableRow,
    ReturnItemRead,
    SalesReturnCreate,
    SalesReturnListItem,
    SalesReturnRead,
)
from app.modules.settings.service import ensure_period_open
from app.shared.audit import record_audit
from app.shared.dates import business_date as today
from app.shared.dates import now_utc
from app.shared.money import ZERO, round_money
from app.shared.pagination import PageParams, paginate
from app.shared.sequences import next_doc_number

DOC_TYPE = "sales_return"
DOC_PREFIX = "SR"
SALES_RETURNS = "4100"
SALES_DISCOUNTS = "4200"
COGS = "5000"

Key = tuple[int, int | None]  # (invoice line id, batch id or None for untracked stock)


def get_return(session: Session, return_id: int) -> SalesReturn:
    ret = session.get(SalesReturn, return_id)
    if ret is None:
        raise NotFoundError("Return not found.")
    return ret


def _lock_invoice(session: Session, invoice_id: int) -> SalesInvoice:
    invoice = session.scalar(
        select(SalesInvoice)
        .where(SalesInvoice.id == invoice_id)
        .with_for_update(of=SalesInvoice)
        .execution_options(populate_existing=True)
    )
    if invoice is None:
        raise NotFoundError("Invoice not found.")
    return invoice


def _sold_by_batch(invoice: SalesInvoice) -> dict[Key, Decimal]:
    sold: dict[Key, Decimal] = defaultdict(lambda: ZERO)
    for item in invoice.items:
        for allocation in item.batches:
            if not allocation.is_free:
                sold[(item.id, allocation.batch_id)] += allocation.qty_base
    return dict(sold)


def _returned_by_batch(session: Session, invoice_id: int) -> dict[Key, Decimal]:
    rows = session.execute(
        select(
            SalesReturnItem.invoice_item_id,
            SalesReturnItem.batch_id,
            func.sum(SalesReturnItem.qty_base),
        )
        .join(SalesReturn, SalesReturn.id == SalesReturnItem.return_id)
        .where(SalesReturn.invoice_id == invoice_id, SalesReturn.status == "posted")
        .group_by(SalesReturnItem.invoice_item_id, SalesReturnItem.batch_id)
    ).all()
    return {(row[0], row[1]): Decimal(row[2]) for row in rows}


def _check_quantities(
    session: Session, invoice: SalesInvoice, lines: list[tuple[int, int | None, Decimal]]
) -> None:
    """Every returned quantity must come from a batch sold on that line, and the total returned
    from each batch may not exceed what was sold from it."""
    items = {item.id: item for item in invoice.items}
    sold = _sold_by_batch(invoice)
    returned = _returned_by_batch(session, invoice.id)
    requested: dict[Key, Decimal] = defaultdict(lambda: ZERO)
    for item_id, batch_id, qty in lines:
        item = items.get(item_id)
        if item is None:
            raise BusinessRuleError(
                "A returned line is not on this invoice.",
                errors=[{"field": "items", "message": "Not on this invoice"}],
            )
        key = (item_id, batch_id)
        if key not in sold:
            raise BusinessRuleError(
                f"{item.product.sku}: these goods were not sold from that batch.",
                code="not_sold",
                errors=[{"field": "items", "message": "Not sold from this batch"}],
            )
        requested[key] += qty
    for key, qty in requested.items():
        available = sold[key] - returned.get(key, ZERO)
        if qty > available:
            item = items[key[0]]
            raise BusinessRuleError(
                f"{item.product.sku}: only {available:f} can still be returned from that batch "
                f"(asked for {qty:f}).",
                code="over_return",
                errors=[{"field": "items", "message": "More than was sold"}],
            )


def create_return(session: Session, data: SalesReturnCreate, actor: CurrentUser) -> SalesReturn:
    invoice = session.get(SalesInvoice, data.invoice_id)
    if invoice is None:
        raise NotFoundError("Invoice not found.")
    if invoice.status != "posted":
        raise ConflictError(
            "Goods can only be returned against a posted invoice.", code="not_posted"
        )
    if data.refund_method == "cash":
        if data.payment_account_id is None:
            raise BusinessRuleError(
                "Choose the account the refund is paid from.",
                errors=[{"field": "payment_account_id", "message": "Required"}],
            )
        require_active_payment_account(session, data.payment_account_id)
    elif data.payment_account_id is not None:
        raise BusinessRuleError(
            "A credit refund does not use a payment account.",
            errors=[{"field": "payment_account_id", "message": "Not used for credit"}],
        )
    lines = [(i.invoice_item_id, i.batch_id, i.qty_base) for i in data.items]
    _check_quantities(session, invoice, lines)

    items = {item.id: item for item in invoice.items}
    ret = SalesReturn(
        invoice_id=invoice.id,
        customer_id=invoice.customer_id,
        business_date=data.business_date or today(),
        status="draft",
        refund_method=data.refund_method,
        payment_account_id=data.payment_account_id,
        notes=data.notes,
        created_by_id=actor.id,
    )
    ret.items = [
        SalesReturnItem(
            invoice_item_id=line.invoice_item_id,
            product_id=items[line.invoice_item_id].product_id,
            batch_id=line.batch_id,
            condition=line.condition,
            qty_base=line.qty_base,
            unit_cost=items[line.invoice_item_id].unit_cost,
        )
        for line in data.items
    ]
    session.add(ret)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="create",
        entity_type="sales_return",
        entity_id=ret.id,
        after={"invoice_id": invoice.id, "refund_method": data.refund_method},
    )
    return ret


def post_return(  # noqa: PLR0915
    session: Session, return_id: int, actor: CurrentUser
) -> SalesReturn:
    draft = get_return(session, return_id)
    customer = lock_customer(session, draft.customer_id)
    invoice = _lock_invoice(session, draft.invoice_id)
    ret = session.scalar(
        select(SalesReturn)
        .where(SalesReturn.id == return_id)
        .with_for_update(of=SalesReturn)
        .execution_options(populate_existing=True)
    )
    assert ret is not None
    if ret.status != "draft":
        raise ConflictError("This return is already posted.", code="not_draft")
    if invoice.status != "posted":
        raise ConflictError("The invoice is no longer posted.", code="not_posted")
    ensure_period_open(session, ret.business_date)
    pa = (
        require_active_payment_account(session, ret.payment_account_id)
        if ret.refund_method == "cash" and ret.payment_account_id is not None
        else None
    )
    _check_quantities(
        session, invoice, [(i.invoice_item_id, i.batch_id, i.qty_base) for i in ret.items]
    )

    items = {item.id: item for item in invoice.items}
    lock_costs(session, {i.product_id for i in ret.items})
    # Running totals per invoice line: earlier returns plus what this return has already taken.
    prior = _prior_totals(session, invoice.id)
    running: dict[int, list[Decimal]] = {
        k: list(v) for k, v in prior.items()
    }  # qty,gross,dd,kd,cogs

    for it in ret.items:
        line = items[it.invoice_item_id]
        acc = running.setdefault(line.id, [ZERO, ZERO, ZERO, ZERO, ZERO])
        new_qty = acc[0] + it.qty_base
        if new_qty == line.qty_base:
            gross = line.gross_amount - acc[1]
            dealer = line.dealer_discount - acc[2]
            company = line.company_discount - acc[3]
            cogs = line.cogs - acc[4]
        else:
            share = it.qty_base / line.qty_base
            gross = round_money(line.gross_amount * share)
            dealer = round_money(line.dealer_discount * share)
            company = round_money(line.company_discount * share)
            cogs = round_money(line.cogs * share)
        acc[0] += it.qty_base
        acc[1] += gross
        acc[2] += dealer
        acc[3] += company
        acc[4] += cogs
        it.gross_amount = gross
        it.dealer_discount = dealer
        it.company_discount = company
        it.net_amount = gross - dealer - company
        it.cogs = cogs
        it.unit_cost = line.unit_cost

    ret.doc_no = next_doc_number(session, DOC_PREFIX, ret.business_date)
    session.flush()
    for it in ret.items:
        stock_in(
            session,
            product_id=it.product_id,
            location_id=get_location_id(session, CONDITION_LOCATION[it.condition]),
            qty=it.qty_base,
            value=it.cogs,
            business_date=ret.business_date,
            movement_type="sales_return",
            source_doc_type=DOC_TYPE,
            source_doc_id=ret.id,
            source_doc_no=ret.doc_no,
            source_line_id=it.id,
            batch_id=it.batch_id,
            actor_id=actor.id,
        )

    _post_return_journal(session, ret, items, pa, actor)
    for it in ret.items:
        if it.company_discount > 0:
            reduce_trade_discount(
                session,
                invoice_item_id=it.invoice_item_id,
                amount=it.company_discount,
                actor_id=actor.id,
            )

    ret.gross_amount = sum((i.gross_amount for i in ret.items), ZERO)
    ret.dealer_discount = sum((i.dealer_discount for i in ret.items), ZERO)
    ret.company_discount = sum((i.company_discount for i in ret.items), ZERO)
    ret.net_amount = sum((i.net_amount for i in ret.items), ZERO)
    ret.cogs = sum((i.cogs for i in ret.items), ZERO)
    ret.status = "posted"
    ret.posted_at = now_utc()
    apply_return_summary(session, ret)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="post",
        entity_type="sales_return",
        entity_id=ret.id,
        after={
            "doc_no": ret.doc_no,
            "gross_amount": ret.gross_amount,
            "net_amount": ret.net_amount,
            "customer_id": customer.id,
        },
    )
    return ret


def _prior_totals(session: Session, invoice_id: int) -> dict[int, list[Decimal]]:
    rows = session.execute(
        select(
            SalesReturnItem.invoice_item_id,
            func.sum(SalesReturnItem.qty_base),
            func.sum(SalesReturnItem.gross_amount),
            func.sum(SalesReturnItem.dealer_discount),
            func.sum(SalesReturnItem.company_discount),
            func.sum(SalesReturnItem.cogs),
        )
        .join(SalesReturn, SalesReturn.id == SalesReturnItem.return_id)
        .where(SalesReturn.invoice_id == invoice_id, SalesReturn.status == "posted")
        .group_by(SalesReturnItem.invoice_item_id)
    ).all()
    return {
        row[0]: [
            Decimal(row[1]),
            Decimal(row[2]),
            Decimal(row[3]),
            Decimal(row[4]),
            Decimal(row[5]),
        ]
        for row in rows
    }


def _post_return_journal(
    session: Session,
    ret: SalesReturn,
    items: dict[int, SalesInvoiceItem],
    pa: PaymentAccount | None,
    actor: CurrentUser,
) -> None:
    gross = sum((i.gross_amount for i in ret.items), ZERO)
    dealer = sum((i.dealer_discount for i in ret.items), ZERO)
    net = sum((i.net_amount for i in ret.items), ZERO)
    cogs = sum((i.cogs for i in ret.items), ZERO)
    company_by_supplier: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for it in ret.items:
        line = items[it.invoice_item_id]
        if it.company_discount > 0 and line.company_supplier_id is not None:
            company_by_supplier[line.company_supplier_id] += it.company_discount

    lines: list[JLine] = []
    if gross > 0:
        lines.append(JLine(SALES_RETURNS, debit=gross, memo=f"Return {ret.doc_no}"))
    if dealer > 0:
        lines.append(
            JLine(SALES_DISCOUNTS, credit=dealer, memo=f"Return {ret.doc_no} dealer discount")
        )
    for supplier_id, amount in sorted(company_by_supplier.items()):
        lines.append(
            JLine(
                CLAIMS_RECEIVABLE,
                credit=amount,
                party_type="supplier",
                party_id=supplier_id,
                memo=f"Return {ret.doc_no} company discount",
            )
        )
    if net > 0:
        if ret.refund_method == "credit":
            lines.append(
                JLine(
                    RECEIVABLE,
                    credit=net,
                    party_type="customer",
                    party_id=ret.customer_id,
                    memo=f"Credit for return {ret.doc_no}",
                )
            )
        elif pa is not None:
            lines.append(
                JLine(
                    pa.account_id,
                    credit=net,
                    payment_account_id=pa.id,
                    memo=f"Refund for return {ret.doc_no}",
                )
            )
    if cogs > 0:
        lines.append(JLine(INVENTORY, debit=cogs, memo=f"Stock back {ret.doc_no}"))
        lines.append(JLine(COGS, credit=cogs, memo=f"Cost reversed {ret.doc_no}"))
    if lines:
        post_journal(
            session,
            business_date=ret.business_date,
            source_doc_type=DOC_TYPE,
            source_doc_id=ret.id,
            source_doc_no=ret.doc_no,
            memo=f"Sales return {ret.doc_no} against {ret.invoice.doc_no}",
            lines=lines,
            actor_id=actor.id,
        )


def return_to_read(ret: SalesReturn) -> SalesReturnRead:
    return SalesReturnRead(
        id=ret.id,
        doc_no=ret.doc_no,
        invoice_id=ret.invoice_id,
        invoice_doc_no=ret.invoice.doc_no,
        customer_id=ret.customer_id,
        customer_name=ret.invoice.customer.name,
        business_date=ret.business_date,
        status=ret.status,
        refund_method=ret.refund_method,
        payment_account_id=ret.payment_account_id,
        payment_account_name=ret.payment_account.name if ret.payment_account else None,
        notes=ret.notes,
        gross_amount=ret.gross_amount,
        dealer_discount=ret.dealer_discount,
        company_discount=ret.company_discount,
        net_amount=ret.net_amount,
        cogs=ret.cogs,
        posted_at=ret.posted_at,
        items=[
            ReturnItemRead(
                id=i.id,
                invoice_item_id=i.invoice_item_id,
                product_id=i.product_id,
                sku=i.product.sku,
                name_en=i.product.name_en,
                name_bn=i.product.name_bn,
                base_uom_code=i.product.base_uom.code,
                batch_id=i.batch_id,
                batch_no=i.batch.batch_no if i.batch else None,
                condition=i.condition,
                qty_base=i.qty_base,
                unit_cost=i.unit_cost,
                gross_amount=i.gross_amount,
                dealer_discount=i.dealer_discount,
                company_discount=i.company_discount,
                net_amount=i.net_amount,
                cogs=i.cogs,
            )
            for i in ret.items
        ],
    )


def list_returns(
    session: Session, params: PageParams, *, status: str | None, invoice_id: int | None
) -> tuple[list[SalesReturn], int]:
    stmt = select(SalesReturn).order_by(SalesReturn.business_date.desc(), SalesReturn.id.desc())
    if status:
        stmt = stmt.where(SalesReturn.status == status)
    if invoice_id is not None:
        stmt = stmt.where(SalesReturn.invoice_id == invoice_id)
    items, total = paginate(session, stmt, params)
    return list(items), total


def return_list_item(ret: SalesReturn) -> SalesReturnListItem:
    return SalesReturnListItem(
        id=ret.id,
        doc_no=ret.doc_no,
        invoice_id=ret.invoice_id,
        invoice_doc_no=ret.invoice.doc_no,
        customer_id=ret.customer_id,
        customer_name=ret.invoice.customer.name,
        business_date=ret.business_date,
        status=ret.status,
        refund_method=ret.refund_method,
        net_amount=ret.net_amount,
    )


def delete_draft(session: Session, return_id: int, actor: CurrentUser) -> None:
    ret = get_return(session, return_id)
    if ret.status != "draft":
        raise ConflictError("Only draft returns can be deleted.", code="not_draft")
    session.delete(ret)
    session.flush()
    record_audit(
        session, actor_id=actor.id, action="delete", entity_type="sales_return", entity_id=return_id
    )


def returnable_for_invoice(session: Session, invoice: SalesInvoice) -> list[ReturnableRow]:
    """What can still be returned, one row per batch of each invoice line."""
    sold = _sold_by_batch(invoice)
    returned = _returned_by_batch(session, invoice.id)
    items = {item.id: item for item in invoice.items}
    batch_info = {
        (item.id, b.batch_id): b for item in invoice.items for b in item.batches if not b.is_free
    }
    rows: list[ReturnableRow] = []
    for key in sorted(sold, key=lambda k: (k[0], k[1] or 0)):
        item = items[key[0]]
        info = batch_info[key]
        qty_sold = sold[key]
        qty_returned = returned.get(key, ZERO)
        rows.append(
            ReturnableRow(
                invoice_item_id=item.id,
                product_id=item.product_id,
                sku=item.product.sku,
                name_en=item.product.name_en,
                name_bn=item.product.name_bn,
                base_uom_code=item.product.base_uom.code,
                batch_id=key[1],
                batch_no=info.batch.batch_no if info.batch else None,
                expiry_date=info.batch.expiry_date if info.batch else None,
                sold_qty_base=qty_sold,
                returned_qty_base=qty_returned,
                returnable_qty_base=qty_sold - qty_returned,
                unit_cost=item.unit_cost,
                base_price=item.base_price,
            )
        )
    return rows
