"""Claims with the company: claimable items, claims, settlements, rejections and commissions.

Accounting model (see docs/plan.md section 3.2, events 7, 13, 14)
- A company-funded cost is booked to the claims receivable (1300) for the supplier when it
  happens; that is the item's `book_amount`. The dealer asks for `claim_amount`.
- Settling cash or supplier credit clears the receivable up to the book amount; anything
  settled above the book is scheme income (4300).
- A rejection writes the book amount off (5300) and clears it from the receivable.
- When a line is fully resolved, any book amount left over is written off (5300).
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.accounting.journal import CLAIMS_RECEIVABLE, RECEIVABLE, JLine, post_journal
from app.modules.accounting.models import PaymentAccount
from app.modules.accounting.service import require_active_payment_account
from app.modules.claims.models import (
    PAYMENT_METHODS_WITH_ACCOUNT,
    ClaimableItem,
    ClaimSettlement,
    ClaimSettlementLine,
    CommissionPayment,
    CompanyClaim,
    CompanyClaimLine,
)
from app.modules.claims.schemas import (
    CommissionCreate,
    CompanyClaimCreate,
    CompanyClaimSubmit,
    RejectionCreate,
    SettlementCreate,
)
from app.modules.parties.models import Customer, Supplier
from app.modules.settings.service import ensure_period_open
from app.shared.audit import record_audit
from app.shared.dates import business_date as today
from app.shared.dates import now_utc
from app.shared.money import ZERO
from app.shared.pagination import PageParams, paginate_rows
from app.shared.sequences import next_doc_number

CLAIM_DOC = "CLM"
SETTLEMENT_DOC = "STL"
COMMISSION_DOC = "CMP"
AP_SUPPLIER = "2000"
SCHEME_INCOME = "4300"
REJECTED_WRITE_OFF = "5300"
DEALER_PROMOTION = "5200"
OPEN_CLAIM_STATUSES = ("draft", "submitted", "partially_settled")


# --- Claimable items (created by postings) --------------------------------------------------
def create_claimable(
    session: Session,
    *,
    supplier_id: int,
    claim_type: str,
    source_doc_type: str,
    source_doc_id: int,
    source_line_id: int | None,
    business_date: date,
    book_amount: Decimal,
    claim_amount: Decimal,
    actor_id: int | None,
) -> ClaimableItem:
    item = ClaimableItem(
        supplier_id=supplier_id,
        claim_type=claim_type,
        source_doc_type=source_doc_type,
        source_doc_id=source_doc_id,
        source_line_id=source_line_id,
        business_date=business_date,
        book_amount=book_amount,
        claim_amount=claim_amount,
        status="open",
        created_by_id=actor_id,
    )
    session.add(item)
    session.flush()
    return item


def cancel_for_document(session: Session, *, source_doc_type: str, source_doc_id: int) -> None:
    """Cancel the open claimable items of a reversed document.

    Refused once an item has been put into a company claim: that claim must be dealt with first.
    """
    items = session.scalars(
        select(ClaimableItem).where(
            ClaimableItem.source_doc_type == source_doc_type,
            ClaimableItem.source_doc_id == source_doc_id,
        )
    ).all()
    blocked = [i for i in items if i.status not in ("open", "cancelled")]
    if blocked:
        raise ConflictError(
            "This document's company claim has already been submitted; it cannot be reversed.",
            code="claim_in_progress",
        )
    for item in items:
        item.status = "cancelled"
    session.flush()


def reduce_trade_discount(
    session: Session, *, invoice_item_id: int, amount: Decimal, actor_id: int | None
) -> None:
    """A returned sale line gives back part of the company-funded discount on it.

    - Open item: the item shrinks (and is cancelled if nothing is left).
    - Item inside a submitted claim: the claim line shrinks, provided the company has not
      already settled or rejected that part.
    - Item already settled or rejected: refused. The company has paid for it, and the money
      would have to come back from the dealer, which is not automated yet.
    """
    if amount <= 0:
        return
    item = session.scalar(
        select(ClaimableItem).where(
            ClaimableItem.source_doc_type == "sales_invoice",
            ClaimableItem.source_line_id == invoice_item_id,
            ClaimableItem.claim_type == "trade_discount",
            ClaimableItem.status != "cancelled",
        )
    )
    if item is None:
        return
    if item.status == "open":
        if amount > item.claim_amount:
            raise ConflictError(
                "The returned discount is larger than the claimable.", code="discount_mismatch"
            )
        item.book_amount -= amount
        item.claim_amount -= amount
        if item.claim_amount == 0:
            item.status = "cancelled"
        session.flush()
        return
    if item.status == "in_claim":
        line = session.scalar(
            select(CompanyClaimLine)
            .join(CompanyClaim, CompanyClaim.id == CompanyClaimLine.claim_id)
            .where(
                CompanyClaimLine.claimable_item_id == item.id,
                CompanyClaim.status.in_(OPEN_CLAIM_STATUSES),
            )
        )
        if line is None:
            raise ConflictError(
                "The claim line for this discount could not be found.", code="claim_missing"
            )
        claim = _lock_claim(session, line.claim_id)
        if line.claimed_amount - amount < line.settled_amount + line.rejected_amount:
            raise ConflictError(
                "The company has already settled part of this discount; the return "
                "cannot reduce it.",
                code="discount_already_settled",
            )
        if line.book_amount - amount < line.book_cleared:
            raise ConflictError(
                "The company has already cleared part of this discount; the return "
                "cannot reduce it.",
                code="discount_already_settled",
            )
        line.claimed_amount -= amount
        line.book_amount -= amount
        item.book_amount -= amount
        item.claim_amount -= amount
        _refresh_claim(session, claim)
        session.flush()
        return
    raise ConflictError(
        "The company has already settled this discount; the return cannot reduce it.",
        code="discount_already_settled",
    )


# --- Helpers ---------------------------------------------------------------------------------
def _active_supplier(session: Session, supplier_id: int) -> Supplier:
    supplier = session.get(Supplier, supplier_id)
    if supplier is None or not supplier.is_active:
        raise BusinessRuleError(
            "Unknown or inactive company.",
            errors=[{"field": "supplier_id", "message": "Unknown company"}],
        )
    return supplier


def _lock_claim(session: Session, claim_id: int) -> CompanyClaim:
    claim = session.scalar(
        select(CompanyClaim)
        .where(CompanyClaim.id == claim_id)
        .with_for_update(of=CompanyClaim)
        .execution_options(populate_existing=True)
    )
    if claim is None:
        raise NotFoundError("Claim not found.")
    return claim


def _new_split(claim_line_id: int) -> ClaimSettlementLine:
    """A settlement split row with zeroed amounts (defaults only apply at flush time)."""
    return ClaimSettlementLine(
        claim_line_id=claim_line_id,
        settled_amount=ZERO,
        rejected_amount=ZERO,
        to_book=ZERO,
        to_income=ZERO,
        write_off=ZERO,
    )


def _outstanding(line: CompanyClaimLine) -> Decimal:
    return line.claimed_amount - line.settled_amount - line.rejected_amount


def _refresh_claim(session: Session, claim: CompanyClaim) -> None:
    """Recompute claim totals and status, and close the claimable items of resolved lines."""
    lines = claim.lines
    claim.total_claimed = sum((line.claimed_amount for line in lines), ZERO)
    claim.total_settled = sum((line.settled_amount for line in lines), ZERO)
    claim.total_rejected = sum((line.rejected_amount for line in lines), ZERO)
    for line in lines:
        if _outstanding(line) == 0:
            line.claimable_item.status = "settled" if line.settled_amount > 0 else "rejected"
    outstanding = claim.total_claimed - claim.total_settled - claim.total_rejected
    if claim.status == "draft":
        return
    if outstanding == 0:
        claim.status = "settled" if claim.total_settled > 0 else "rejected"
    elif claim.total_settled + claim.total_rejected > 0:
        claim.status = "partially_settled"
    else:
        claim.status = "submitted"
    session.flush()


def _write_off_resolved(line: CompanyClaimLine) -> Decimal:
    """Book left on a fully resolved line is written off. Returns the amount written off."""
    if _outstanding(line) == 0 and line.book_cleared < line.book_amount:
        left = line.book_amount - line.book_cleared
        line.book_cleared = line.book_amount
        return left
    return ZERO


# --- Claims ----------------------------------------------------------------------------------
def create_claim(session: Session, data: CompanyClaimCreate, actor: CurrentUser) -> CompanyClaim:
    supplier = _active_supplier(session, data.supplier_id)
    ids = list(dict.fromkeys(data.claimable_item_ids))
    items = session.scalars(
        select(ClaimableItem)
        .where(ClaimableItem.id.in_(ids))
        .order_by(ClaimableItem.id)
        .with_for_update(of=ClaimableItem)
    ).all()
    if len(items) != len(ids):
        raise NotFoundError("One or more claimable items were not found.")
    for item in items:
        if item.supplier_id != supplier.id:
            raise BusinessRuleError(
                f"Item {item.id} belongs to a different company.",
                code="wrong_supplier",
            )
        if item.status != "open":
            raise ConflictError(
                f"Item {item.id} is {item.status}; only open items can be claimed.",
                code="item_not_open",
            )
    already = session.scalars(
        select(CompanyClaimLine.claimable_item_id)
        .join(CompanyClaim, CompanyClaim.id == CompanyClaimLine.claim_id)
        .where(
            CompanyClaimLine.claimable_item_id.in_(ids),
            CompanyClaim.status.in_(OPEN_CLAIM_STATUSES),
        )
    ).all()
    if already:
        raise ConflictError(
            "Some items are already on another open claim.",
            code="already_claimed",
        )

    claim = CompanyClaim(
        supplier_id=supplier.id,
        business_date=data.business_date or today(),
        period_from=data.period_from,
        period_to=data.period_to,
        status="draft",
        notes=data.notes,
        created_by_id=actor.id,
    )
    claim.lines = [
        CompanyClaimLine(
            claimable_item_id=item.id,
            book_amount=item.book_amount,
            claimed_amount=item.claim_amount,
        )
        for item in items
    ]
    claim.total_claimed = sum((i.claim_amount for i in items), ZERO)
    session.add(claim)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="create",
        entity_type="company_claim",
        entity_id=claim.id,
        after={"supplier_id": supplier.id, "total_claimed": claim.total_claimed, "items": ids},
    )
    return claim


def submit_claim(
    session: Session, claim_id: int, data: CompanyClaimSubmit, actor: CurrentUser
) -> CompanyClaim:
    claim = _lock_claim(session, claim_id)
    if claim.status != "draft":
        raise ConflictError("Only draft claims can be submitted.", code="not_draft")
    if not claim.lines:
        raise BusinessRuleError("A claim needs at least one item.", code="empty_claim")
    for line in claim.lines:
        item = session.scalar(
            select(ClaimableItem)
            .where(ClaimableItem.id == line.claimable_item_id)
            .with_for_update()
        )
        if item is None or item.status != "open":
            raise ConflictError(
                "An item on this claim is no longer open; remove it and try again.",
                code="item_not_open",
            )
        item.status = "in_claim"
    claim.doc_no = next_doc_number(session, CLAIM_DOC, claim.business_date)
    claim.status = "submitted"
    claim.company_ref = data.company_ref
    claim.submitted_at = now_utc()
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="submit",
        entity_type="company_claim",
        entity_id=claim.id,
        after={"doc_no": claim.doc_no, "total_claimed": claim.total_claimed},
    )
    return claim


def delete_claim(session: Session, claim_id: int, actor: CurrentUser) -> None:
    claim = _lock_claim(session, claim_id)
    if claim.status != "draft":
        raise ConflictError("Only draft claims can be deleted.", code="not_draft")
    session.delete(claim)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="delete",
        entity_type="company_claim",
        entity_id=claim_id,
    )


def _payment_account_for(
    session: Session, method: str, payment_account_id: int | None
) -> PaymentAccount | None:
    if method in PAYMENT_METHODS_WITH_ACCOUNT:
        if payment_account_id is None:
            raise BusinessRuleError(
                "Choose the account the money was received into.",
                errors=[{"field": "payment_account_id", "message": "Required"}],
            )
        return require_active_payment_account(session, payment_account_id)
    return None


def _settle_and_post(  # noqa: PLR0915
    session: Session,
    claim: CompanyClaim,
    *,
    method: str,
    amount: Decimal,
    payment_account_id: int | None,
    business_date: date,
    reference: str | None,
    notes: str | None,
    actor: CurrentUser,
) -> ClaimSettlement:
    """Apply `amount` to the claim lines in order, post the journal, record the settlement."""
    pa = _payment_account_for(session, method, payment_account_id)
    outstanding_total = sum((_outstanding(line) for line in claim.lines), ZERO)
    if amount > outstanding_total:
        raise BusinessRuleError(
            f"The company owes {outstanding_total:f} on this claim; {amount:f} is more than that.",
            code="over_settled",
            errors=[{"field": "amount", "message": "More than outstanding"}],
        )

    remaining = amount
    to_book_total = ZERO
    to_income_total = ZERO
    write_off_total = ZERO
    split: dict[int, ClaimSettlementLine] = {}

    def row(line: CompanyClaimLine) -> ClaimSettlementLine:
        if line.id not in split:
            split[line.id] = _new_split(line.id)
        return split[line.id]

    for line in claim.lines:
        if remaining <= 0:
            break
        room = _outstanding(line)
        if room <= 0:
            continue
        take = min(room, remaining)
        book_left = line.book_amount - line.book_cleared
        to_book = min(take, book_left)
        to_income = take - to_book
        line.settled_amount += take
        line.book_cleared += to_book
        remaining -= take
        to_book_total += to_book
        to_income_total += to_income
        entry = row(line)
        entry.settled_amount += take
        entry.to_book += to_book
        entry.to_income += to_income

    for line in claim.lines:
        left = _write_off_resolved(line)
        if left > 0:
            write_off_total += left
            row(line).write_off += left

    settlement = ClaimSettlement(
        doc_no=next_doc_number(session, SETTLEMENT_DOC, business_date),
        claim_id=claim.id,
        business_date=business_date,
        method=method,
        amount=amount,
        payment_account_id=pa.id if pa else None,
        reference=reference,
        notes=notes,
        created_by_id=actor.id,
    )
    settlement.lines = list(split.values())
    session.add(settlement)
    session.flush()

    if method == "supplier_credit":
        debit = JLine(
            AP_SUPPLIER,
            debit=amount,
            party_type="supplier",
            party_id=claim.supplier_id,
            memo=f"Claim {claim.doc_no} settled as supplier credit",
        )
    else:
        assert pa is not None
        debit = JLine(
            pa.account_id,
            debit=amount,
            payment_account_id=pa.id,
            memo=f"Claim {claim.doc_no} settled in cash",
        )
    lines = [debit]
    if to_book_total > 0:
        lines.append(
            JLine(
                CLAIMS_RECEIVABLE,
                credit=to_book_total,
                party_type="supplier",
                party_id=claim.supplier_id,
                memo=f"Claim {claim.doc_no}",
            )
        )
    if to_income_total > 0:
        lines.append(
            JLine(SCHEME_INCOME, credit=to_income_total, memo=f"Claim {claim.doc_no} above book")
        )
    if write_off_total > 0:
        lines.append(
            JLine(
                REJECTED_WRITE_OFF,
                debit=write_off_total,
                memo=f"Claim {claim.doc_no} unclaimed book",
            )
        )
        lines.append(
            JLine(
                CLAIMS_RECEIVABLE,
                credit=write_off_total,
                party_type="supplier",
                party_id=claim.supplier_id,
                memo=f"Claim {claim.doc_no} written off",
            )
        )
    post_journal(
        session,
        business_date=business_date,
        source_doc_type="claim_settlement",
        source_doc_id=settlement.id,
        source_doc_no=settlement.doc_no,
        memo=f"Settlement {settlement.doc_no} of {claim.doc_no}",
        lines=lines,
        actor_id=actor.id,
    )
    _refresh_claim(session, claim)
    record_audit(
        session,
        actor_id=actor.id,
        action="settle",
        entity_type="company_claim",
        entity_id=claim.id,
        after={"settlement": settlement.doc_no, "method": method, "amount": amount},
    )
    return settlement


def settle_claim(
    session: Session, claim_id: int, data: SettlementCreate, actor: CurrentUser
) -> ClaimSettlement:
    claim = _lock_claim(session, claim_id)
    if claim.status not in ("submitted", "partially_settled"):
        raise ConflictError("This claim is not open for settlement.", code="claim_not_open")
    on = data.business_date or today()
    ensure_period_open(session, on)
    return _settle_and_post(
        session,
        claim,
        method=data.method,
        amount=data.amount,
        payment_account_id=data.payment_account_id,
        business_date=on,
        reference=data.reference,
        notes=data.notes,
        actor=actor,
    )


def reject_claim_lines(
    session: Session, claim_id: int, data: RejectionCreate, actor: CurrentUser
) -> ClaimSettlement:
    claim = _lock_claim(session, claim_id)
    if claim.status not in ("submitted", "partially_settled"):
        raise ConflictError("This claim is not open for rejection.", code="claim_not_open")
    on = data.business_date or today()
    ensure_period_open(session, on)
    by_id = {line.id: line for line in claim.lines}

    to_book_total = ZERO
    write_off_total = ZERO
    rejected_total = ZERO
    split: dict[int, ClaimSettlementLine] = {}
    for entry in data.lines:
        line = by_id.get(entry.claim_line_id)
        if line is None:
            raise NotFoundError("A claim line was not found on this claim.")
        if entry.amount > _outstanding(line):
            raise BusinessRuleError(
                f"Only {_outstanding(line):f} is outstanding on that line.",
                code="over_rejected",
                errors=[{"field": "lines", "message": "More than outstanding"}],
            )
        book_left = line.book_amount - line.book_cleared
        to_book = min(entry.amount, book_left)
        line.rejected_amount += entry.amount
        line.book_cleared += to_book
        to_book_total += to_book
        rejected_total += entry.amount
        row = split.setdefault(line.id, _new_split(line.id))
        row.rejected_amount += entry.amount
        row.to_book += to_book

    for line in claim.lines:
        left = _write_off_resolved(line)
        if left > 0:
            write_off_total += left
            split.setdefault(line.id, _new_split(line.id)).write_off += left

    settlement = ClaimSettlement(
        doc_no=next_doc_number(session, SETTLEMENT_DOC, on),
        claim_id=claim.id,
        business_date=on,
        method="rejection",
        amount=rejected_total,
        payment_account_id=None,
        reference=None,
        notes=data.notes,
        created_by_id=actor.id,
    )
    settlement.lines = list(split.values())
    session.add(settlement)
    session.flush()

    write_off = to_book_total + write_off_total
    if write_off > 0:
        post_journal(
            session,
            business_date=on,
            source_doc_type="claim_settlement",
            source_doc_id=settlement.id,
            source_doc_no=settlement.doc_no,
            memo=f"Rejected claim lines of {claim.doc_no}",
            lines=[
                JLine(REJECTED_WRITE_OFF, debit=write_off, memo=f"Rejected {claim.doc_no}"),
                JLine(
                    CLAIMS_RECEIVABLE,
                    credit=write_off,
                    party_type="supplier",
                    party_id=claim.supplier_id,
                    memo=f"Rejected {claim.doc_no}",
                ),
            ],
            actor_id=actor.id,
        )
    _refresh_claim(session, claim)
    record_audit(
        session,
        actor_id=actor.id,
        action="reject",
        entity_type="company_claim",
        entity_id=claim.id,
        after={"settlement": settlement.doc_no, "rejected": rejected_total},
    )
    return settlement


# --- Commissions -----------------------------------------------------------------------------
def create_commission(
    session: Session, data: CommissionCreate, actor: CurrentUser
) -> CommissionPayment:
    customer = session.get(Customer, data.customer_id)
    if customer is None or not customer.is_active:
        raise BusinessRuleError(
            "Unknown or inactive customer.",
            errors=[{"field": "customer_id", "message": "Unknown customer"}],
        )
    on = data.business_date or today()
    ensure_period_open(session, on)
    pa: PaymentAccount | None = None
    if data.method == "cash":
        pa = _payment_account_for(session, "cash", data.payment_account_id)
    supplier: Supplier | None = None
    if data.is_company_funded:
        if data.supplier_id is None:
            raise BusinessRuleError(
                "Choose the company that funds this commission.",
                errors=[{"field": "supplier_id", "message": "Required"}],
            )
        supplier = _active_supplier(session, data.supplier_id)

    payment = CommissionPayment(
        doc_no=next_doc_number(session, COMMISSION_DOC, on),
        customer_id=customer.id,
        business_date=on,
        amount=data.amount,
        method=data.method,
        payment_account_id=pa.id if pa else None,
        is_company_funded=data.is_company_funded,
        supplier_id=supplier.id if supplier else None,
        scheme_ref=data.scheme_ref,
        notes=data.notes,
        created_by_id=actor.id,
    )
    session.add(payment)
    session.flush()

    if supplier is not None:
        item = create_claimable(
            session,
            supplier_id=supplier.id,
            claim_type="cash_commission",
            source_doc_type="commission_payment",
            source_doc_id=payment.id,
            source_line_id=None,
            business_date=on,
            book_amount=data.amount,
            claim_amount=data.amount,
            actor_id=actor.id,
        )
        payment.claimable_item_id = item.id

    debit = (
        JLine(
            CLAIMS_RECEIVABLE,
            debit=data.amount,
            party_type="supplier",
            party_id=supplier.id,
            memo=f"Company-funded commission {payment.doc_no}",
        )
        if supplier is not None
        else JLine(DEALER_PROMOTION, debit=data.amount, memo=f"Commission {payment.doc_no}")
    )
    if pa is not None:
        credit = JLine(
            pa.account_id,
            credit=data.amount,
            payment_account_id=pa.id,
            memo=f"Paid to {customer.name}",
        )
    else:
        credit = JLine(
            RECEIVABLE,
            credit=data.amount,
            party_type="customer",
            party_id=customer.id,
            memo=f"Commission credited to {customer.name}",
        )
    post_journal(
        session,
        business_date=on,
        source_doc_type="commission_payment",
        source_doc_id=payment.id,
        source_doc_no=payment.doc_no,
        memo=f"Commission {payment.doc_no} to {customer.name}",
        lines=[debit, credit],
        actor_id=actor.id,
    )
    record_audit(
        session,
        actor_id=actor.id,
        action="post",
        entity_type="commission_payment",
        entity_id=payment.id,
        after={
            "doc_no": payment.doc_no,
            "amount": data.amount,
            "company_funded": data.is_company_funded,
        },
    )
    return payment


# --- Reads -----------------------------------------------------------------------------------
def list_claimables(
    session: Session,
    params: PageParams,
    *,
    supplier_id: int | None,
    status: str | None,
    claim_type: str | None,
) -> tuple[Sequence[Any], int]:
    stmt = (
        select(ClaimableItem, Supplier.name)
        .join(Supplier, Supplier.id == ClaimableItem.supplier_id)
        .order_by(ClaimableItem.business_date.desc(), ClaimableItem.id.desc())
    )
    if supplier_id is not None:
        stmt = stmt.where(ClaimableItem.supplier_id == supplier_id)
    if status:
        stmt = stmt.where(ClaimableItem.status == status)
    if claim_type:
        stmt = stmt.where(ClaimableItem.claim_type == claim_type)
    return paginate_rows(session, stmt, params)


def get_claim(session: Session, claim_id: int) -> CompanyClaim:
    claim = session.get(CompanyClaim, claim_id)
    if claim is None:
        raise NotFoundError("Claim not found.")
    return claim


def list_claims(
    session: Session, params: PageParams, *, status: str | None, supplier_id: int | None
) -> tuple[Sequence[Any], int]:
    stmt = (
        select(CompanyClaim, Supplier.name)
        .join(Supplier, Supplier.id == CompanyClaim.supplier_id)
        .order_by(CompanyClaim.business_date.desc(), CompanyClaim.id.desc())
    )
    if status:
        stmt = stmt.where(CompanyClaim.status == status)
    if supplier_id is not None:
        stmt = stmt.where(CompanyClaim.supplier_id == supplier_id)
    return paginate_rows(session, stmt, params)


def claim_outstanding(claim: CompanyClaim) -> Decimal:
    return claim.total_claimed - claim.total_settled - claim.total_rejected
