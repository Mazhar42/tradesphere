"""Expenses, fund transfers and owner transactions. Each posts its journal entry at once.

Journal
- Expense:        Dr expense account / Cr payment account.
- Fund transfer:  Dr destination account, Dr Bank & MFS charges (6060) / Cr source account.
- Owner capital:  Dr payment account / Cr Owner's Capital (3000).
- Owner drawing:  Dr Owner's Drawings (3100) / Cr payment account.
"""

from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.accounting.journal import JLine, post_journal
from app.modules.accounting.models import Account
from app.modules.accounting.service import require_active_payment_account
from app.modules.finance.models import Expense, FundTransfer, OwnerTransaction
from app.modules.finance.schemas import (
    ExpenseCreate,
    FundTransferCreate,
    OwnerTransactionCreate,
)
from app.modules.settings.service import ensure_period_open
from app.shared.audit import record_audit
from app.shared.dates import business_date as today
from app.shared.sequences import next_doc_number

BANK_CHARGES = "6060"
OWNER_CAPITAL = "3000"
OWNER_DRAWINGS = "3100"


def create_expense(session: Session, data: ExpenseCreate, actor: CurrentUser) -> Expense:
    account = session.get(Account, data.expense_account_id)
    if account is None or account.type != "expense" or not account.is_postable:
        raise BusinessRuleError(
            "Choose an expense account.",
            errors=[{"field": "expense_account_id", "message": "Not an expense account"}],
        )
    pa = require_active_payment_account(session, data.payment_account_id)
    on = data.business_date or today()
    ensure_period_open(session, on)
    expense = Expense(
        doc_no=next_doc_number(session, "EXP", on),
        business_date=on,
        expense_account_id=account.id,
        amount=data.amount,
        payment_account_id=pa.id,
        payee=data.payee,
        notes=data.notes,
        created_by_id=actor.id,
    )
    session.add(expense)
    session.flush()
    post_journal(
        session,
        business_date=on,
        source_doc_type="expense",
        source_doc_id=expense.id,
        source_doc_no=expense.doc_no,
        memo=f"Expense {expense.doc_no}: {account.name_en}",
        lines=[
            JLine(account.id, debit=data.amount, memo=data.payee or account.name_en),
            JLine(pa.account_id, credit=data.amount, payment_account_id=pa.id),
        ],
        actor_id=actor.id,
    )
    record_audit(
        session,
        actor_id=actor.id,
        action="post",
        entity_type="expense",
        entity_id=expense.id,
        after={"doc_no": expense.doc_no, "amount": data.amount},
    )
    return expense


def create_fund_transfer(
    session: Session, data: FundTransferCreate, actor: CurrentUser
) -> FundTransfer:
    if data.from_payment_account_id == data.to_payment_account_id:
        raise BusinessRuleError(
            "Choose two different accounts.",
            errors=[{"field": "to_payment_account_id", "message": "Same as the source"}],
        )
    source = require_active_payment_account(session, data.from_payment_account_id)
    target = require_active_payment_account(session, data.to_payment_account_id)
    on = data.business_date or today()
    ensure_period_open(session, on)
    transfer = FundTransfer(
        doc_no=next_doc_number(session, "FTR", on),
        business_date=on,
        from_payment_account_id=source.id,
        to_payment_account_id=target.id,
        amount=data.amount,
        charges=data.charges,
        reference=data.reference,
        notes=data.notes,
        created_by_id=actor.id,
    )
    session.add(transfer)
    session.flush()
    lines = [
        JLine(
            target.account_id,
            debit=data.amount,
            payment_account_id=target.id,
            memo=f"From {source.name}",
        ),
        JLine(
            source.account_id,
            credit=data.amount + data.charges,
            payment_account_id=source.id,
            memo=f"To {target.name}",
        ),
    ]
    if data.charges > 0:
        lines.append(JLine(BANK_CHARGES, debit=data.charges, memo=f"Charges {transfer.doc_no}"))
    post_journal(
        session,
        business_date=on,
        source_doc_type="fund_transfer",
        source_doc_id=transfer.id,
        source_doc_no=transfer.doc_no,
        memo=f"Transfer {transfer.doc_no}: {source.name} to {target.name}",
        lines=lines,
        actor_id=actor.id,
    )
    record_audit(
        session,
        actor_id=actor.id,
        action="post",
        entity_type="fund_transfer",
        entity_id=transfer.id,
        after={"doc_no": transfer.doc_no, "amount": data.amount, "charges": data.charges},
    )
    return transfer


def create_owner_transaction(
    session: Session, data: OwnerTransactionCreate, actor: CurrentUser
) -> OwnerTransaction:
    pa = require_active_payment_account(session, data.payment_account_id)
    on = data.business_date or today()
    ensure_period_open(session, on)
    owner = OwnerTransaction(
        doc_no=next_doc_number(session, "OWN", on),
        business_date=on,
        kind=data.kind,
        amount=data.amount,
        payment_account_id=pa.id,
        notes=data.notes,
        created_by_id=actor.id,
    )
    session.add(owner)
    session.flush()
    if data.kind == "capital":
        lines = [
            JLine(pa.account_id, debit=data.amount, payment_account_id=pa.id, memo="Owner capital"),
            JLine(OWNER_CAPITAL, credit=data.amount, memo="Owner capital"),
        ]
    else:
        lines = [
            JLine(OWNER_DRAWINGS, debit=data.amount, memo="Owner drawing"),
            JLine(
                pa.account_id, credit=data.amount, payment_account_id=pa.id, memo="Owner drawing"
            ),
        ]
    post_journal(
        session,
        business_date=on,
        source_doc_type="owner_transaction",
        source_doc_id=owner.id,
        source_doc_no=owner.doc_no,
        memo=f"Owner {data.kind} {owner.doc_no}",
        lines=lines,
        actor_id=actor.id,
    )
    record_audit(
        session,
        actor_id=actor.id,
        action="post",
        entity_type="owner_transaction",
        entity_id=owner.id,
        after={"doc_no": owner.doc_no, "kind": data.kind, "amount": data.amount},
    )
    return owner


def expense_read_model(expense: Expense) -> dict[str, object]:
    account = expense.expense_account
    return {
        "id": expense.id,
        "status": expense.status,
        "doc_no": expense.doc_no,
        "business_date": expense.business_date,
        "expense_account_code": account.code,
        "expense_account_name": account.name_en,
        "amount": expense.amount,
        "payment_account_name": expense.payment_account.name,
        "payee": expense.payee,
        "notes": expense.notes,
    }


def get_expense(session: Session, expense_id: int) -> Expense:
    expense = session.get(Expense, expense_id)
    if expense is None:
        raise NotFoundError("Expense not found.")
    return expense
