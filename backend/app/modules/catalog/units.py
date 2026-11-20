"""Quantity conversion between product units and the base unit.

All stock quantities are stored in base units. A product may have larger
units (e.g. 1 CTN = 24 PCS, 1 BOX = 12 PCS) used for entry and display.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.core.errors import BusinessRuleError


@dataclass(frozen=True)
class UnitFactor:
    code: str
    factor: Decimal  # base units per one of this unit


@dataclass(frozen=True)
class QtyPart:
    code: str
    qty: Decimal


def to_base(parts: Sequence[QtyPart], units: Sequence[UnitFactor], base_code: str) -> Decimal:
    """[2 CTN, 5 PCS] with CTN=24 -> 53 base units."""
    factors = {u.code: u.factor for u in units}
    factors[base_code] = Decimal(1)
    total = Decimal(0)
    for part in parts:
        if part.code not in factors:
            raise BusinessRuleError(f"Unit {part.code} is not configured for this product.")
        if part.qty < 0:
            raise BusinessRuleError("Quantity cannot be negative.")
        total += part.qty * factors[part.code]
    return total


def split_qty(base_qty: Decimal, units: Sequence[UnitFactor], base_code: str) -> list[QtyPart]:
    """53 base units with CTN=24 -> [2 CTN, 5 PCS]. Largest unit first; zeros omitted."""
    remaining = abs(base_qty)
    parts: list[QtyPart] = []
    for unit in sorted(units, key=lambda u: u.factor, reverse=True):
        whole = remaining // unit.factor
        if whole:
            parts.append(QtyPart(unit.code, whole))
            remaining -= whole * unit.factor
    if remaining or not parts:
        parts.append(QtyPart(base_code, remaining))
    if base_qty < 0:
        parts = [QtyPart(p.code, -p.qty) for p in parts]
    return parts
