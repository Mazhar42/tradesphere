"""Opening positions per domain (ADR-010), through the stock and journal engines, against
Opening Balance Equity. All or nothing; reversible per load. The older one-off opening document
is untouched, and the two cannot be mixed."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.core.tenant_context import tenant_id_of
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
from app.modules.accounting.models import OpeningBalance, PaymentAccount
from app.modules.catalog.models import Product
from app.modules.imports.models import OpeningLine, OpeningLoad
from app.modules.inventory.batches import resolve_batch
from app.modules.inventory.stock import get_location_id, lock_costs, reverse_movements, stock_in
from app.modules.settings.service import ensure_period_open
from app.modules.tenancy.models import Tenant
from app.shared.audit import record_audit
from app.shared.dates import business_date as today
from app.shared.dates import now_utc
from app.shared.money import ZERO, round_money
from app.shared.sequences import next_doc_number

SOURCE = "opening_load"
DOMAINS = ("stock", "receivables", "payables", "claims", "cash")


@dataclass(frozen=True)
class Item:
    key: str
    amount: Decimal = ZERO  # money; negative payable = advance
    party_id: int | None = None
    payment_account_id: int | None = None
    product_id: int | None = None
    location: str = "SELLABLE"
    batch_no: str | None = None
    mfg_date: date | None = None
    expiry_date: date | None = None
    qty: Decimal = ZERO


def tenant_cutover(session: Session) -> date | None:
    tenant = session.get(Tenant, tenant_id_of(session), populate_existing=True)
    assert tenant is not None
    return tenant.cutover_date


def set_cutover(session: Session, cutover: date) -> None:
    """Fixed by the first opening load: live records start on this date."""
    tenant = session.get(Tenant, tenant_id_of(session), with_for_update=True)
    assert tenant is not None
    if cutover > today():
        raise BusinessRuleError("The cutover date cannot be in the future.", code="bad_cutover")
    if tenant.cutover_date not in (None, cutover) and session.scalar(
        select(OpeningLoad.id).where(OpeningLoad.status == "posted").limit(1)
    ):
        raise ConflictError(
            f"The cutover date is {tenant.cutover_date}; opening loads already use it.",
            code="cutover_fixed",
        )
    tenant.cutover_date = cutover


def refuse_if_legacy_opening(session: Session) -> None:
    if session.scalar(select(OpeningBalance.id).where(OpeningBalance.status == "posted")):
        raise ConflictError(
            "Opening balances were already posted as one document. Reverse that first, or "
            "enter the rest by normal documents.",
            code="legacy_opening_exists",
        )


def post_load(
    session: Session,
    domain: str,
    items: list[Item],
    *,
    cutover: date,
    actor: CurrentUser,
    job_id: int | None = None,
) -> OpeningLoad:
    refuse_if_legacy_opening(session)
    set_cutover(session, cutover)
    on = cutover - timedelta(days=1)
    ensure_period_open(session, on)
    if not items:
        raise BusinessRuleError("Nothing to open.", code="empty_load")
    keys = [i.key for i in items]
    taken = session.scalars(
        select(OpeningLine.item_key).where(
            OpeningLine.domain == domain, OpeningLine.active, OpeningLine.item_key.in_(keys)
        )
    ).all()
    if taken or len(set(keys)) != len(keys):
        raise ConflictError(
            f"Already opened: {', '.join(sorted(set(taken))[:5]) or 'a repeated item'}.",
            code="already_opened",
        )

    load = OpeningLoad(
        domain=domain,
        doc_no=next_doc_number(session, "OPL", on),
        business_date=on,
        total=ZERO,
        status="posted",
        job_id=job_id,
        created_by_id=actor.id,
    )
    session.add(load)
    session.flush()
    lines: list[JLine] = []
    amounts: dict[str, Decimal] = {}

    if domain == "stock":
        products = {
            p.id: p
            for p in session.scalars(
                select(Product).where(Product.id.in_({i.product_id for i in items}))
            )
        }
        lock_costs(session, products)
        total = ZERO
        for item in sorted(items, key=lambda i: (i.product_id or 0, i.key)):
            product = products[item.product_id or 0]
            batch = resolve_batch(
                session,
                product,
                batch_no=item.batch_no,
                mfg_date=item.mfg_date,
                expiry_date=item.expiry_date,
                received_on=on,
                allow_expired=True,
            )
            value = round_money(item.amount)
            stock_in(
                session,
                product_id=product.id,
                batch_id=batch.id if batch else None,
                location_id=get_location_id(session, item.location),
                qty=item.qty,
                value=value,
                business_date=on,
                movement_type="opening",
                source_doc_type=SOURCE,
                source_doc_id=load.id,
                source_doc_no=load.doc_no,
                actor_id=actor.id,
            )
            amounts[item.key] = value
            total += value
        if total > 0:
            lines.append(JLine(INVENTORY, debit=total, memo=f"Opening stock {load.doc_no}"))
    else:
        account = {
            "receivables": RECEIVABLE,
            "payables": SUPPLIER_PAYABLE,
            "claims": CLAIMS_RECEIVABLE,
        }.get(domain)
        party = {"receivables": "customer", "payables": "supplier", "claims": "supplier"}.get(
            domain
        )
        for item in items:
            amount = round_money(item.amount)
            amounts[item.key] = amount
            memo = f"Opening {domain} {load.doc_no}"
            if domain == "cash":
                pa = session.get(PaymentAccount, item.payment_account_id)
                assert pa is not None
                lines.append(
                    JLine(pa.account_id, debit=amount, payment_account_id=pa.id, memo=memo)
                )
                continue
            # What others owe us is a debit; what we owe is a credit.
            debit = amount if domain != "payables" else -amount
            lines.append(
                JLine(
                    account or "",
                    debit=debit if debit > 0 else ZERO,
                    credit=-debit if debit < 0 else ZERO,
                    party_type=party,
                    party_id=item.party_id,
                    memo=memo,
                )
            )

    net = sum((line.debit - line.credit for line in lines), ZERO)
    if net != 0:
        lines.append(
            JLine(
                OPENING_EQUITY,
                credit=net if net > 0 else ZERO,
                debit=-net if net < 0 else ZERO,
                memo=f"Opening balance equity {load.doc_no}",
            )
        )
    if len(lines) >= 2:
        post_journal(
            session,
            business_date=on,
            source_doc_type=SOURCE,
            source_doc_id=load.id,
            source_doc_no=load.doc_no,
            memo=f"Opening {domain} {load.doc_no}",
            lines=lines,
            actor_id=actor.id,
        )
    session.add_all(
        OpeningLine(
            load_id=load.id,
            domain=domain,
            item_key=i.key,
            party_id=i.party_id,
            amount=amounts[i.key],
        )
        for i in items
    )
    load.total = sum(amounts.values(), ZERO)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="post",
        entity_type="opening_load",
        entity_id=load.id,
        after={"doc_no": load.doc_no, "domain": domain, "total": load.total},
    )
    return load


def reverse_load(session: Session, load_id: int, actor: CurrentUser) -> OpeningLoad:
    """Opening stock only while its products have not moved since."""
    load = session.scalar(
        select(OpeningLoad)
        .where(OpeningLoad.id == load_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if load is None:
        raise NotFoundError("Opening load not found.")
    if load.status != "posted":
        raise ConflictError("This load is already reversed.", code="not_posted")
    on = today()
    ensure_period_open(session, load.business_date)
    ensure_period_open(session, on)
    reverse_movements(
        session, source_doc_type=SOURCE, source_doc_id=load.id, business_date=on, actor_id=actor.id
    )
    reverse_entries(
        session, source_doc_type=SOURCE, source_doc_id=load.id, business_date=on, actor_id=actor.id
    )
    session.execute(update(OpeningLine).where(OpeningLine.load_id == load.id).values(active=False))
    load.status = "reversed"
    load.reversed_at = now_utc()
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="reverse",
        entity_type="opening_load",
        entity_id=load.id,
        after={"doc_no": load.doc_no},
    )
    return load


def imported_totals(session: Session) -> dict[str, Decimal]:
    """What the active opening loads add up to, in the terms a dealer's own books use."""
    totals = {
        "stock_value": ZERO,
        "receivables": ZERO,
        "payables": ZERO,
        "advances": ZERO,
        "claims": ZERO,
        "cash": ZERO,
    }
    sign = func.sign(OpeningLine.amount)
    rows = session.execute(
        select(OpeningLine.domain, sign, func.sum(OpeningLine.amount))
        .where(OpeningLine.active)
        .group_by(OpeningLine.domain, sign)
    ).all()
    for domain, direction, amount in rows:
        if domain == "payables":
            totals["advances" if direction < 0 else "payables"] += abs(amount)
        else:
            totals["stock_value" if domain == "stock" else domain] += amount
    return totals
