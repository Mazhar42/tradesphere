"""Decimal-safe money / quantity types for both the database and the API.

Money never touches float. On the wire, decimals are serialised as strings
(e.g. "1250.00") so JavaScript clients cannot silently lose precision.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import Field, PlainSerializer
from sqlalchemy import Numeric

# --- Database column types -------------------------------------------------
MONEY = Numeric(18, 2)
QTY = Numeric(18, 3)
UNIT_COST = Numeric(18, 4)

ZERO = Decimal("0")
CENT = Decimal("0.01")


def _serializer(places: int) -> PlainSerializer:
    """Serialise a Decimal as a string with exactly `places` decimals ("0" -> "0.00")."""
    quantum = Decimal(1).scaleb(-places)

    def _to_str(value: Decimal) -> str:
        return format(value.quantize(quantum), "f")

    return PlainSerializer(_to_str, return_type=str, when_used="json")


_money_str = _serializer(2)
_qty_str = _serializer(3)
_cost_str = _serializer(4)

# --- API types --------------------------------------------------------------
Money = Annotated[Decimal, Field(max_digits=18, decimal_places=2), _money_str]
NonNegativeMoney = Annotated[Decimal, Field(max_digits=18, decimal_places=2, ge=0), _money_str]
PositiveMoney = Annotated[Decimal, Field(max_digits=18, decimal_places=2, gt=0), _money_str]
Qty = Annotated[Decimal, Field(max_digits=18, decimal_places=3), _qty_str]
NonNegativeQty = Annotated[Decimal, Field(max_digits=18, decimal_places=3, ge=0), _qty_str]
PositiveQty = Annotated[Decimal, Field(max_digits=18, decimal_places=3, gt=0), _qty_str]
UnitCost = Annotated[Decimal, Field(max_digits=18, decimal_places=4), _cost_str]
NonNegativeUnitCost = Annotated[Decimal, Field(max_digits=18, decimal_places=4, ge=0), _cost_str]


def round_money(value: Decimal) -> Decimal:
    """Round half-up to 2 decimal places (Taka + paisa)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
