from datetime import date

from app.shared.money import Money, Qty, UnitCost
from app.shared.schemas import ReadSchema


class PLLine(ReadSchema):
    label: str
    amount: Money


class ProfitLoss(ReadSchema):
    from_date: date
    to_date: date
    gross_sales: Money
    sales_returns: Money
    dealer_discounts: Money
    net_sales: Money
    cogs: Money
    gross_profit: Money
    gross_margin_pct: Money
    scheme_income: Money
    other_income: Money
    write_offs: Money
    dealer_commission: Money
    operating_expenses: Money
    net_profit: Money
    expense_lines: list[PLLine]


class CashDay(ReadSchema):
    business_date: date
    opening: Money
    inflow: Money
    outflow: Money
    closing: Money


class Cashbook(ReadSchema):
    from_date: date
    to_date: date
    opening: Money
    inflow: Money
    outflow: Money
    closing: Money
    days: list[CashDay]


class MarginRow(ReadSchema):
    label: str
    qty_base: Qty
    gross_sales: Money
    returns: Money
    dealer_discounts: Money
    net_sales: Money
    cogs: Money
    gross_profit: Money
    margin_pct: Money


class Margin(ReadSchema):
    by: str
    from_date: date
    to_date: date
    rows: list[MarginRow]


class ValuationRow(ReadSchema):
    sku: str
    name: str
    qty_base: Qty
    avg_cost: UnitCost
    value: Money


class StockValuation(ReadSchema):
    rows: list[ValuationRow]
    total_value: Money


class NearExpiryRow(ReadSchema):
    sku: str
    name: str
    batch_no: str
    expiry_date: date
    days_left: int
    qty_base: Qty
    value: Money


class NearExpiry(ReadSchema):
    days: int
    rows: list[NearExpiryRow]


class ClaimAgingRow(ReadSchema):
    supplier: str
    current: Money
    days_31_60: Money
    days_61_90: Money
    days_over_90: Money
    total: Money


class ClaimsAging(ReadSchema):
    as_of: date
    rows: list[ClaimAgingRow]
    total: Money


class POStatusRow(ReadSchema):
    doc_no: str
    supplier: str
    status: str
    ordered: Money
    received: Money
    undelivered: Money
    paid: Money
    balance_due: Money
    advance: Money


class POStatus(ReadSchema):
    rows: list[POStatusRow]


class CashAccountBalance(ReadSchema):
    name: str
    balance: Money


class SalesDay(ReadSchema):
    business_date: date
    net_sales: Money


class Dashboard(ReadSchema):
    today: date
    today_gross: Money
    today_returns: Money
    today_net_sales: Money
    today_gross_profit: Money | None  # only with reports:profit
    today_margin_pct: Money | None
    cash_total: Money
    cash_accounts: list[CashAccountBalance]
    customer_dues: Money
    supplier_advance: Money
    claims_outstanding: Money
    stock_value: Money | None  # only with reports:profit
    low_stock_count: int
    near_expiry_count: int
    sales_last_30_days: list[SalesDay]
