"""Reversing a claim settlement (including a rejection) and a commission payment (TS-03).

A settlement is undone only newest first, because a later one was computed from what the
earlier one left outstanding.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.accounting.journal import reverse_entries
from app.modules.claims.models import (
    ClaimableItem,
    ClaimSettlement,
    CommissionPayment,
    CompanyClaim,
)
from app.modules.claims.service import _lock_claim, _outstanding, _refresh_claim
from app.modules.settings.service import ensure_period_open
from app.shared.audit import record_audit
from app.shared.dates import business_date as today
from app.shared.dates import now_utc


def reverse_settlement(
    session: Session, claim_id: int, settlement_id: int, actor: CurrentUser
) -> CompanyClaim:
    claim = _lock_claim(session, claim_id)
    settlement = session.scalar(
        select(ClaimSettlement)
        .where(ClaimSettlement.id == settlement_id, ClaimSettlement.claim_id == claim.id)
        .with_for_update(of=ClaimSettlement)
        .execution_options(populate_existing=True)
    )
    if settlement is None:
        raise NotFoundError("Settlement not found.")
    if settlement.status != "posted":
        raise ConflictError("This settlement is already reversed.", code="not_posted")
    later = session.scalar(
        select(ClaimSettlement.id).where(
            ClaimSettlement.claim_id == claim.id,
            ClaimSettlement.id > settlement.id,
            ClaimSettlement.status == "posted",
        )
    )
    if later is not None:
        raise ConflictError(
            "Reverse the later settlements of this claim first.", code="reverse_later_first"
        )
    on = today()
    ensure_period_open(session, settlement.business_date)
    ensure_period_open(session, on)
    reverse_entries(
        session,
        source_doc_type="claim_settlement",
        source_doc_id=settlement.id,
        business_date=on,
        actor_id=actor.id,
    )
    lines = {line.id: line for line in claim.lines}
    for split in settlement.lines:
        line = lines[split.claim_line_id]
        line.settled_amount -= split.settled_amount
        line.rejected_amount -= split.rejected_amount
        line.book_cleared -= split.to_book + split.write_off
        if _outstanding(line) > 0:
            line.claimable_item.status = "in_claim"
    settlement.status = "reversed"
    settlement.reversed_at = now_utc()
    _refresh_claim(session, claim)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="reverse",
        entity_type="claim_settlement",
        entity_id=settlement.id,
        after={"doc_no": settlement.doc_no, "claim": claim.doc_no},
    )
    return claim


def reverse_commission(
    session: Session, commission_id: int, actor: CurrentUser
) -> CommissionPayment:
    payment = session.scalar(
        select(CommissionPayment)
        .where(CommissionPayment.id == commission_id)
        .with_for_update(of=CommissionPayment)
        .execution_options(populate_existing=True)
    )
    if payment is None:
        raise NotFoundError("Commission payment not found.")
    if payment.status != "posted":
        raise ConflictError("This payment is already reversed.", code="not_posted")
    on = today()
    ensure_period_open(session, payment.business_date)
    ensure_period_open(session, on)
    reverse_entries(
        session,
        source_doc_type="commission_payment",
        source_doc_id=payment.id,
        business_date=on,
        actor_id=actor.id,
    )
    if payment.claimable_item_id is not None:
        item = session.scalar(
            select(ClaimableItem)
            .where(ClaimableItem.id == payment.claimable_item_id)
            .with_for_update(of=ClaimableItem)
            .execution_options(populate_existing=True)
        )
        if item is not None and item.status != "open":
            raise ConflictError(
                "The company claim for this commission has already been submitted.",
                code="claim_in_progress",
            )
        if item is not None:
            item.status = "cancelled"
    payment.status = "reversed"
    payment.reversed_at = now_utc()
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="reverse",
        entity_type="commission_payment",
        entity_id=payment.id,
        after={"doc_no": payment.doc_no},
    )
    return payment
