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


def _to_str(value: Decimal) -> str:
    return format(value, "f")


_as_string = PlainSerializer(_to_str, return_type=str, when_used="json")

# --- API types --------------------------------------------------------------
Money = Annotated[Decimal, Field(max_digits=18, decimal_places=2), _as_string]
NonNegativeMoney = Annotated[Decimal, Field(max_digits=18, decimal_places=2, ge=0), _as_string]
Qty = Annotated[Decimal, Field(max_digits=18, decimal_places=3), _as_string]
NonNegativeQty = Annotated[Decimal, Field(max_digits=18, decimal_places=3, ge=0), _as_string]
PositiveQty = Annotated[Decimal, Field(max_digits=18, decimal_places=3, gt=0), _as_string]
UnitCost = Annotated[Decimal, Field(max_digits=18, decimal_places=4), _as_string]


def round_money(value: Decimal) -> Decimal:
    """Round half-up to 2 decimal places (Taka + paisa)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
