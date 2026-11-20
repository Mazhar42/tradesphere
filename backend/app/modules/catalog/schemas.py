from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.shared.money import NonNegativeMoney, NonNegativeQty, Qty
from app.shared.schemas import ReadSchema, WriteSchema

NameEn = Annotated[str, Field(min_length=1, max_length=100)]
NameBn = Annotated[str | None, Field(max_length=100)]


# --- Categories / brands / units -------------------------------------------
class CategoryRead(ReadSchema):
    id: int
    name_en: str
    name_bn: str | None
    parent_id: int | None
    is_active: bool


class CategoryCreate(WriteSchema):
    name_en: NameEn
    name_bn: NameBn = None
    parent_id: int | None = None


class CategoryUpdate(WriteSchema):
    name_en: NameEn | None = None
    name_bn: NameBn = None
    parent_id: int | None = None
    is_active: bool | None = None


class BrandRead(ReadSchema):
    id: int
    name_en: str
    name_bn: str | None
    is_active: bool


class BrandCreate(WriteSchema):
    name_en: NameEn
    name_bn: NameBn = None


class BrandUpdate(WriteSchema):
    name_en: NameEn | None = None
    name_bn: NameBn = None
    is_active: bool | None = None


UomCode = Annotated[
    str, StringConstraints(min_length=1, max_length=10, pattern=r"^[A-Za-z0-9]+$", to_upper=True)
]


class UomRead(ReadSchema):
    id: int
    code: str
    name_en: str
    name_bn: str | None
    is_active: bool


class UomCreate(WriteSchema):
    code: UomCode
    name_en: Annotated[str, Field(min_length=1, max_length=50)]
    name_bn: Annotated[str | None, Field(max_length=50)] = None


class UomUpdate(WriteSchema):
    name_en: Annotated[str | None, Field(min_length=1, max_length=50)] = None
    name_bn: Annotated[str | None, Field(max_length=50)] = None
    is_active: bool | None = None


# --- Products ---------------------------------------------------------------
Sku = Annotated[
    str,
    StringConstraints(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_.\-/]+$", to_upper=True),
]
Barcode = Annotated[str | None, Field(max_length=64)]


class ProductUomIn(WriteSchema):
    uom_id: int
    factor_to_base: Annotated[Decimal, Field(gt=1, max_digits=18, decimal_places=3)]
    is_purchase_default: bool = False
    is_sales_default: bool = False
    barcode: Barcode = None


class ProductUomRead(ReadSchema):
    id: int
    uom_id: int
    uom_code: str
    factor_to_base: Qty
    is_purchase_default: bool
    is_sales_default: bool
    barcode: str | None


def _check_uoms(uoms: list[ProductUomIn] | None) -> list[ProductUomIn] | None:
    if uoms is None:
        return None
    ids = [u.uom_id for u in uoms]
    if len(ids) != len(set(ids)):
        raise ValueError("Each unit may appear only once.")
    if sum(u.is_purchase_default for u in uoms) > 1:
        raise ValueError("Only one unit can be the purchase default.")
    if sum(u.is_sales_default for u in uoms) > 1:
        raise ValueError("Only one unit can be the sales default.")
    return uoms


class _ProductFields(WriteSchema):
    @field_validator("barcode", check_fields=False)
    @classmethod
    def _blank_barcode_is_none(cls, v: str | None) -> str | None:
        return v or None


class ProductCreate(_ProductFields):
    sku: Sku
    barcode: Barcode = None
    name_en: Annotated[str, Field(min_length=1, max_length=200)]
    name_bn: Annotated[str | None, Field(max_length=200)] = None
    category_id: int | None = None
    brand_id: int | None = None
    base_uom_id: int
    dp: NonNegativeMoney = Decimal("0")
    tp: NonNegativeMoney = Decimal("0")
    mrp: NonNegativeMoney = Decimal("0")
    reorder_level: NonNegativeQty = Decimal("0")
    track_batch: bool = True
    track_expiry: bool = True
    uoms: list[ProductUomIn] = Field(default_factory=list)

    _validate_uoms = field_validator("uoms")(_check_uoms)

    @model_validator(mode="after")
    def _prices(self) -> "ProductCreate":
        if self.mrp > 0 and self.tp > self.mrp:
            raise ValueError("Trade price (TP) cannot exceed MRP.")
        return self


class ProductUpdate(_ProductFields):
    sku: Sku | None = None
    barcode: Barcode = None
    name_en: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    name_bn: Annotated[str | None, Field(max_length=200)] = None
    category_id: int | None = None
    brand_id: int | None = None
    base_uom_id: int | None = None
    dp: NonNegativeMoney | None = None
    tp: NonNegativeMoney | None = None
    mrp: NonNegativeMoney | None = None
    reorder_level: NonNegativeQty | None = None
    track_batch: bool | None = None
    track_expiry: bool | None = None
    is_active: bool | None = None
    uoms: list[ProductUomIn] | None = None  # when given, replaces the full set

    _validate_uoms = field_validator("uoms")(_check_uoms)


class ProductRead(ReadSchema):
    id: int
    sku: str
    barcode: str | None
    name_en: str
    name_bn: str | None
    category_id: int | None
    category_name: str | None
    brand_id: int | None
    brand_name: str | None
    base_uom_id: int
    base_uom_code: str
    dp: NonNegativeMoney
    tp: NonNegativeMoney
    mrp: NonNegativeMoney
    reorder_level: NonNegativeQty
    track_batch: bool
    track_expiry: bool
    is_active: bool
    uoms: list[ProductUomRead]
    created_at: datetime
    updated_at: datetime
