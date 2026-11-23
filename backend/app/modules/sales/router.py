from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.core.db import DbSession
from app.core.errors import NotFoundError
from app.core.permissions import CurrentUser, require
from app.modules.accounting.journal import RECEIVABLE
from app.modules.accounting.models import PaymentAccount
from app.modules.accounting.schemas import LedgerEntryRead
from app.modules.accounting.service import party_ledger
from app.modules.parties.models import Customer
from app.modules.sales import invoices, receipts, reports, returns
from app.modules.sales.printing import render_invoice
from app.modules.sales.schemas import (
    AgingReport,
    CustomerSummary,
    InvoiceListItem,
    InvoiceRead,
    InvoiceWrite,
    PaymentOption,
    ReceiptCreate,
    ReceiptListItem,
    ReceiptRead,
    ReturnableRow,
    SalesReturnCreate,
    SalesReturnListItem,
    SalesReturnRead,
    SalesSummaryRow,
)
from app.modules.settings.schemas import BusinessProfile
from app.modules.settings.service import get_setting
from app.shared.dates import business_date
from app.shared.idempotency import IdempotencyKeyHeader, run_idempotent
from app.shared.pagination import PageParamsDep
from app.shared.schemas import Page

router = APIRouter(tags=["sales"])

CanRead = Annotated[CurrentUser, Depends(require("sales:read"))]
CanWrite = Annotated[CurrentUser, Depends(require("sales:write"))]
CanReverse = Annotated[CurrentUser, Depends(require("sales:reverse"))]
CanCustomers = Annotated[CurrentUser, Depends(require("customers:read"))]
CanReports = Annotated[CurrentUser, Depends(require("reports:read"))]
SearchQ = Annotated[str | None, Query(max_length=100)]


def _customer(db: DbSession, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise NotFoundError("Customer not found.")
    return customer


# --- Sales invoices ----------------------------------------------------------------------------
@router.get("/sales-invoices", response_model=Page[InvoiceListItem])
def list_sales_invoices(
    db: DbSession,
    _: CanRead,
    page: PageParamsDep,
    q: SearchQ = None,
    status_: Annotated[str | None, Query(alias="status", max_length=10)] = None,
    customer_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[InvoiceListItem]:
    items, total = invoices.list_invoices(
        db,
        page,
        q=q,
        status=status_,
        customer_id=customer_id,
        date_from=date_from,
        date_to=date_to,
    )
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.post("/sales-invoices", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
def create_sales_invoice(db: DbSession, actor: CanWrite, data: InvoiceWrite) -> InvoiceRead:
    invoice = invoices.create_draft(db, data, actor)
    db.commit()
    return invoices.invoice_to_read(db, invoice)


@router.get("/sales-invoices/{invoice_id}", response_model=InvoiceRead)
def get_sales_invoice(db: DbSession, _: CanRead, invoice_id: int) -> InvoiceRead:
    return invoices.invoice_to_read(db, invoices.get_invoice(db, invoice_id))


@router.patch("/sales-invoices/{invoice_id}", response_model=InvoiceRead)
def update_sales_invoice(
    db: DbSession, actor: CanWrite, invoice_id: int, data: InvoiceWrite
) -> InvoiceRead:
    invoice = invoices.update_draft(db, invoice_id, data, actor)
    db.commit()
    return invoices.invoice_to_read(db, invoice)


@router.delete("/sales-invoices/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sales_invoice(db: DbSession, actor: CanWrite, invoice_id: int) -> Response:
    invoices.delete_draft(db, invoice_id, actor)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sales-invoices/{invoice_id}/post", response_model=InvoiceRead)
def post_sales_invoice(
    db: DbSession, actor: CanWrite, invoice_id: int, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint=f"POST /sales-invoices/{invoice_id}/post",
        payload={"id": invoice_id},
        handler=lambda: invoices.invoice_to_read(db, invoices.post_invoice(db, invoice_id, actor)),
    )
    db.commit()
    return result


@router.post("/sales-invoices/{invoice_id}/reverse", response_model=InvoiceRead)
def reverse_sales_invoice(
    db: DbSession, actor: CanReverse, invoice_id: int, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint=f"POST /sales-invoices/{invoice_id}/reverse",
        payload={"id": invoice_id},
        handler=lambda: invoices.invoice_to_read(
            db, invoices.reverse_invoice(db, invoice_id, actor)
        ),
    )
    db.commit()
    return result


@router.get("/sales-invoices/{invoice_id}/print", response_class=HTMLResponse)
def print_sales_invoice(
    db: DbSession,
    _: CanRead,
    invoice_id: int,
    paper: Annotated[str, Query(pattern="^(a4|thermal80)$")] = "a4",
) -> HTMLResponse:
    invoice = invoices.invoice_to_read(db, invoices.get_invoice(db, invoice_id))
    profile = get_setting(db, "business_profile", BusinessProfile)
    html = render_invoice(invoice, profile, "thermal80" if paper == "thermal80" else "a4")
    return HTMLResponse(html)


# --- Customer receipts -------------------------------------------------------------------------
@router.get("/sales-payment-accounts", response_model=list[PaymentOption])
def list_sales_payment_accounts(db: DbSession, _: CanRead) -> list[PaymentOption]:
    """Active accounts that cash can be received into (for the sales counter)."""
    accounts = db.scalars(
        select(PaymentAccount)
        .where(PaymentAccount.is_active.is_(True))
        .order_by(PaymentAccount.name)
    ).all()
    return [PaymentOption(id=a.id, name=a.name, kind=a.kind) for a in accounts]


@router.get("/customer-receipts", response_model=Page[ReceiptListItem])
def list_customer_receipts(
    db: DbSession,
    _: CanRead,
    page: PageParamsDep,
    customer_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[ReceiptListItem]:
    rows, total = receipts.list_receipts(
        db, page, customer_id=customer_id, date_from=date_from, date_to=date_to
    )
    return Page(
        items=[receipts.receipt_list_item(r) for r in rows],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post("/customer-receipts", response_model=ReceiptRead, status_code=status.HTTP_201_CREATED)
def create_customer_receipt(
    db: DbSession, actor: CanWrite, data: ReceiptCreate, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint="POST /customer-receipts",
        payload=data.model_dump(mode="json"),
        status_code=201,
        handler=lambda: receipts.receipt_to_read(receipts.create_receipt(db, data, actor)),
    )
    db.commit()
    return result


@router.get("/customer-receipts/{receipt_id}", response_model=ReceiptRead)
def get_customer_receipt(db: DbSession, _: CanRead, receipt_id: int) -> ReceiptRead:
    return receipts.receipt_to_read(receipts.get_receipt(db, receipt_id))


@router.post("/customer-receipts/{receipt_id}/reverse", response_model=ReceiptRead)
def reverse_customer_receipt(
    db: DbSession, actor: CanReverse, receipt_id: int, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint=f"POST /customer-receipts/{receipt_id}/reverse",
        payload={"id": receipt_id},
        handler=lambda: receipts.receipt_to_read(receipts.reverse_receipt(db, receipt_id, actor)),
    )
    db.commit()
    return result


# --- Sales returns -----------------------------------------------------------------------------
CanReturn = Annotated[CurrentUser, Depends(require("sales:return"))]


@router.get("/sales-invoices/{invoice_id}/returnable", response_model=list[ReturnableRow])
def get_returnable(db: DbSession, _: CanRead, invoice_id: int) -> list[ReturnableRow]:
    return returns.returnable_for_invoice(db, invoices.get_invoice(db, invoice_id))


@router.get("/sales-returns", response_model=Page[SalesReturnListItem])
def list_sales_returns(
    db: DbSession,
    _: CanRead,
    page: PageParamsDep,
    status_: Annotated[str | None, Query(alias="status", max_length=10)] = None,
    invoice_id: int | None = None,
) -> Page[SalesReturnListItem]:
    rows, total = returns.list_returns(db, page, status=status_, invoice_id=invoice_id)
    return Page(
        items=[returns.return_list_item(r) for r in rows],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )


@router.post("/sales-returns", response_model=SalesReturnRead, status_code=status.HTTP_201_CREATED)
def create_sales_return(
    db: DbSession, actor: CanReturn, data: SalesReturnCreate
) -> SalesReturnRead:
    ret = returns.create_return(db, data, actor)
    db.commit()
    return returns.return_to_read(ret)


@router.get("/sales-returns/{return_id}", response_model=SalesReturnRead)
def get_sales_return(db: DbSession, _: CanRead, return_id: int) -> SalesReturnRead:
    return returns.return_to_read(returns.get_return(db, return_id))


@router.delete("/sales-returns/{return_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sales_return(db: DbSession, actor: CanReturn, return_id: int) -> Response:
    returns.delete_draft(db, return_id, actor)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sales-returns/{return_id}/post", response_model=SalesReturnRead)
def post_sales_return(
    db: DbSession, actor: CanReturn, return_id: int, key: IdempotencyKeyHeader
) -> object:
    result = run_idempotent(
        db,
        user_id=actor.id,
        key=key,
        endpoint=f"POST /sales-returns/{return_id}/post",
        payload={"id": return_id},
        handler=lambda: returns.return_to_read(returns.post_return(db, return_id, actor)),
    )
    db.commit()
    return result


# --- Customers ----------------------------------------------------------------------------------
@router.get("/customers/{customer_id}/summary", response_model=CustomerSummary)
def get_customer_summary(db: DbSession, _: CanCustomers, customer_id: int) -> CustomerSummary:
    return reports.customer_summary(db, _customer(db, customer_id))


@router.get("/customers/{customer_id}/ledger", response_model=Page[LedgerEntryRead])
def get_customer_ledger(
    db: DbSession, _: CanCustomers, page: PageParamsDep, customer_id: int
) -> Page[LedgerEntryRead]:
    _customer(db, customer_id)
    items, total = party_ledger(
        db, page, account_code=RECEIVABLE, party_type="customer", party_id=customer_id
    )
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


# --- Reports ------------------------------------------------------------------------------------
@router.get("/reports/customer-aging", response_model=AgingReport)
def get_customer_aging(db: DbSession, _: CanReports, as_of: date | None = None) -> AgingReport:
    return reports.aging(db, as_of or business_date())


@router.get("/reports/sales-summary", response_model=list[SalesSummaryRow])
def get_sales_summary(
    db: DbSession,
    user: CanReports,
    date_from: date,
    date_to: date,
    group: Annotated[str, Query(pattern="^(day|month)$")] = "day",
) -> list[SalesSummaryRow]:
    rows = reports.sales_summary(db, date_from=date_from, date_to=date_to, group=group)
    if user.has("reports:profit"):
        return rows
    return [row.model_copy(update={"cogs": None, "gross_profit": None}) for row in rows]
