from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.accounting.journal import BANK, BKASH, CASH, NAGAD
from app.modules.accounting.models import (
    Account,
    JournalEntry,
    JournalLine,
    PaymentAccount,
)
from app.modules.accounting.schemas import (
    JournalEntryRead,
    JournalLineRead,
    LedgerEntryRead,
    PaymentAccountCreate,
    PaymentAccountRead,
    PaymentAccountUpdate,
    TrialBalanceRead,
    TrialBalanceRow,
)
from app.shared.audit import record_audit, snapshot
from app.shared.money import ZERO
from app.shared.pagination import PageParams, paginate, paginate_rows

DEFAULT_GL = {"cash": CASH, "bank": BANK, "mfs": BKASH}
KIND_ALLOWED_GL = {"cash": {CASH}, "bank": {BANK}, "mfs": {BKASH, NAGAD}}


# --- Payment accounts -----------------------------------------------------------------
def _balances(session: Session) -> dict[int, Decimal]:
    rows = session.execute(
        select(JournalLine.payment_account_id, func.sum(JournalLine.debit - JournalLine.credit))
        .where(JournalLine.payment_account_id.is_not(None))
        .group_by(JournalLine.payment_account_id)
    ).all()
    return {pid: Decimal(total) for pid, total in rows if pid is not None}


def payment_account_to_read(pa: PaymentAccount, balance: Decimal) -> PaymentAccountRead:
    return PaymentAccountRead(
        id=pa.id,
        name=pa.name,
        kind=pa.kind,
        gl_account_code=pa.account.code,
        bank_name=pa.bank_name,
        account_no=pa.account_no,
        is_system=pa.is_system,
        is_active=pa.is_active,
        balance=balance,
    )


def read_payment_account(session: Session, pa: PaymentAccount) -> PaymentAccountRead:
    balance = session.scalar(
        select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), 0)).where(
            JournalLine.payment_account_id == pa.id
        )
    )
    return payment_account_to_read(pa, Decimal(balance or 0))


def list_payment_accounts(
    session: Session, *, active_only: bool = False
) -> list[PaymentAccountRead]:
    stmt = select(PaymentAccount).order_by(PaymentAccount.kind, PaymentAccount.name)
    if active_only:
        stmt = stmt.where(PaymentAccount.is_active.is_(True))
    balances = _balances(session)
    return [
        payment_account_to_read(pa, balances.get(pa.id, ZERO)) for pa in session.scalars(stmt).all()
    ]


def get_payment_account(session: Session, payment_account_id: int) -> PaymentAccount:
    pa = session.get(PaymentAccount, payment_account_id)
    if pa is None:
        raise NotFoundError("Payment account not found.")
    return pa


def require_active_payment_account(session: Session, payment_account_id: int) -> PaymentAccount:
    pa = get_payment_account(session, payment_account_id)
    if not pa.is_active:
        raise BusinessRuleError(
            "This payment account is inactive.",
            errors=[{"field": "payment_account_id", "message": "Inactive account"}],
        )
    return pa


def create_payment_account(
    session: Session, data: PaymentAccountCreate, actor: CurrentUser
) -> PaymentAccount:
    code = data.gl_account_code or DEFAULT_GL[data.kind]
    if code not in KIND_ALLOWED_GL[data.kind]:
        raise BusinessRuleError(
            "Account type and ledger account do not match.",
            errors=[{"field": "gl_account_code", "message": "Not valid for this type"}],
        )
    if session.scalar(
        select(PaymentAccount.id).where(func.lower(PaymentAccount.name) == data.name.lower())
    ):
        raise ConflictError(
            "A payment account with this name already exists.",
            code="duplicate",
            errors=[{"field": "name", "message": "Already exists"}],
        )
    gl = session.scalar(select(Account).where(Account.code == code))
    if gl is None:
        raise BusinessRuleError(f"Ledger account {code} is missing; run the seed command.")
    pa = PaymentAccount(
        name=data.name,
        kind=data.kind,
        account_id=gl.id,
        bank_name=data.bank_name,
        account_no=data.account_no,
    )
    session.add(pa)
    session.flush()
    session.refresh(pa)
    record_audit(
        session,
        actor_id=actor.id,
        action="create",
        entity_type="payment_account",
        entity_id=pa.id,
        after=snapshot(pa),
    )
    return pa


def update_payment_account(
    session: Session, payment_account_id: int, data: PaymentAccountUpdate, actor: CurrentUser
) -> PaymentAccount:
    pa = get_payment_account(session, payment_account_id)
    before = snapshot(pa)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("name") is None:
        changes.pop("name", None)
    elif session.scalar(
        select(PaymentAccount.id).where(
            func.lower(PaymentAccount.name) == changes["name"].lower(),
            PaymentAccount.id != pa.id,
        )
    ):
        raise ConflictError(
            "A payment account with this name already exists.",
            code="duplicate",
            errors=[{"field": "name", "message": "Already exists"}],
        )
    if pa.is_system and changes.get("is_active") is False:
        raise BusinessRuleError("System payment accounts cannot be deactivated.")
    if changes.get("is_active") is None:
        changes.pop("is_active", None)
    for field, value in changes.items():
        setattr(pa, field, value)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="update",
        entity_type="payment_account",
        entity_id=pa.id,
        before=before,
        after=snapshot(pa),
    )
    return pa


# --- Journal / trial balance ----------------------------------------------------------
def entry_to_read(entry: JournalEntry) -> JournalEntryRead:
    return JournalEntryRead(
        id=entry.id,
        entry_no=entry.entry_no,
        business_date=entry.business_date,
        source_doc_type=entry.source_doc_type,
        source_doc_id=entry.source_doc_id,
        source_doc_no=entry.source_doc_no,
        memo=entry.memo,
        reversal_of_id=entry.reversal_of_id,
        created_at=entry.created_at,
        lines=[
            JournalLineRead(
                id=line.id,
                account_code=line.account.code,
                account_name=line.account.name_en,
                debit=line.debit,
                credit=line.credit,
                party_type=line.party_type,
                party_id=line.party_id,
                payment_account_id=line.payment_account_id,
                memo=line.memo,
            )
            for line in entry.lines
        ],
    )


def list_entries(
    session: Session,
    params: PageParams,
    *,
    date_from: date | None,
    date_to: date | None,
    source_doc_type: str | None,
    q: str | None,
) -> tuple[Sequence[JournalEntry], int]:
    stmt = select(JournalEntry).order_by(JournalEntry.business_date.desc(), JournalEntry.id.desc())
    if date_from:
        stmt = stmt.where(JournalEntry.business_date >= date_from)
    if date_to:
        stmt = stmt.where(JournalEntry.business_date <= date_to)
    if source_doc_type:
        stmt = stmt.where(JournalEntry.source_doc_type == source_doc_type)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            JournalEntry.entry_no.ilike(like)
            | JournalEntry.source_doc_no.ilike(like)
            | JournalEntry.memo.ilike(like)
        )
    return paginate(session, stmt, params)


def trial_balance(session: Session, as_of: date | None) -> TrialBalanceRead:
    stmt = (
        select(
            Account.code,
            Account.name_en,
            Account.type,
            func.coalesce(func.sum(JournalLine.debit), 0),
            func.coalesce(func.sum(JournalLine.credit), 0),
        )
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .group_by(Account.code, Account.name_en, Account.type)
        .order_by(Account.code)
    )
    if as_of:
        stmt = stmt.where(JournalEntry.business_date <= as_of)
    rows: list[TrialBalanceRow] = []
    total_debit = total_credit = ZERO
    for code, name, type_, debit, credit in session.execute(stmt).all():
        net = Decimal(debit) - Decimal(credit)
        row_debit, row_credit = (net, ZERO) if net >= 0 else (ZERO, -net)
        rows.append(
            TrialBalanceRow(
                account_code=code,
                account_name=name,
                account_type=type_,
                debit=row_debit,
                credit=row_credit,
            )
        )
        total_debit += row_debit
        total_credit += row_credit
    return TrialBalanceRead(
        as_of=as_of,
        rows=rows,
        total_debit=total_debit,
        total_credit=total_credit,
        balanced=total_debit == total_credit,
    )


def party_ledger(
    session: Session,
    params: PageParams,
    *,
    account_code: str,
    party_type: str,
    party_id: int,
) -> tuple[list[LedgerEntryRead], int]:
    """Running-balance ledger of one customer/supplier on one control account (newest first)."""
    running = func.sum(JournalLine.debit - JournalLine.credit).over(
        order_by=(JournalEntry.business_date, JournalLine.id)
    )
    inner = (
        select(
            JournalLine.id.label("line_id"),
            JournalEntry.business_date,
            JournalEntry.entry_no,
            JournalEntry.memo,
            JournalEntry.source_doc_type,
            JournalEntry.source_doc_id,
            JournalEntry.source_doc_no,
            JournalLine.debit,
            JournalLine.credit,
            running.label("balance"),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(
            Account.code == account_code,
            JournalLine.party_type == party_type,
            JournalLine.party_id == party_id,
        )
        .subquery()
    )
    stmt = select(inner).order_by(inner.c.business_date.desc(), inner.c.line_id.desc())
    rows, total = paginate_rows(session, stmt, params)
    return [LedgerEntryRead.model_validate(row, from_attributes=True) for row in rows], total
