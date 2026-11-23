from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field

from app.modules.inventory.doc_schemas import LocationCode
from app.shared.money import Money, NonNegativeUnitCost, PositiveQty, Qty, UnitCost
from app.shared.schemas import ReadSchema, WriteSchema

Notes = Annotated[str | None, Field(max_length=1000)]


class PurchaseReturnLineIn(WriteSchema):
    product_id: int
    batch_id: int | None = None  # required for batch-tracked products
    location: LocationCode = "SELLABLE"  # where the goods are now (DAMAGED for damaged stock)
    qty_base: PositiveQty
    unit_value: NonNegativeUnitCost  # the credit agreed with the company, per base unit
    settlement: Literal["supplier_credit", "claim"] = "supplier_credit"


class PurchaseReturnCreate(WriteSchema):
    supplier_id: int
    grn_id: int | None = None
    business_date: date | None = None
    notes: Notes = None
    items: Annotated[list[PurchaseReturnLineIn], Field(min_length=1, max_length=200)]


class PurchaseReturnItemRead(ReadSchema):
    id: int
    product_id: int
    sku: str
    name_en: str
    name_bn: str | None
    base_uom_code: str
    batch_id: int | None
    batch_no: str | None
    location: str
    qty_base: Qty
    unit_value: UnitCost
    settlement: str
    value: Money
    cost: Money


class PurchaseReturnRead(ReadSchema):
    id: int
    doc_no: str | None
    supplier_id: int
    supplier_name: str
    grn_id: int | None
    business_date: date
    status: str
    notes: str | None
    total_value: Money
    total_cost: Money
    posted_at: datetime | None
    items: list[PurchaseReturnItemRead]
