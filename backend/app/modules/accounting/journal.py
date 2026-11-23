"""Double-entry posting engine. Every money movement is posted through `post_journal`.

Guarantees (also enforced by the database where possible)
- every entry balances (Σ debit = Σ credit), checked here and by a deferred constraint
  trigger at commit;
- every line has exactly one positive side and at most 2 decimals;
- accounts that carry a customer/supplier sub-ledger require that party, others forbid it;
- nothing is posted on or before the period lock date;
- entries are append-only: corrections are reversals (`reverse_entries`).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError
from app.modules.accounting.models import Account, JournalEntry, JournalLine
from app.modules.settings.service import ensure_period_open
from app.shared.money import ZERO, round_money
from app.shared.sequences import next_doc_number

# Well-known account codes (see seed.py for the full chart).
CASH = "1000"
BANK = "1010"
BKASH = "1020"
NAGAD = "1030"
RECEIVABLE = "1100"
INVENTORY = "1200"
CLAIMS_RECEIVABLE = "1300"
SUPPLIER_PAYABLE = "2000"
OPENING_EQUITY = "3900"


@dataclass(frozen=True)
class JLine:
    account: str | int  # account code (str) or account id (int)
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    party_type: str | None = None
    party_id: int | None = None
    payment_account_id: int | None = None
    memo: str | None = None


def post_journal(
    session: Session,
    *,
    business_date: date,
    source_doc_type: str,
    source_doc_id: int | None,
    source_doc_no: str | None,
    memo: str,
    lines: list[JLine],
    actor_id: int | None,
    reversal_of_id: int | None = None,
) -> JournalEntry:
    ensure_period_open(session, business_date)
    if len(lines) < 2:
        raise BusinessRuleError("A journal entry needs at least two lines.")

    accounts = session.scalars(select(Account)).all()
    by_code = {a.code: a for a in accounts}
    by_id = {a.id: a for a in accounts}

    total_debit = total_credit = ZERO
    resolved: list[tuple[JLine, Account]] = []
    for line in lines:
        account = (
            by_code.get(line.account) if isinstance(line.account, str) else by_id.get(line.account)
        )
        if account is None or not account.is_active:
            raise BusinessRuleError(f"Unknown or inactive account {line.account}.")
        if not account.is_postable:
            raise BusinessRuleError(f"Account {account.code} does not accept postings.")
        if (line.debit > 0) == (line.credit > 0) or line.debit < 0 or line.credit < 0:
            raise BusinessRuleError("Each journal line needs exactly one positive amount.")
        if line.debit != round_money(line.debit) or line.credit != round_money(line.credit):
            raise BusinessRuleError("Journal amounts cannot have more than 2 decimals.")
        if account.party_type:
            if line.party_type != account.party_type or line.party_id is None:
                raise BusinessRuleError(
                    f"Account {account.code} requires a {account.party_type}.",
                    code="party_required",
                )
        elif line.party_type or line.party_id:
            raise BusinessRuleError(f"Account {account.code} does not carry a party.")
        total_debit += line.debit
        total_credit += line.credit
        resolved.append((line, account))

    if total_debit != total_credit:
        raise BusinessRuleError(
            f"Journal entry is not balanced (debit {total_debit:f}, credit {total_credit:f}).",
            code="unbalanced_entry",
        )

    entry = JournalEntry(
        entry_no=next_doc_number(session, "JV", business_date),
        business_date=business_date,
        source_doc_type=source_doc_type,
        source_doc_id=source_doc_id,
        source_doc_no=source_doc_no,
        memo=memo[:300],
        reversal_of_id=reversal_of_id,
        created_by_id=actor_id,
    )
    session.add(entry)
    session.flush()
    session.add_all(
        JournalLine(
            entry_id=entry.id,
            account_id=account.id,
            debit=line.debit,
            credit=line.credit,
            party_type=line.party_type,
            party_id=line.party_id,
            payment_account_id=line.payment_account_id,
            memo=line.memo,
        )
        for line, account in resolved
    )
    session.flush()
    session.refresh(entry)
    return entry


def reverse_entries(
    session: Session,
    *,
    source_doc_type: str,
    source_doc_id: int,
    business_date: date,
    actor_id: int | None,
) -> list[JournalEntry]:
    """Post the mirror image (debits and credits swapped) of a document's entries."""
    originals = session.scalars(
        select(JournalEntry)
        .where(
            JournalEntry.source_doc_type == source_doc_type,
            JournalEntry.source_doc_id == source_doc_id,
            JournalEntry.reversal_of_id.is_(None),
        )
        .order_by(JournalEntry.id)
    ).all()
    already = set(
        session.scalars(
            select(JournalEntry.reversal_of_id).where(
                JournalEntry.reversal_of_id.in_([e.id for e in originals])
            )
        ).all()
    )
    created: list[JournalEntry] = []
    for original in originals:
        if original.id in already:
            continue
        ensure_period_open(session, original.business_date)
        created.append(
            post_journal(
                session,
                business_date=business_date,
                source_doc_type=source_doc_type,
                source_doc_id=source_doc_id,
                source_doc_no=original.source_doc_no,
                memo=f"Reversal of {original.entry_no}: {original.memo}",
                lines=[
                    JLine(
                        account=line.account_id,
                        debit=line.credit,
                        credit=line.debit,
                        party_type=line.party_type,
                        party_id=line.party_id,
                        payment_account_id=line.payment_account_id,
                        memo=line.memo,
                    )
                    for line in original.lines
                ],
                actor_id=actor_id,
                reversal_of_id=original.id,
            )
        )
    return created


def account_balance(
    session: Session,
    code: str,
    *,
    party_type: str | None = None,
    party_id: int | None = None,
) -> Decimal:
    """Debit minus credit for an account (optionally for a single customer/supplier)."""
    stmt = (
        select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0))
        .join(Account, Account.id == JournalLine.account_id)
        .where(Account.code == code)
    )
    if party_type is not None:
        stmt = stmt.where(JournalLine.party_type == party_type, JournalLine.party_id == party_id)
    return Decimal(session.scalar(stmt) or 0)
