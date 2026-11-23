"""Sales invoices: drafting, posting (stock, journal, claims, cash) and reversal.

Posting is one database transaction. It locks the customer, then the invoice, then the stock
rows it needs, checks credit, allocates stock first-expiry-first-out, writes the journal,
creates the claimable items for company-funded discounts and free goods, updates the daily
projection, and records any cash taken at the counter as a receipt. Any failure rolls it all back.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, ForbiddenError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.accounting.journal import (
    CLAIMS_RECEIVABLE,
    INVENTORY,
    JLine,
    post_journal,
    reverse_entries,
)
from app.modules.accounting.models import PaymentAccount
from app.modules.accounting.service import require_active_payment_account
from app.modules.catalog.models import Product
from app.modules.claims.service import cancel_for_document, create_claimable
from app.modules.inventory.allocation import allocate_fefo
from app.modules.inventory.stock import get_location_id, lock_costs, reverse_movements, stock_out
from app.modules.parties.models import Customer, Supplier
from app.modules.sales.ledger import (
    customer_balance,
    lock_customer,
    posted_receipt_allocations,
    received_for_invoices,
)
from app.modules.sales.models import (
    CustomerReceipt,
    ReceiptAllocation,
    SaleItemBatch,
    SalesInvoice,
    SalesInvoiceItem,
)
from app.modules.sales.projections import apply_sales_summary
from app.modules.sales.receipts import (
    create_cash_receipt_for_sale,
    receipt_list_item,
    reverse_receipt_record,
)
from app.modules.sales.schemas import (
    BatchAllocRead,
    InvoiceItemRead,
    InvoiceLineIn,
    InvoiceListItem,
    InvoiceRead,
    InvoiceWrite,
)
from app.modules.settings.schemas import Preferences
from app.modules.settings.service import ensure_period_open, get_setting
from app.shared.audit import record_audit
from app.shared.dates import business_date as today
from app.shared.dates import now_utc
from app.shared.money import ZERO, round_money
from app.shared.pagination import PageParams, paginate_rows
from app.shared.sequences import next_doc_number

DOC_TYPE = "sales_invoice"  # journal and stock source type
DOC_PREFIX = "INV"  # document number prefix: INV-2627-000001
COST_Q = Decimal("0.0001")
QTY_Q = Decimal("0.001")

RECEIVABLE = "1100"
SALES_REVENUE = "4000"
SALES_DISCOUNTS = "4200"
COGS = "5000"
DEALER_PROMOTION = "5200"


# --- Lookups and locks ------------------------------------------------------------------------
def get_invoice(session: Session, invoice_id: int) -> SalesInvoice:
    invoice = session.get(SalesInvoice, invoice_id)
    if invoice is None:
        raise NotFoundError("Invoice not found.")
    return invoice


def _lock_customer_of(session: Session, invoice_id: int) -> Customer:
    customer_id = session.scalar(
        select(SalesInvoice.customer_id).where(SalesInvoice.id == invoice_id)
    )
    if customer_id is None:
        raise NotFoundError("Invoice not found.")
    return lock_customer(session, customer_id)


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


# --- Building the document ---------------------------------------------------------------------
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


def _to_base_qty(value: Decimal, index: int, product: Product, field: str) -> Decimal:
    if value != value.quantize(QTY_Q):
        raise BusinessRuleError(
            f"{product.sku}: quantity has too many decimals after unit conversion.",
            errors=[{"field": f"items.{index}.{field}", "message": "Too many decimals"}],
        )
    return value


def _check_supplier(session: Session, supplier_id: int, index: int) -> None:
    supplier = session.get(Supplier, supplier_id)
    if supplier is None or not supplier.is_active:
        raise BusinessRuleError(
            "Unknown or inactive company.",
            errors=[{"field": f"items.{index}.company_supplier_id", "message": "Unknown company"}],
        )


def _build_items(
    session: Session,
    lines: Sequence[InvoiceLineIn],
    actor: CurrentUser,
    *,
    allow_below_dp: bool,
) -> list[SalesInvoiceItem]:
    products = {
        p.id: p
        for p in session.scalars(
            select(Product).where(Product.id.in_({line.product_id for line in lines}))
        ).all()
    }
    items: list[SalesInvoiceItem] = []
    for index, line in enumerate(lines):
        product = products.get(line.product_id)
        if product is None or not product.is_active:
            raise BusinessRuleError(
                "Unknown or inactive product.",
                errors=[{"field": f"items.{index}.product_id", "message": "Unknown product"}],
            )
        uom_id, factor = _entry_unit(product, line.uom_id, index)
        qty_base = _to_base_qty(line.qty * factor, index, product, "qty")
        free_base = _to_base_qty(line.free_qty * factor, index, product, "free_qty")

        # Price per entry unit. Default: the trade price. Any other price is an override.
        if line.unit_price is None:
            unit_price = (product.tp * factor).quantize(COST_Q)
        else:
            unit_price = line.unit_price.quantize(COST_Q)
        if unit_price <= 0:
            raise BusinessRuleError(
                f"{product.sku}: no selling price. Set a trade price or enter one.",
                errors=[{"field": f"items.{index}.unit_price", "message": "Price required"}],
            )
        base_price = (unit_price / factor).quantize(COST_Q)
        if base_price != product.tp and not actor.has("sales:price_override"):
            raise ForbiddenError(
                f"{product.sku}: changing the trade price needs the sales:price_override "
                "permission.",
                code="price_override_denied",
            )
        if product.dp > 0 and base_price < product.dp and not allow_below_dp:
            raise BusinessRuleError(
                f"{product.sku}: {base_price:f} per unit is below the dealer price "
                f"{product.dp:f}. Selling below DP is not allowed.",
                code="below_dp",
                errors=[{"field": f"items.{index}.unit_price", "message": "Below dealer price"}],
            )
        if product.mrp > 0 and base_price > product.mrp:
            raise BusinessRuleError(
                f"{product.sku}: {base_price:f} per unit is above the MRP {product.mrp:f}.",
                code="above_mrp",
                errors=[{"field": f"items.{index}.unit_price", "message": "Above MRP"}],
            )

        gross = round_money(line.qty * unit_price)
        dealer = line.dealer_discount
        company = line.company_discount
        if dealer + company > gross:
            raise BusinessRuleError(
                "Discounts cannot exceed the line value.",
                errors=[{"field": f"items.{index}.dealer_discount", "message": "Too large"}],
            )
        needs_company = company > 0 or (line.free_is_company_scheme and free_base > 0)
        supplier_id: int | None = None
        if needs_company:
            if line.company_supplier_id is None:
                raise BusinessRuleError(
                    "Company-funded discounts and free goods need the company that funds them.",
                    errors=[
                        {
                            "field": f"items.{index}.company_supplier_id",
                            "message": "Choose the company",
                        }
                    ],
                )
            _check_supplier(session, line.company_supplier_id, index)
            supplier_id = line.company_supplier_id

        items.append(
            SalesInvoiceItem(
                product_id=product.id,
                uom_id=uom_id,
                factor=factor,
                qty_entry=line.qty,
                free_qty_entry=line.free_qty,
                qty_base=qty_base,
                free_qty_base=free_base,
                free_is_company_scheme=line.free_is_company_scheme,
                unit_price=unit_price,
                base_price=base_price,
                gross_amount=gross,
                dealer_discount=dealer,
                company_discount=company,
                company_supplier_id=supplier_id,
                line_net=gross - dealer - company,
                unit_cost=Decimal(0),
                cogs=Decimal(0),
                free_cost=Decimal(0),
            )
        )
    return items


def _apply_write(
    session: Session, invoice: SalesInvoice, data: InvoiceWrite, actor: CurrentUser
) -> None:
    customer = session.get(Customer, data.customer_id)
    if customer is None:
        raise BusinessRuleError(
            "Unknown customer.",
            errors=[{"field": "customer_id", "message": "Unknown customer"}],
        )
    if not customer.is_active:
        raise BusinessRuleError(
            "This customer is inactive.",
            errors=[{"field": "customer_id", "message": "Inactive customer"}],
        )
    prefs = get_setting(session, "preferences", Preferences)
    items = _build_items(session, data.items, actor, allow_below_dp=prefs.allow_sell_below_dp)

    gross = sum((i.gross_amount for i in items), ZERO)
    dealer = sum((i.dealer_discount for i in items), ZERO)
    company = sum((i.company_discount for i in items), ZERO)
    net = sum((i.line_net for i in items), ZERO)
    if data.paid_amount > net:
        raise BusinessRuleError(
            f"Paid amount ({data.paid_amount:f}) is more than the invoice total ({net:f}).",
            errors=[{"field": "paid_amount", "message": "More than the total"}],
        )
    payment_account: PaymentAccount | None = None
    if data.paid_amount > 0:
        if data.payment_account_id is None:
            raise BusinessRuleError(
                "Choose the account the cash was received into.",
                errors=[{"field": "payment_account_id", "message": "Required"}],
            )
        payment_account = require_active_payment_account(session, data.payment_account_id)

    invoice.customer_id = customer.id
    invoice.business_date = data.business_date or today()
    invoice.notes = data.notes
    invoice.gross_amount = gross
    invoice.dealer_discount = dealer
    invoice.company_discount = company
    invoice.net_amount = net
    invoice.paid_amount = data.paid_amount
    invoice.payment_account_id = payment_account.id if payment_account else None
    invoice.items = items


def create_draft(session: Session, data: InvoiceWrite, actor: CurrentUser) -> SalesInvoice:
    invoice = SalesInvoice(status="draft", created_by_id=actor.id)
    _apply_write(session, invoice, data, actor)
    session.add(invoice)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="create",
        entity_type="sales_invoice",
        entity_id=invoice.id,
        after={"customer_id": invoice.customer_id, "net_amount": invoice.net_amount},
    )
    return invoice


def update_draft(
    session: Session, invoice_id: int, data: InvoiceWrite, actor: CurrentUser
) -> SalesInvoice:
    invoice = _lock_invoice(session, invoice_id)
    if invoice.status != "draft":
        raise ConflictError("Only draft invoices can be edited.", code="not_draft")
    _apply_write(session, invoice, data, actor)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="update",
        entity_type="sales_invoice",
        entity_id=invoice.id,
        after={"customer_id": invoice.customer_id, "net_amount": invoice.net_amount},
    )
    return invoice


def delete_draft(session: Session, invoice_id: int, actor: CurrentUser) -> None:
    invoice = _lock_invoice(session, invoice_id)
    if invoice.status != "draft":
        raise ConflictError("Only draft invoices can be deleted.", code="not_draft")
    record_audit(
        session,
        actor_id=actor.id,
        action="delete",
        entity_type="sales_invoice",
        entity_id=invoice.id,
        before={"net_amount": invoice.net_amount},
    )
    session.delete(invoice)
    session.flush()


# --- Posting ------------------------------------------------------------------------------------
def _issue_stock(session: Session, invoice: SalesInvoice, actor: CurrentUser) -> None:
    """Take the goods out of SELLABLE, FEFO. Sold units and free units both leave stock."""
    lock_costs(session, {item.product_id for item in invoice.items})
    sellable = get_location_id(session, "SELLABLE")
    for item in invoice.items:
        allocations = allocate_fefo(
            session,
            product=item.product,
            location_id=sellable,
            qty=item.qty_base + item.free_qty_base,
        )
        sold_left = item.qty_base
        cogs = ZERO
        free_cost = ZERO
        for batch_id, take in allocations:
            sold = min(take, sold_left)
            free = take - sold
            sold_left -= sold
            if sold > 0:
                move = stock_out(
                    session,
                    product_id=item.product_id,
                    location_id=sellable,
                    qty=sold,
                    batch_id=batch_id,
                    business_date=invoice.business_date,
                    movement_type="sale",
                    source_doc_type=DOC_TYPE,
                    source_doc_id=invoice.id,
                    source_doc_no=invoice.doc_no,
                    source_line_id=item.id,
                    actor_id=actor.id,
                )
                cogs += -move.value
                item.batches.append(SaleItemBatch(batch_id=batch_id, qty_base=sold, is_free=False))
            if free > 0:
                move = stock_out(
                    session,
                    product_id=item.product_id,
                    location_id=sellable,
                    qty=free,
                    batch_id=batch_id,
                    business_date=invoice.business_date,
                    movement_type="sale_free",
                    source_doc_type=DOC_TYPE,
                    source_doc_id=invoice.id,
                    source_doc_no=invoice.doc_no,
                    source_line_id=item.id,
                    actor_id=actor.id,
                )
                free_cost += -move.value
                item.batches.append(SaleItemBatch(batch_id=batch_id, qty_base=free, is_free=True))
        item.cogs = cogs
        item.free_cost = free_cost
        item.unit_cost = (cogs / item.qty_base).quantize(COST_Q)


@dataclass(frozen=True)
class _Funding:
    """Who pays for what on one invoice, grouped the way the journal needs it."""

    trade_discount_by_company: dict[int, Decimal]
    free_goods_by_company: dict[int, Decimal]
    free_goods_dealer: Decimal
    cogs: Decimal


def _funding(invoice: SalesInvoice) -> _Funding:
    trade_discount: dict[int, Decimal] = {}
    free_company: dict[int, Decimal] = {}
    free_dealer = ZERO
    cogs = ZERO
    for item in invoice.items:
        cogs += item.cogs
        company = item.company_supplier_id
        if item.company_discount > 0 and company is not None:
            trade_discount[company] = trade_discount.get(company, ZERO) + item.company_discount
        if item.free_cost > 0:
            if item.free_is_company_scheme and company is not None:
                free_company[company] = free_company.get(company, ZERO) + item.free_cost
            else:
                free_dealer += item.free_cost
    return _Funding(trade_discount, free_company, free_dealer, cogs)


def _post_sale_journal(session: Session, invoice: SalesInvoice, actor: CurrentUser) -> None:
    funding = _funding(invoice)
    trade_discount_by_company = funding.trade_discount_by_company
    free_by_company = funding.free_goods_by_company
    free_dealer = funding.free_goods_dealer
    cogs_total = funding.cogs

    lines: list[JLine] = []
    if invoice.net_amount > 0:
        lines.append(
            JLine(
                RECEIVABLE,
                debit=invoice.net_amount,
                party_type="customer",
                party_id=invoice.customer_id,
                memo=f"Sale {invoice.doc_no}",
            )
        )
    if invoice.dealer_discount > 0:
        lines.append(JLine(SALES_DISCOUNTS, debit=invoice.dealer_discount, memo="Dealer discount"))
    for supplier_id, amount in sorted(trade_discount_by_company.items()):
        lines.append(
            JLine(
                CLAIMS_RECEIVABLE,
                debit=amount,
                party_type="supplier",
                party_id=supplier_id,
                memo=f"Company-funded discount {invoice.doc_no}",
            )
        )
    if invoice.gross_amount > 0:
        lines.append(
            JLine(SALES_REVENUE, credit=invoice.gross_amount, memo=f"Sale {invoice.doc_no}")
        )
    for supplier_id, amount in sorted(free_by_company.items()):
        lines.append(
            JLine(
                CLAIMS_RECEIVABLE,
                debit=amount,
                party_type="supplier",
                party_id=supplier_id,
                memo=f"Free goods {invoice.doc_no}",
            )
        )
    if free_dealer > 0:
        lines.append(JLine(DEALER_PROMOTION, debit=free_dealer, memo="Dealer-funded free goods"))
    if cogs_total > 0:
        lines.append(JLine(COGS, debit=cogs_total, memo=f"Cost of sale {invoice.doc_no}"))
    stock_out_value = cogs_total + sum(free_by_company.values(), ZERO) + free_dealer
    if stock_out_value > 0:
        lines.append(
            JLine(INVENTORY, credit=stock_out_value, memo=f"Stock issued {invoice.doc_no}")
        )
    if lines:
        post_journal(
            session,
            business_date=invoice.business_date,
            source_doc_type=DOC_TYPE,
            source_doc_id=invoice.id,
            source_doc_no=invoice.doc_no,
            memo=f"Sale {invoice.doc_no}",
            lines=lines,
            actor_id=actor.id,
        )


def _create_claims(session: Session, invoice: SalesInvoice, actor: CurrentUser) -> None:
    for item in invoice.items:
        if item.company_discount > 0 and item.company_supplier_id is not None:
            create_claimable(
                session,
                supplier_id=item.company_supplier_id,
                claim_type="trade_discount",
                source_doc_type=DOC_TYPE,
                source_doc_id=invoice.id,
                source_line_id=item.id,
                business_date=invoice.business_date,
                book_amount=item.company_discount,
                claim_amount=item.company_discount,
                actor_id=actor.id,
            )
        if item.free_is_company_scheme and item.free_qty_base > 0 and item.company_supplier_id:
            create_claimable(
                session,
                supplier_id=item.company_supplier_id,
                claim_type="free_goods",
                source_doc_type=DOC_TYPE,
                source_doc_id=invoice.id,
                source_line_id=item.id,
                business_date=invoice.business_date,
                book_amount=item.free_cost,
                claim_amount=round_money(item.free_qty_base * item.product.dp),
                actor_id=actor.id,
            )


def post_invoice(session: Session, invoice_id: int, actor: CurrentUser) -> SalesInvoice:
    customer = _lock_customer_of(session, invoice_id)
    invoice = _lock_invoice(session, invoice_id)
    if invoice.status != "draft":
        raise ConflictError("This invoice is already posted.", code="not_draft")
    ensure_period_open(session, invoice.business_date)

    due = invoice.net_amount - invoice.paid_amount
    if due > 0:
        room = customer.credit_limit - customer_balance(session, customer.id)
        if due > room:
            message = (
                f"{customer.name} has no credit limit, so this sale must be paid in full."
                if customer.credit_limit == 0
                else f"{customer.name} can take {max(room, ZERO):f} more on credit; "
                f"this sale leaves {due:f} unpaid."
            )
            raise BusinessRuleError(
                message,
                code="credit_limit_exceeded",
                errors=[{"field": "paid_amount", "message": "Exceeds credit limit"}],
            )

    invoice.doc_no = next_doc_number(session, DOC_PREFIX, invoice.business_date)
    session.flush()  # item ids are needed as source lines
    _issue_stock(session, invoice, actor)
    _post_sale_journal(session, invoice, actor)
    _create_claims(session, invoice, actor)
    apply_sales_summary(session, invoice, +1)
    if invoice.paid_amount > 0:
        create_cash_receipt_for_sale(session, invoice, actor_id=actor.id)
    invoice.status = "posted"
    invoice.posted_at = now_utc()
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="post",
        entity_type="sales_invoice",
        entity_id=invoice.id,
        after={
            "doc_no": invoice.doc_no,
            "net_amount": invoice.net_amount,
            "paid_amount": invoice.paid_amount,
        },
    )
    return invoice


def reverse_invoice(session: Session, invoice_id: int, actor: CurrentUser) -> SalesInvoice:
    _lock_customer_of(session, invoice_id)
    invoice = _lock_invoice(session, invoice_id)
    if invoice.status != "posted":
        raise ConflictError("Only posted invoices can be reversed.", code="not_posted")
    on = today()
    ensure_period_open(session, invoice.business_date)
    ensure_period_open(session, on)

    # Money from receipts that were recorded separately blocks the reversal: those must be
    # reversed (or re-allocated) first, so no customer balance is silently moved.
    blocking = session.scalar(
        select(func.count())
        .select_from(ReceiptAllocation)
        .join(CustomerReceipt, CustomerReceipt.id == ReceiptAllocation.receipt_id)
        .where(
            ReceiptAllocation.invoice_id == invoice.id,
            CustomerReceipt.status == "posted",
            or_(
                CustomerReceipt.sales_invoice_id.is_(None),
                CustomerReceipt.sales_invoice_id != invoice.id,
            ),
        )
    )
    if blocking:
        raise ConflictError(
            f"{invoice.doc_no} has receipts allocated to it. Reverse those receipts first.",
            code="receipts_allocated",
        )
    own_cash = session.scalar(
        select(CustomerReceipt).where(
            CustomerReceipt.sales_invoice_id == invoice.id,
            CustomerReceipt.status == "posted",
        )
    )
    if own_cash is not None:
        reverse_receipt_record(session, own_cash, on=on, actor_id=actor.id)

    reverse_movements(
        session,
        source_doc_type=DOC_TYPE,
        source_doc_id=invoice.id,
        business_date=on,
        actor_id=actor.id,
    )
    reverse_entries(
        session,
        source_doc_type=DOC_TYPE,
        source_doc_id=invoice.id,
        business_date=on,
        actor_id=actor.id,
    )
    cancel_for_document(session, source_doc_type=DOC_TYPE, source_doc_id=invoice.id)
    apply_sales_summary(session, invoice, -1)
    invoice.status = "reversed"
    invoice.reversed_at = now_utc()
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="reverse",
        entity_type="sales_invoice",
        entity_id=invoice.id,
        after={"doc_no": invoice.doc_no, "status": invoice.status},
    )
    return invoice


# --- Reads --------------------------------------------------------------------------------------
def payment_status(status: str, net: Decimal, received: Decimal) -> str:
    if status == "draft":
        return "draft"
    if status == "reversed":
        return "reversed"
    if net - received <= 0:
        return "paid"
    return "partial" if received > 0 else "unpaid"


def _item_read(item: SalesInvoiceItem) -> InvoiceItemRead:
    return InvoiceItemRead(
        id=item.id,
        product_id=item.product_id,
        sku=item.product.sku,
        name_en=item.product.name_en,
        name_bn=item.product.name_bn,
        base_uom_code=item.product.base_uom.code,
        uom_id=item.uom_id,
        uom_code=item.uom.code,
        factor=item.factor,
        qty_entry=item.qty_entry,
        free_qty_entry=item.free_qty_entry,
        qty_base=item.qty_base,
        free_qty_base=item.free_qty_base,
        free_is_company_scheme=item.free_is_company_scheme,
        unit_price=item.unit_price,
        base_price=item.base_price,
        gross_amount=item.gross_amount,
        dealer_discount=item.dealer_discount,
        company_discount=item.company_discount,
        company_supplier_id=item.company_supplier_id,
        company_supplier_name=item.company_supplier.name if item.company_supplier else None,
        line_net=item.line_net,
        unit_cost=item.unit_cost,
        cogs=item.cogs,
        free_cost=item.free_cost,
        batches=[
            BatchAllocRead(
                batch_id=b.batch_id,
                batch_no=b.batch.batch_no if b.batch else None,
                expiry_date=b.batch.expiry_date if b.batch else None,
                qty_base=b.qty_base,
                is_free=b.is_free,
            )
            for b in item.batches
        ],
    )


def invoice_to_read(session: Session, invoice: SalesInvoice) -> InvoiceRead:
    received = (
        received_for_invoices(session, [invoice.id]).get(invoice.id, ZERO)
        if invoice.status == "posted"
        else ZERO
    )
    due = ZERO if invoice.status == "reversed" else invoice.net_amount - received
    receipts = session.scalars(
        select(CustomerReceipt)
        .join(ReceiptAllocation, ReceiptAllocation.receipt_id == CustomerReceipt.id)
        .where(ReceiptAllocation.invoice_id == invoice.id)
        .order_by(CustomerReceipt.id)
    ).all()
    return InvoiceRead(
        id=invoice.id,
        doc_no=invoice.doc_no,
        customer_id=invoice.customer_id,
        customer_code=invoice.customer.code,
        customer_name=invoice.customer.name,
        customer_shop_name=invoice.customer.shop_name,
        business_date=invoice.business_date,
        status=invoice.status,
        notes=invoice.notes,
        gross_amount=invoice.gross_amount,
        dealer_discount=invoice.dealer_discount,
        company_discount=invoice.company_discount,
        net_amount=invoice.net_amount,
        paid_amount=invoice.paid_amount,
        payment_account_id=invoice.payment_account_id,
        payment_account_name=invoice.payment_account.name if invoice.payment_account else None,
        received_amount=received,
        due_amount=due,
        payment_status=payment_status(invoice.status, invoice.net_amount, received),
        posted_at=invoice.posted_at,
        reversed_at=invoice.reversed_at,
        items=[_item_read(i) for i in invoice.items],
        receipts=[receipt_list_item(r) for r in receipts],
    )


def list_invoices(
    session: Session,
    params: PageParams,
    *,
    q: str | None,
    status: str | None,
    customer_id: int | None,
    date_from: date | None,
    date_to: date | None,
) -> tuple[list[InvoiceListItem], int]:
    received = posted_receipt_allocations().subquery()
    stmt = (
        select(SalesInvoice, func.coalesce(received.c.received, ZERO))
        .outerjoin(received, received.c.invoice_id == SalesInvoice.id)
        .order_by(SalesInvoice.business_date.desc(), SalesInvoice.id.desc())
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.join(Customer, Customer.id == SalesInvoice.customer_id).where(
            or_(
                SalesInvoice.doc_no.ilike(like),
                Customer.name.ilike(like),
                Customer.code.ilike(like),
            )
        )
    if status:
        stmt = stmt.where(SalesInvoice.status == status)
    if customer_id is not None:
        stmt = stmt.where(SalesInvoice.customer_id == customer_id)
    if date_from is not None:
        stmt = stmt.where(SalesInvoice.business_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(SalesInvoice.business_date <= date_to)

    rows, total = paginate_rows(session, stmt, params)
    items = []
    for invoice, received_amount in rows:
        rec = Decimal(received_amount)
        due = ZERO if invoice.status == "reversed" else invoice.net_amount - rec
        items.append(
            InvoiceListItem(
                id=invoice.id,
                doc_no=invoice.doc_no,
                customer_id=invoice.customer_id,
                customer_code=invoice.customer.code,
                customer_name=invoice.customer.name,
                business_date=invoice.business_date,
                status=invoice.status,
                net_amount=invoice.net_amount,
                received_amount=rec if invoice.status == "posted" else ZERO,
                due_amount=due if invoice.status == "posted" else ZERO,
                payment_status=payment_status(invoice.status, invoice.net_amount, rec),
            )
        )
    return items, total
