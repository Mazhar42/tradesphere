from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select

from app.core.db import DbSession
from app.core.permissions import CurrentUser, require
from app.modules.finance import reports, service
from app.modules.finance.models import Expense, FundTransfer, OwnerTransaction
from app.modules.finance.report_schemas import (
    Cashbook,
    ClaimsAging,
    Dashboard,
    Margin,
    NearExpiry,
    POStatus,
    ProfitLoss,
    StockValuation,
)
from app.modules.finance.schemas import (
    ExpenseCreate,
    ExpenseRead,
    FundTransferCreate,
    FundTransferRead,
    OwnerTransactionCreate,
    OwnerTransactionRead,
)
from app.modules.finance.xlsx import workbook_bytes
from app.shared.dates import business_date
from app.shared.idempotency import IdempotencyKeyHeader, run_idempotent
from app.shared.pagination import PageParamsDep, paginate
from app.shared.schemas import Page

router = APIRouter(tags=["finance"])

CanRead = Annotated[CurrentUser, Depends(require("finance:read"))]
CanWrite = Annotated[CurrentUser, Depends(require("finance:write"))]
CanProfit = Annotated[CurrentUser, Depends(require("reports:profit"))]
CanReports = Annotated[CurrentUser, Depends(require("reports:read"))]
CanClaims = Annotated[CurrentUser, Depends(require("claims:read"))]
CanPurchases = Annotated[CurrentUser, Depends(require("purchasing:read"))]
Format = Annotated[str | None, Query(pattern="^xlsx$")]

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _export(name: str, headers: list[str], rows: list[list[object]]) -> Response:
    return Response(
        content=workbook_bytes(name, headers, rows),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
    )


# --- Expenses, transfers and owner transactions ------------------------------------
def _expense_read(e: Expense) -> ExpenseRead:
    return ExpenseRead.model_validate(service.expense_read_model(e))


@router.get("/expenses", response_model=Page[ExpenseRead])
def list_expenses(db: DbSession, _: CanRead, page: PageParamsDep) -> Page[ExpenseRead]:
    rows, total = paginate(
        db, select(Expense).order_by(Expense.business_date.desc(), Expense.id.desc()), page
    )
    return Page(
        items=[_expense_read(e) for e in rows],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post("/expenses", response_model=ExpenseRead, status_code=status.HTTP_201_CREATED)
def create_expense(
    db: DbSession, actor: CanWrite, data: ExpenseCreate, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint="POST /expenses",
        payload=data.model_dump(mode="json"),
        status_code=201,
        handler=lambda: _expense_read(service.create_expense(db, data, actor)),
    )
    db.commit()
    return result


@router.get("/fund-transfers", response_model=Page[FundTransferRead])
def list_fund_transfers(db: DbSession, _: CanRead, page: PageParamsDep) -> Page[FundTransferRead]:
    rows, total = paginate(
        db,
        select(FundTransfer).order_by(FundTransfer.business_date.desc(), FundTransfer.id.desc()),
        page,
    )
    items = [
        FundTransferRead(
            id=t.id,
            doc_no=t.doc_no,
            business_date=t.business_date,
            from_account=t.from_account.name,
            to_account=t.to_account.name,
            amount=t.amount,
            charges=t.charges,
            reference=t.reference,
            notes=t.notes,
        )
        for t in rows
    ]
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.post(
    "/fund-transfers", response_model=FundTransferRead, status_code=status.HTTP_201_CREATED
)
def create_fund_transfer(
    db: DbSession, actor: CanWrite, data: FundTransferCreate, key: IdempotencyKeyHeader
) -> object:
    def handler() -> FundTransferRead:
        t = service.create_fund_transfer(db, data, actor)
        return FundTransferRead(
            id=t.id,
            doc_no=t.doc_no,
            business_date=t.business_date,
            from_account=t.from_account.name,
            to_account=t.to_account.name,
            amount=t.amount,
            charges=t.charges,
            reference=t.reference,
            notes=t.notes,
        )

    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint="POST /fund-transfers",
        payload=data.model_dump(mode="json"),
        status_code=201,
        handler=handler,
    )
    db.commit()
    return result


@router.get("/owner-transactions", response_model=Page[OwnerTransactionRead])
def list_owner_transactions(
    db: DbSession, _: CanRead, page: PageParamsDep
) -> Page[OwnerTransactionRead]:
    rows, total = paginate(
        db,
        select(OwnerTransaction).order_by(
            OwnerTransaction.business_date.desc(), OwnerTransaction.id.desc()
        ),
        page,
    )
    items = [
        OwnerTransactionRead(
            id=o.id,
            doc_no=o.doc_no,
            business_date=o.business_date,
            kind=o.kind,
            amount=o.amount,
            payment_account_name=o.payment_account.name,
            notes=o.notes,
        )
        for o in rows
    ]
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.post(
    "/owner-transactions", response_model=OwnerTransactionRead, status_code=status.HTTP_201_CREATED
)
def create_owner_transaction(
    db: DbSession, actor: CanWrite, data: OwnerTransactionCreate, key: IdempotencyKeyHeader
) -> object:
    def handler() -> OwnerTransactionRead:
        o = service.create_owner_transaction(db, data, actor)
        return OwnerTransactionRead(
            id=o.id,
            doc_no=o.doc_no,
            business_date=o.business_date,
            kind=o.kind,
            amount=o.amount,
            payment_account_name=o.payment_account.name,
            notes=o.notes,
        )

    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint="POST /owner-transactions",
        payload=data.model_dump(mode="json"),
        status_code=201,
        handler=handler,
    )
    db.commit()
    return result


# --- Reports -----------------------------------------------------------------------------
@router.get("/reports/profit-loss", response_model=ProfitLoss)
def profit_loss_report(
    db: DbSession,
    _: CanProfit,
    date_from: date,
    date_to: date,
    format_: Annotated[str | None, Query(alias="format", pattern="^xlsx$")] = None,
) -> object:
    report = reports.profit_loss(db, date_from, date_to)
    if format_ == "xlsx":
        headers, rows = reports.profit_loss_table(report)
        return _export("profit-loss", headers, rows)
    return report


@router.get("/reports/cashbook", response_model=Cashbook)
def cashbook_report(
    db: DbSession,
    _: CanRead,
    date_from: date,
    date_to: date,
    payment_account_id: int | None = None,
    format_: Annotated[str | None, Query(alias="format", pattern="^xlsx$")] = None,
) -> object:
    report = reports.cashbook(db, date_from, date_to, payment_account_id)
    if format_ == "xlsx":
        headers, rows = reports.cashbook_table(report)
        return _export("cashbook", headers, rows)
    return report


@router.get("/reports/margin", response_model=Margin)
def margin_report(
    db: DbSession,
    _: CanProfit,
    date_from: date,
    date_to: date,
    by: Annotated[str, Query(pattern="^(product|customer|category|brand)$")] = "product",
    format_: Annotated[str | None, Query(alias="format", pattern="^xlsx$")] = None,
) -> object:
    report = reports.margin(db, date_from, date_to, by)
    if format_ == "xlsx":
        headers, rows = reports.margin_table(report)
        return _export(f"margin-by-{by}", headers, rows)
    return report


@router.get("/reports/stock-valuation", response_model=StockValuation)
def stock_valuation_report(
    db: DbSession,
    _: CanProfit,
    format_: Annotated[str | None, Query(alias="format", pattern="^xlsx$")] = None,
) -> object:
    report = reports.stock_valuation(db)
    if format_ == "xlsx":
        headers, rows = reports.valuation_table(report)
        return _export("stock-valuation", headers, rows)
    return report


@router.get("/reports/near-expiry", response_model=NearExpiry)
def near_expiry_report(
    db: DbSession,
    _: CanReports,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    format_: Annotated[str | None, Query(alias="format", pattern="^xlsx$")] = None,
) -> object:
    report = reports.near_expiry(db, days)
    if format_ == "xlsx":
        headers, rows = reports.near_expiry_table(report)
        return _export("near-expiry", headers, rows)
    return report


@router.get("/reports/claims-aging", response_model=ClaimsAging)
def claims_aging_report(
    db: DbSession,
    _: CanClaims,
    as_of: date | None = None,
    format_: Annotated[str | None, Query(alias="format", pattern="^xlsx$")] = None,
) -> object:
    report = reports.claims_aging(db, as_of or business_date())
    if format_ == "xlsx":
        headers, rows = reports.claims_aging_table(report)
        return _export("claims-aging", headers, rows)
    return report


@router.get("/reports/po-status", response_model=POStatus)
def po_status_report(
    db: DbSession,
    _: CanPurchases,
    format_: Annotated[str | None, Query(alias="format", pattern="^xlsx$")] = None,
) -> object:
    report = reports.po_status(db)
    if format_ == "xlsx":
        headers, rows = reports.po_status_table(report)
        return _export("po-status", headers, rows)
    return report


@router.get("/reports/dashboard", response_model=Dashboard)
def dashboard_report(db: DbSession, user: CanReports) -> Dashboard:
    return reports.dashboard(db, show_profit=user.has("reports:profit"))
