"""Reversing expenses, fund transfers and owner transactions (TS-03, ADR-012).

A reversal posts the mirror image of the document's journal entry, dated today, and marks the
document reversed. Nothing is edited or deleted. See docs/.../reversals-impact.md.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.accounting.journal import reverse_entries
from app.modules.finance.models import Expense, FundTransfer, OwnerTransaction
from app.modules.settings.service import ensure_period_open
from app.shared.audit import record_audit
from app.shared.dates import business_date as today
from app.shared.dates import now_utc

Document = Expense | FundTransfer | OwnerTransaction

SOURCE_TYPES: dict[type[Any], str] = {
    Expense: "expense",
    FundTransfer: "fund_transfer",
    OwnerTransaction: "owner_transaction",
}


def reverse_money_document[T: Document](
    session: Session, model: type[T], document_id: int, actor: CurrentUser
) -> T:
    document = session.scalar(
        select(model)
        .where(model.id == document_id)
        .with_for_update(of=model)
        .execution_options(populate_existing=True)
    )
    if document is None:
        raise NotFoundError("Document not found.")
    if document.status != "posted":
        raise ConflictError("This document is already reversed.", code="not_posted")
    on = today()
    ensure_period_open(session, document.business_date)
    ensure_period_open(session, on)
    reverse_entries(
        session,
        source_doc_type=SOURCE_TYPES[model],
        source_doc_id=document.id,
        business_date=on,
        actor_id=actor.id,
    )
    document.status = "reversed"
    document.reversed_at = now_utc()
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="reverse",
        entity_type=SOURCE_TYPES[model],
        entity_id=document.id,
        after={"doc_no": document.doc_no},
    )
    return document
