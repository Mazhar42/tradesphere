"""Reading one cell. Money and quantities never pass through float; nothing is rounded."""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

BANGLA_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
DATE_FORMATS = {"YYYY-MM-DD": "%Y-%m-%d", "DD/MM/YYYY": "%d/%m/%Y", "MM/DD/YYYY": "%m/%d/%Y"}


class Invalid(ValueError):
    """A message for the person who will fix the file."""


def number(value: str, *, places: int, positive: bool = False) -> Decimal:
    cleaned = value.translate(BANGLA_DIGITS).replace(",", "").strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        raise Invalid(f"'{value}' is not a number")
    try:
        result = Decimal(cleaned)
    except InvalidOperation as exc:
        raise Invalid(f"'{value}' is not a number") from exc
    exponent = result.normalize().as_tuple().exponent
    if isinstance(exponent, int) and -exponent > places:
        raise Invalid(f"'{value}' has more than {places} decimals")
    if result < 0 or (positive and result == 0):
        raise Invalid(f"'{value}' must be {'above' if positive else 'at least'} zero")
    if result >= Decimal(10) ** 12:
        raise Invalid(f"'{value}' is too large")
    return result


def day(value: str, fmt: str) -> date:
    try:
        # Excel date cells arrive already as ISO text.
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        return datetime.strptime(value.translate(BANGLA_DIGITS), DATE_FORMATS[fmt]).date()
    except ValueError as exc:
        raise Invalid(f"'{value}' is not a date ({fmt})") from exc


def yes_no(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in ("yes", "y", "true", "1", "হ্যাঁ"):
        return True
    if lowered in ("no", "n", "false", "0", "না"):
        return False
    raise Invalid(f"'{value}' is not yes or no")


def code(value: str) -> str:
    """A SKU, code or barcode. A number Excel turned into 8.94E+12 has lost digits."""
    cleaned = value.translate(BANGLA_DIGITS).strip()
    if re.fullmatch(r"\d+(\.\d+)?[eE]\+?\d+", cleaned):
        raise Invalid(f"'{value}' was shortened by the spreadsheet; format the column as text")
    return cleaned
