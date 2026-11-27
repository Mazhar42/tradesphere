from datetime import date, datetime

from app.shared.money import Money, NonNegativeQty, Qty, UnitCost
from app.shared.schemas import ReadSchema


class LocationRead(ReadSchema):
    id: int
    code: str
    name_en: str
    name_bn: str | None
    is_sellable: bool
    is_active: bool


class UnitInfo(ReadSchema):
    uom_code: str
    factor_to_base: Qty


class StockRow(ReadSchema):
    product_id: int
    sku: str
    name_en: str
    name_bn: str | None
    base_uom_code: str
    uoms: list[UnitInfo]
    qty_sellable: NonNegativeQty
    qty_damaged: NonNegativeQty
    qty_expired: NonNegativeQty
    qty_total: NonNegativeQty
    avg_cost: UnitCost | None  # null when the user may not see costs (reports:profit)
    value: Money | None
    reorder_level: NonNegativeQty
    is_low: bool


class BatchStockRow(ReadSchema):
    product_id: int
    sku: str
    name_en: str
    name_bn: str | None
    base_uom_code: str
    uoms: list[UnitInfo]
    batch_id: int | None
    batch_no: str | None
    mfg_date: date | None
    expiry_date: date | None
    days_to_expiry: int | None
    location_code: str
    qty_base: NonNegativeQty


class MovementRead(ReadSchema):
    id: int
    business_date: date
    movement_type: str
    source_doc_type: str
    source_doc_id: int
    source_doc_no: str | None
    batch_no: str | None
    location_code: str
    qty_base: Qty
    unit_cost: UnitCost | None
    value: Money | None
    reversal_of_id: int | None
    created_at: datetime


class MovementPage(ReadSchema):
    items: list[MovementRead]
    next_after: int | None


class StockTotals(ReadSchema):
    total_value: Money | None
    products_in_stock: int
    low_stock_count: int
    expiring_soon_count: int
    near_expiry_days: int


class BatchRead(ReadSchema):
    id: int
    product_id: int
    batch_no: str
    mfg_date: date | None
    expiry_date: date | None


class ReconcileIssue(ReadSchema):
    check: str
    detail: str


class ReconcileRead(ReadSchema):
    ran_at: datetime | None = None  # None: checked just now
    ok: bool
    issues: list[ReconcileIssue]
