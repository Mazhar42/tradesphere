"""One-time opening balances (go-live data load).

Posts opening stock (at the supplied cost), customer dues, supplier advances/payables,
pending company claims and cash/bank balances as a single balanced journal entry; the
difference is balanced against Opening Balance Equity (3900). Dated the day before
go-live. Only one load may be active; it can be reversed (while no later stock
movements exist) and posted again.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.accounting.journal import (
    CLAIMS_RECEIVABLE,
    INVENTORY,
    OPENING_EQUITY,
    RECEIVABLE,
    SUPPLIER_PAYABLE,
    JLine,
    post_journal,
    reverse_entries,
)
from app.modules.accounting.models import OpeningBalance
from app.modules.accounting.schemas import OpeningBalancesCreate
from app.modules.accounting.service import require_active_payment_account
from app.modules.catalog.models import Product
from app.modules.inventory.batches import resolve_batch
from app.modules.inventory.stock import (
    get_location_id,
    lock_costs,
    reverse_movements,
    stock_in,
)
from app.modules.parties.models import Customer, Supplier
from app.modules.settings.service import ensure_period_open
from app.shared.audit import record_audit
from app.shared.dates import business_date as today
from app.shared.dates import now_utc
from app.shared.money import ZERO, round_money
from app.shared.sequences import next_doc_number

DOC_TYPE = "OPENING"


def get_current(session: Session) -> OpeningBalance | None:
    return session.scalar(select(OpeningBalance).where(OpeningBalance.status == "posted"))


def list_all(session: Session) -> list[OpeningBalance]:
    return list(session.scalars(select(OpeningBalance).order_by(OpeningBalance.id.desc())).all())


def _duplicates(values: list[int], label: str, field: str) -> None:
    if len(values) != len(set(values)):
        raise BusinessRuleError(
            f"Each {label} may appear only once.", errors=[{"field": field, "message": "Duplicate"}]
        )


def post_opening(  # noqa: PLR0912, PLR0915
    session: Session, data: OpeningBalancesCreate, actor: CurrentUser
) -> OpeningBalance:
    if data.business_date >= today():
        raise BusinessRuleError(
            "Opening balances must be dated before today (use the day before go-live).",
            errors=[{"field": "business_date", "message": "Must be in the past"}],
        )
    ensure_period_open(session, data.business_date)
    if get_current(session) is not None:
        raise ConflictError(
            "Opening balances are already posted. Reverse them first to load again.",
            code="opening_already_posted",
        )
    _duplicates([d.customer_id for d in data.customer_dues], "customer", "customer_dues")
    _duplicates([d.supplier_id for d in data.supplier_balances], "supplier", "supplier_balances")
    _duplicates([d.supplier_id for d in data.claims_receivable], "supplier", "claims_receivable")
    _duplicates([d.payment_account_id for d in data.cash], "payment account", "cash")

    doc_no = next_doc_number(session, "OPN", data.business_date)
    opening = OpeningBalance(
        doc_no=doc_no,
        business_date=data.business_date,
        status="posted",
        summary={},
        notes=data.notes,
        created_by_id=actor.id,
    )
    session.add(opening)
    session.flush()

    lines: list[JLine] = []
    summary: dict[str, str | int] = {}

    # --- stock -------------------------------------------------------------------------
    stock_total = ZERO
    if data.stock:
        products = {
            p.id: p
            for p in session.scalars(
                select(Product).where(Product.id.in_({s.product_id for s in data.stock}))
            ).all()
        }
        missing = {s.product_id for s in data.stock} - set(products)
        if missing:
            raise BusinessRuleError(
                f"Unknown product id(s): {', '.join(map(str, sorted(missing)))}.",
                errors=[{"field": "stock", "message": "Unknown product"}],
            )
        lock_costs(session, products)
        for index, line in enumerate(sorted(data.stock, key=lambda s: s.product_id)):
            product = products[line.product_id]
            batch = resolve_batch(
                session,
                product,
                batch_no=line.batch_no,
                mfg_date=line.mfg_date,
                expiry_date=line.expiry_date,
                received_on=data.business_date,
                allow_expired=True,
                field_prefix=f"stock.{index}.",
            )
            value = round_money(line.qty_base * line.unit_cost)
            stock_in(
                session,
                product_id=product.id,
                batch_id=batch.id if batch else None,
                location_id=get_location_id(session, line.location),
                qty=line.qty_base,
                value=value,
                business_date=data.business_date,
                movement_type="opening",
                source_doc_type=DOC_TYPE,
                source_doc_id=opening.id,
                source_doc_no=doc_no,
                actor_id=actor.id,
            )
            stock_total += value
        if stock_total > 0:
            lines.append(JLine(INVENTORY, debit=stock_total, memo="Opening stock"))
    summary["stock_lines"] = len(data.stock)
    summary["stock_value"] = format(stock_total, "f")

    # --- customers -----------------------------------------------------------------------
    if data.customer_dues:
        ids = {d.customer_id for d in data.customer_dues}
        found = set(session.scalars(select(Customer.id).where(Customer.id.in_(ids))).all())
        if found != ids:
            raise BusinessRuleError(
                "Unknown customer.",
                errors=[{"field": "customer_dues", "message": "Unknown customer"}],
            )
        for due in data.customer_dues:
            lines.append(
                JLine(
                    RECEIVABLE,
                    debit=due.amount,
                    party_type="customer",
                    party_id=due.customer_id,
                    memo="Opening customer due",
                )
            )
    summary["customer_dues"] = format(sum((d.amount for d in data.customer_dues), ZERO), "f")

    # --- suppliers -----------------------------------------------------------------------
    supplier_ids = {d.supplier_id for d in data.supplier_balances} | {
        d.supplier_id for d in data.claims_receivable
    }
    if supplier_ids:
        found_suppliers = set(
            session.scalars(select(Supplier.id).where(Supplier.id.in_(supplier_ids))).all()
        )
        if found_suppliers != supplier_ids:
            raise BusinessRuleError("Unknown supplier.")
    advance_total = payable_total = ZERO
    for balance in data.supplier_balances:
        if balance.direction == "advance":
            lines.append(
                JLine(
                    SUPPLIER_PAYABLE,
                    debit=balance.amount,
                    party_type="supplier",
                    party_id=balance.supplier_id,
                    memo="Opening supplier advance",
                )
            )
            advance_total += balance.amount
        else:
            lines.append(
                JLine(
                    SUPPLIER_PAYABLE,
                    credit=balance.amount,
                    party_type="supplier",
                    party_id=balance.supplier_id,
                    memo="Opening supplier payable",
                )
            )
            payable_total += balance.amount
    summary["supplier_advance"] = format(advance_total, "f")
    summary["supplier_payable"] = format(payable_total, "f")

    for claim in data.claims_receivable:
        lines.append(
            JLine(
                CLAIMS_RECEIVABLE,
                debit=claim.amount,
                party_type="supplier",
                party_id=claim.supplier_id,
                memo="Opening pending company claim",
            )
        )
    summary["claims_receivable"] = format(
        sum((c.amount for c in data.claims_receivable), ZERO), "f"
    )

    # --- cash / bank -----------------------------------------------------------------------
    cash_total = ZERO
    for cash in data.cash:
        pa = require_active_payment_account(session, cash.payment_account_id)
        lines.append(
            JLine(
                pa.account_id,
                debit=cash.amount,
                payment_account_id=pa.id,
                memo=f"Opening balance {pa.name}",
            )
        )
        cash_total += cash.amount
    summary["cash"] = format(cash_total, "f")

    # --- balance against Opening Balance Equity ---------------------------------------------
    net = sum((line.debit - line.credit for line in lines), ZERO)
    if net > 0:
        lines.append(JLine(OPENING_EQUITY, credit=net, memo="Opening balance equity"))
    elif net < 0:
        lines.append(JLine(OPENING_EQUITY, debit=-net, memo="Opening balance equity"))
    if len(lines) < 2:
        raise BusinessRuleError("Nothing to post: all opening amounts are zero.")

    post_journal(
        session,
        business_date=data.business_date,
        source_doc_type=DOC_TYPE,
        source_doc_id=opening.id,
        source_doc_no=doc_no,
        memo=f"Opening balances {doc_no}",
        lines=lines,
        actor_id=actor.id,
    )
    summary["equity"] = format(net, "f")
    opening.summary = summary
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="post",
        entity_type="opening_balance",
        entity_id=opening.id,
        after={"doc_no": doc_no, "business_date": data.business_date.isoformat(), **summary},
    )
    return opening


def reverse_opening(session: Session, opening_id: int, actor: CurrentUser) -> OpeningBalance:
    opening = session.scalar(
        select(OpeningBalance)
        .where(OpeningBalance.id == opening_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if opening is None:
        raise NotFoundError("Opening balance not found.")
    if opening.status != "posted":
        raise ConflictError("Opening balances are already reversed.", code="already_reversed")
    ensure_period_open(session, opening.business_date)
    reverse_date: date = today()
    reverse_movements(
        session,
        source_doc_type=DOC_TYPE,
        source_doc_id=opening.id,
        business_date=reverse_date,
        actor_id=actor.id,
    )
    reverse_entries(
        session,
        source_doc_type=DOC_TYPE,
        source_doc_id=opening.id,
        business_date=reverse_date,
        actor_id=actor.id,
    )
    opening.status = "reversed"
    opening.reversed_at = now_utc()
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="reverse",
        entity_type="opening_balance",
        entity_id=opening.id,
        after={"doc_no": opening.doc_no, "status": "reversed"},
    )
    return opening
