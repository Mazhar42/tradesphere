from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from app.core.db import DbSession
from app.core.permissions import CurrentUser, require
from app.modules.accounting import service
from app.modules.accounting.models import Account
from app.modules.accounting.schemas import (
    AccountRead,
    JournalEntryRead,
    PaymentAccountCreate,
    PaymentAccountRead,
    PaymentAccountUpdate,
    TrialBalanceRead,
)
from app.shared.pagination import CursorQuery, PageParamsDep
from app.shared.schemas import Page

router = APIRouter(tags=["accounting"])

CanRead = Annotated[CurrentUser, Depends(require("finance:read"))]
CanWrite = Annotated[CurrentUser, Depends(require("finance:write"))]


@router.get("/accounts", response_model=list[AccountRead])
def list_accounts(db: DbSession, _: CanRead) -> list[Account]:
    return list(db.scalars(select(Account).order_by(Account.code)).all())


# --- Payment accounts ---------------------------------------------------------------------
@router.get("/payment-accounts", response_model=list[PaymentAccountRead])
def list_payment_accounts(
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require("purchasing:pay"))],
    active_only: bool = False,
) -> list[PaymentAccountRead]:
    del user
    return service.list_payment_accounts(db, active_only=active_only)


@router.post(
    "/payment-accounts", response_model=PaymentAccountRead, status_code=status.HTTP_201_CREATED
)
def create_payment_account(
    db: DbSession, actor: CanWrite, data: PaymentAccountCreate
) -> PaymentAccountRead:
    pa = service.create_payment_account(db, data, actor)
    db.commit()
    return service.read_payment_account(db, pa)


@router.patch("/payment-accounts/{payment_account_id}", response_model=PaymentAccountRead)
def update_payment_account(
    db: DbSession, actor: CanWrite, payment_account_id: int, data: PaymentAccountUpdate
) -> PaymentAccountRead:
    pa = service.update_payment_account(db, payment_account_id, data, actor)
    db.commit()
    return service.read_payment_account(db, pa)


# --- Journal and trial balance ----------------------------------------------------------------
@router.get("/journal", response_model=Page[JournalEntryRead])
def list_journal(
    db: DbSession,
    _: CanRead,
    page: PageParamsDep,
    date_from: date | None = None,
    date_to: date | None = None,
    source_doc_type: Annotated[str | None, Query(max_length=20)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    cursor: CursorQuery = None,
) -> Page[JournalEntryRead]:
    entries, total, following = service.list_entries(
        db,
        page,
        date_from=date_from,
        date_to=date_to,
        source_doc_type=source_doc_type,
        q=q,
        cursor=cursor,
    )
    return Page(
        items=[service.entry_to_read(e) for e in entries],
        total=total,
        page=page.page,
        page_size=page.page_size,
        next_cursor=following,
    )


@router.get("/reports/trial-balance", response_model=TrialBalanceRead)
def get_trial_balance(db: DbSession, _: CanRead, as_of: date | None = None) -> TrialBalanceRead:
    return service.trial_balance(db, as_of)
