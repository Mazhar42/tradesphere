"""Financial and operational reports. Figures come from the journal, the daily sales projection
and the stock ledgers, never from typed-in totals.

Profit and loss follows docs/plan.md section 3.4:
  Net Sales = Gross Sales - Sales Returns (4100) - Dealer Discounts (4200)
            (company-funded discounts are not deducted: the company pays them)
  Gross Profit = Net Sales - COGS (5000)
  Net Profit = Gross Profit + Scheme Income (4300) + Other Income (4900)
               - write-offs (5100, 5300, 5400) - dealer commission (5200)
               - operating expenses (6xxx)
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.modules.accounting.journal import RECEIVABLE, SUPPLIER_PAYABLE
from app.modules.accounting.models import Account, JournalEntry, JournalLine
from app.modules.accounting.service import list_payment_accounts
from app.modules.catalog.models import Product
from app.modules.claims.models import ClaimableItem, CompanyClaimLine
from app.modules.finance.report_schemas import (
    CashAccountBalance,
    Cashbook,
    CashDay,
    ClaimAgingRow,
    ClaimsAging,
    Dashboard,
    Margin,
    MarginRow,
    NearExpiry,
    NearExpiryRow,
    PLLine,
    POStatus,
    POStatusRow,
    ProfitLoss,
    SalesDay,
    StockValuation,
    ValuationRow,
)
from app.modules.inventory.models import Batch, Location, ProductCost, StockBalance
from app.modules.parties.models import Customer, Supplier
from app.modules.purchasing.models import PurchaseOrder
from app.modules.purchasing.service import paid_by_po, summarize
from app.modules.sales.models import (
    DailySalesSummary,
    SalesInvoice,
    SalesInvoiceItem,
    SalesReturn,
    SalesReturnItem,
)
from app.modules.settings.schemas import Preferences
from app.modules.settings.service import get_setting
from app.shared.dates import business_date
from app.shared.money import ZERO

ZERO_PCT = Decimal("0.00")


def _pct(part: Decimal, whole: Decimal) -> Decimal:
    if whole <= 0:
        return ZERO_PCT
    return (part * 100 / whole).quantize(Decimal("0.01"))


def _activity(session: Session, date_from: date, date_to: date) -> dict[str, Decimal]:
    """Net debit minus credit per account code for entries dated in the range."""
    rows = session.execute(
        select(Account.code, func.sum(JournalLine.debit - JournalLine.credit))
        .select_from(JournalLine)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(JournalEntry.business_date >= date_from, JournalEntry.business_date <= date_to)
        .group_by(Account.code)
    ).all()
    return {code: Decimal(value or 0) for code, value in rows}


def _balance_as_of(session: Session, account_code: str, as_of: date) -> Decimal:
    value = session.scalar(
        select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), ZERO))
        .select_from(JournalLine)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(Account.code == account_code, JournalEntry.business_date <= as_of)
    )
    return Decimal(value or 0)


# --- Profit and loss -----------------------------------------------------------------------------
def profit_loss(session: Session, date_from: date, date_to: date) -> ProfitLoss:
    act = _activity(session, date_from, date_to)

    def net(code: str) -> Decimal:
        return act.get(code, ZERO)

    gross = -net("4000")
    returns = net("4100")
    dealer = net("4200")
    net_sales = gross - returns - dealer
    cogs = net("5000")
    gross_profit = net_sales - cogs
    scheme = -net("4300")
    other = -net("4900")
    write_offs = net("5100") + net("5300") + net("5400")
    commission = net("5200")

    expense_accounts = session.scalars(
        select(Account)
        .where(Account.type == "expense", Account.code.like("6%"), Account.is_postable.is_(True))
        .order_by(Account.code)
    ).all()
    expense_lines = [
        PLLine(label=f"{a.code} {a.name_en}", amount=net(a.code))
        for a in expense_accounts
        if net(a.code) != 0
    ]
    opex = sum((line.amount for line in expense_lines), ZERO)
    net_profit = gross_profit + scheme + other - write_offs - commission - opex
    return ProfitLoss(
        from_date=date_from,
        to_date=date_to,
        gross_sales=gross,
        sales_returns=returns,
        dealer_discounts=dealer,
        net_sales=net_sales,
        cogs=cogs,
        gross_profit=gross_profit,
        gross_margin_pct=_pct(gross_profit, net_sales),
        scheme_income=scheme,
        other_income=other,
        write_offs=write_offs,
        dealer_commission=commission,
        operating_expenses=opex,
        net_profit=net_profit,
        expense_lines=expense_lines,
    )


def profit_loss_table(report: ProfitLoss) -> tuple[list[str], list[list[object]]]:
    rows: list[list[object]] = [
        ["Gross sales", report.gross_sales],
        ["Sales returns", report.sales_returns],
        ["Dealer discounts", report.dealer_discounts],
        ["Net sales", report.net_sales],
        ["Cost of goods sold", report.cogs],
        ["Gross profit", report.gross_profit],
        ["Gross margin %", report.gross_margin_pct],
        ["Scheme and claim income", report.scheme_income],
        ["Other income", report.other_income],
        ["Write-offs", report.write_offs],
        ["Dealer commission and promotion", report.dealer_commission],
        ["Operating expenses", report.operating_expenses],
        *[[line.label, line.amount] for line in report.expense_lines],
        ["Net profit", report.net_profit],
    ]
    return ["Line", "Amount"], rows


# --- Cashbook ------------------------------------------------------------------------------------
def cashbook(
    session: Session, date_from: date, date_to: date, payment_account_id: int | None
) -> Cashbook:
    line_filter: list[ColumnElement[bool]] = [JournalLine.payment_account_id.is_not(None)]
    if payment_account_id is not None:
        line_filter.append(JournalLine.payment_account_id == payment_account_id)

    opening = Decimal(
        session.scalar(
            select(func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), ZERO))
            .select_from(JournalLine)
            .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
            .where(JournalEntry.business_date < date_from, *line_filter)
        )
        or 0
    )
    daily = session.execute(
        select(
            JournalEntry.business_date,
            func.sum(JournalLine.debit),
            func.sum(JournalLine.credit),
        )
        .select_from(JournalLine)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(
            JournalEntry.business_date >= date_from,
            JournalEntry.business_date <= date_to,
            *line_filter,
        )
        .group_by(JournalEntry.business_date)
        .order_by(JournalEntry.business_date)
    ).all()
    running = opening
    days: list[CashDay] = []
    total_in = ZERO
    total_out = ZERO
    for day, debit, credit in daily:
        inflow, outflow = Decimal(debit or 0), Decimal(credit or 0)
        start = running
        running = start + inflow - outflow
        total_in += inflow
        total_out += outflow
        days.append(
            CashDay(
                business_date=day,
                opening=start,
                inflow=inflow,
                outflow=outflow,
                closing=running,
            )
        )
    return Cashbook(
        from_date=date_from,
        to_date=date_to,
        opening=opening,
        inflow=total_in,
        outflow=total_out,
        closing=running,
        days=days,
    )


def cashbook_table(report: Cashbook) -> tuple[list[str], list[list[object]]]:
    headers = ["Date", "Opening", "In", "Out", "Closing"]
    rows: list[list[object]] = [
        [d.business_date.isoformat(), d.opening, d.inflow, d.outflow, d.closing]
        for d in report.days
    ]
    return headers, rows


# --- Margins -------------------------------------------------------------------------------------
@dataclass
class _Acc:
    label: str
    qty: Decimal = ZERO
    gross: Decimal = ZERO
    returns: Decimal = ZERO
    dealer: Decimal = ZERO
    cogs: Decimal = ZERO
    extra: dict[str, Decimal] = field(default_factory=dict)


MARGIN_KEYS = ("product", "customer", "category", "brand")


def margin(session: Session, date_from: date, date_to: date, by: str) -> Margin:
    if by not in MARGIN_KEYS:
        by = "product"
    groups: dict[object, _Acc] = {}

    def group(key: object, label: str) -> _Acc:
        if key not in groups:
            groups[key] = _Acc(label=label)
        return groups[key]

    def keys(product: Product, customer_id: int, customer_name: str) -> tuple[object, str]:
        if by == "product":
            return product.id, f"{product.sku} · {product.name_en}"
        if by == "customer":
            return customer_id, customer_name
        if by == "category":
            cat = product.category
            return (cat.id if cat else 0), (cat.name_en if cat else "Uncategorised")
        brand = product.brand
        return (brand.id if brand else 0), (brand.name_en if brand else "No brand")

    invoice_rows = session.execute(
        select(SalesInvoiceItem, SalesInvoice.customer_id, Customer.name)
        .join(SalesInvoice, SalesInvoice.id == SalesInvoiceItem.invoice_id)
        .join(Customer, Customer.id == SalesInvoice.customer_id)
        .where(
            SalesInvoice.status == "posted",
            SalesInvoice.business_date >= date_from,
            SalesInvoice.business_date <= date_to,
        )
    ).all()
    for item, customer_id, customer_name in invoice_rows:
        key, label = keys(item.product, customer_id, customer_name)
        acc = group(key, label)
        acc.qty += item.qty_base
        acc.gross += item.gross_amount
        acc.dealer += item.dealer_discount
        acc.cogs += item.cogs

    return_rows = session.execute(
        select(SalesReturnItem, SalesReturn.customer_id, Customer.name)
        .join(SalesReturn, SalesReturn.id == SalesReturnItem.return_id)
        .join(Customer, Customer.id == SalesReturn.customer_id)
        .where(
            SalesReturn.status == "posted",
            SalesReturn.business_date >= date_from,
            SalesReturn.business_date <= date_to,
        )
    ).all()
    for ritem, customer_id, customer_name in return_rows:
        key, label = keys(ritem.product, customer_id, customer_name)
        acc = group(key, label)
        acc.qty -= ritem.qty_base
        acc.returns += ritem.gross_amount
        acc.dealer -= ritem.dealer_discount
        acc.cogs -= ritem.cogs

    rows: list[MarginRow] = []
    for acc in groups.values():
        net = acc.gross - acc.returns - acc.dealer
        gp = net - acc.cogs
        rows.append(
            MarginRow(
                label=acc.label,
                qty_base=acc.qty,
                gross_sales=acc.gross,
                returns=acc.returns,
                dealer_discounts=acc.dealer,
                net_sales=net,
                cogs=acc.cogs,
                gross_profit=gp,
                margin_pct=_pct(gp, net),
            )
        )
    rows.sort(key=lambda r: r.gross_profit, reverse=True)
    return Margin(by=by, from_date=date_from, to_date=date_to, rows=rows)


def margin_table(report: Margin) -> tuple[list[str], list[list[object]]]:
    headers = [
        "Label",
        "Qty (base)",
        "Gross",
        "Returns",
        "Dealer discounts",
        "Net sales",
        "COGS",
        "Gross profit",
        "Margin %",
    ]
    rows: list[list[object]] = [
        [
            r.label,
            r.qty_base,
            r.gross_sales,
            r.returns,
            r.dealer_discounts,
            r.net_sales,
            r.cogs,
            r.gross_profit,
            r.margin_pct,
        ]
        for r in report.rows
    ]
    return headers, rows


# --- Stock valuation and near-expiry -----------------------------------------------
def stock_valuation(session: Session) -> StockValuation:
    rows = session.execute(
        select(ProductCost, Product)
        .join(Product, Product.id == ProductCost.product_id)
        .where(ProductCost.qty_on_hand > 0)
        .order_by(Product.sku)
    ).all()
    items = [
        ValuationRow(
            sku=product.sku,
            name=product.name_en,
            qty_base=cost.qty_on_hand,
            avg_cost=cost.avg_cost,
            value=cost.total_value,
        )
        for cost, product in rows
    ]
    return StockValuation(rows=items, total_value=sum((r.value for r in items), ZERO))


def valuation_table(report: StockValuation) -> tuple[list[str], list[list[object]]]:
    return ["SKU", "Product", "Qty (base)", "Avg cost", "Value"], [
        [r.sku, r.name, r.qty_base, r.avg_cost, r.value] for r in report.rows
    ]


def near_expiry(session: Session, days: int) -> NearExpiry:
    today = business_date()
    rows = session.execute(
        select(StockBalance.qty_base, Batch, Product, ProductCost.avg_cost)
        .join(Location, Location.id == StockBalance.location_id)
        .join(Batch, Batch.id == StockBalance.batch_id)
        .join(Product, Product.id == Batch.product_id)
        .join(ProductCost, ProductCost.product_id == Product.id)
        .where(
            Location.code == "SELLABLE",
            StockBalance.qty_base > 0,
            Batch.expiry_date.is_not(None),
            Batch.expiry_date <= today + timedelta(days=days),
        )
        .order_by(Batch.expiry_date, Product.sku)
    ).all()
    items = [
        NearExpiryRow(
            sku=product.sku,
            name=product.name_en,
            batch_no=batch.batch_no,
            expiry_date=batch.expiry_date,
            days_left=((batch.expiry_date or today) - today).days,
            qty_base=qty,
            value=(qty * avg_cost).quantize(Decimal("0.01")),
        )
        for qty, batch, product, avg_cost in rows
    ]
    return NearExpiry(days=days, rows=items)


def near_expiry_table(report: NearExpiry) -> tuple[list[str], list[list[object]]]:
    return ["SKU", "Product", "Batch", "Expiry", "Days left", "Qty (base)", "Value"], [
        [r.sku, r.name, r.batch_no, r.expiry_date.isoformat(), r.days_left, r.qty_base, r.value]
        for r in report.rows
    ]


# --- Claims aging and PO status -------------------------------------------------------------------
def _bucket(days: int) -> str:
    if days <= 30:
        return "current"
    if days <= 60:
        return "days_31_60"
    if days <= 90:
        return "days_61_90"
    return "days_over_90"


def _supplier_name(session: Session, supplier_id: int) -> str:
    supplier = session.get(Supplier, supplier_id)
    return supplier.name if supplier else str(supplier_id)


def claims_aging(session: Session, as_of: date) -> ClaimsAging:
    rows = session.execute(
        select(ClaimableItem, CompanyClaimLine)
        .outerjoin(CompanyClaimLine, CompanyClaimLine.claimable_item_id == ClaimableItem.id)
        .where(ClaimableItem.status.in_(("open", "in_claim")))
    ).all()
    per_supplier: dict[int, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    names: dict[int, str] = {}
    for item, line in rows:
        if line is not None:
            amount = line.claimed_amount - line.settled_amount - line.rejected_amount
        else:
            amount = item.claim_amount
        if amount <= 0:
            continue
        bucket = _bucket((as_of - item.business_date).days)
        per_supplier[item.supplier_id][bucket] += amount
        if item.supplier_id not in names:
            names[item.supplier_id] = _supplier_name(session, item.supplier_id)
    out = []
    for supplier_id, buckets in per_supplier.items():
        total = sum(buckets.values(), ZERO)
        out.append(
            ClaimAgingRow(
                supplier=names[supplier_id],
                current=buckets["current"],
                days_31_60=buckets["days_31_60"],
                days_61_90=buckets["days_61_90"],
                days_over_90=buckets["days_over_90"],
                total=total,
            )
        )
    out.sort(key=lambda r: r.supplier)
    return ClaimsAging(as_of=as_of, rows=out, total=sum((r.total for r in out), ZERO))


def claims_aging_table(report: ClaimsAging) -> tuple[list[str], list[list[object]]]:
    return ["Company", "0-30", "31-60", "61-90", "Over 90", "Total"], [
        [r.supplier, r.current, r.days_31_60, r.days_61_90, r.days_over_90, r.total]
        for r in report.rows
    ]


def po_status(session: Session) -> POStatus:
    orders = session.scalars(
        select(PurchaseOrder)
        .where(PurchaseOrder.status.not_in(("draft", "cancelled")))
        .order_by(PurchaseOrder.business_date.desc(), PurchaseOrder.id.desc())
    ).all()
    paid = paid_by_po(session, [o.id for o in orders])
    rows = []
    for po in orders:
        s = summarize(po, paid.get(po.id, ZERO))
        rows.append(
            POStatusRow(
                doc_no=po.doc_no or f"PO-{po.id}",
                supplier=po.supplier.name,
                status=po.status,
                ordered=s.ordered_value,
                received=s.received_value,
                undelivered=s.remaining_value,
                paid=s.paid_value,
                balance_due=s.balance_due,
                advance=s.unapplied_advance,
            )
        )
    return POStatus(rows=rows)


def po_status_table(report: POStatus) -> tuple[list[str], list[list[object]]]:
    return [
        "PO",
        "Company",
        "Status",
        "Ordered",
        "Received",
        "Undelivered",
        "Paid",
        "Balance due",
        "Advance",
    ], [
        [
            r.doc_no,
            r.supplier,
            r.status,
            r.ordered,
            r.received,
            r.undelivered,
            r.paid,
            r.balance_due,
            r.advance,
        ]
        for r in report.rows
    ]


# --- Dashboard -----------------------------------------------------------------------------------
def dashboard(session: Session, *, show_profit: bool) -> Dashboard:
    today = business_date()
    prefs = get_setting(session, "preferences", Preferences)

    gross_today = Decimal(0)
    returns_today = Decimal(0)
    dealer_today = Decimal(0)
    cogs_today = Decimal(0)
    summary_rows = session.scalars(
        select(DailySalesSummary).where(DailySalesSummary.business_date == today)
    ).all()
    for row in summary_rows:
        gross_today += row.gross_amount
        returns_today += row.returns_amount
        dealer_today += row.dealer_discount
        cogs_today += row.cogs
    net_today = gross_today - returns_today - dealer_today
    gp_today = net_today - cogs_today

    accounts = list_payment_accounts(session, active_only=True)
    cash_accounts = [CashAccountBalance(name=a.name, balance=a.balance) for a in accounts]
    cash_total = sum((a.balance for a in accounts), ZERO)

    customer_dues = _balance_as_of(session, RECEIVABLE, today)
    supplier_advance = _advance_total(session)
    claims_out = _balance_as_of(session, "1300", today)

    stock_value = session.scalar(select(func.coalesce(func.sum(ProductCost.total_value), 0)))
    low_stock = session.scalar(
        select(func.count())
        .select_from(ProductCost)
        .join(Product, Product.id == ProductCost.product_id)
        .where(Product.reorder_level > 0, ProductCost.qty_on_hand < Product.reorder_level)
    )
    near = near_expiry(session, prefs.near_expiry_days)

    since = today - timedelta(days=29)
    per_day: dict[date, Decimal] = defaultdict(lambda: ZERO)
    for row in session.scalars(
        select(DailySalesSummary).where(DailySalesSummary.business_date >= since)
    ).all():
        per_day[row.business_date] += row.gross_amount - row.returns_amount - row.dealer_discount
    sales_30 = [
        SalesDay(
            business_date=since + timedelta(days=i),
            net_sales=per_day.get(since + timedelta(days=i), ZERO),
        )
        for i in range(30)
    ]

    return Dashboard(
        today=today,
        today_gross=gross_today,
        today_returns=returns_today,
        today_net_sales=net_today,
        today_gross_profit=gp_today if show_profit else None,
        today_margin_pct=_pct(gp_today, net_today) if show_profit else None,
        cash_total=cash_total,
        cash_accounts=cash_accounts,
        customer_dues=customer_dues,
        supplier_advance=supplier_advance,
        claims_outstanding=claims_out,
        stock_value=Decimal(stock_value or 0) if show_profit else None,
        low_stock_count=int(low_stock or 0),
        near_expiry_count=len(near.rows),
        sales_last_30_days=sales_30,
    )


def _advance_total(session: Session) -> Decimal:
    """Money the company holds for us: supplier accounts with a debit balance."""
    rows = session.execute(
        select(JournalLine.party_id, func.sum(JournalLine.debit - JournalLine.credit))
        .join(Account, Account.id == JournalLine.account_id)
        .where(Account.code == SUPPLIER_PAYABLE, JournalLine.party_type == "supplier")
        .group_by(JournalLine.party_id)
    ).all()
    return sum((max(Decimal(v or 0), ZERO) for _, v in rows), ZERO)
