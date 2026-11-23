from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field

from app.shared.money import Money, PositiveQty, Qty
from app.shared.schemas import ReadSchema, WriteSchema

LocationCode = Literal["SELLABLE", "DAMAGED", "EXPIRED"]
Notes = Annotated[str | None, Field(max_length=1000)]


class AdjustmentLineIn(WriteSchema):
    product_id: int
    batch_id: int | None = None  # required for batch-tracked products
    location: LocationCode = "SELLABLE"
    qty_delta: Qty  # signed base units: positive adds stock, negative removes it


class AdjustmentCreate(WriteSchema):
    reason: Literal["count", "damage", "expiry", "found", "other"]
    business_date: date | None = None
    notes: Notes = None
    items: Annotated[list[AdjustmentLineIn], Field(min_length=1, max_length=200)]


class AdjustmentItemRead(ReadSchema):
    id: int
    product_id: int
    sku: str
    name_en: str
    name_bn: str | None
    base_uom_code: str
    batch_id: int | None
    batch_no: str | None
    location: str
    qty_delta: Qty
    value: Money


class AdjustmentRead(ReadSchema):
    id: int
    doc_no: str | None
    business_date: date
    reason: str
    status: str
    notes: str | None
    posted_at: datetime | None
    items: list[AdjustmentItemRead]


class TransferLineIn(WriteSchema):
    product_id: int
    batch_id: int | None = None
    qty_base: PositiveQty


class TransferCreate(WriteSchema):
    from_location: LocationCode
    to_location: LocationCode
    business_date: date | None = None
    notes: Notes = None
    items: Annotated[list[TransferLineIn], Field(min_length=1, max_length=200)]


class TransferItemRead(ReadSchema):
    id: int
    product_id: int
    sku: str
    name_en: str
    name_bn: str | None
    base_uom_code: str
    batch_id: int | None
    batch_no: str | None
    qty_base: Qty
    value: Money


class TransferRead(ReadSchema):
    id: int
    doc_no: str | None
    business_date: date
    from_location: str
    to_location: str
    status: str
    notes: str | None
    posted_at: datetime | None
    items: list[TransferItemRead]
