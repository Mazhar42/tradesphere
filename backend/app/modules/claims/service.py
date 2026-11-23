"""Claimable items: created by sales postings, cancelled when their document is reversed."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.modules.claims.models import ClaimableItem


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
