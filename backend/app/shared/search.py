"""What a person types into a search box, made safe to match (search-and-discovery.md §2-3).

Normalised to NFC with Bangla digits turned into Latin ones; `%` and `_` mean themselves.
Search runs fully under row-level security (ADR-008): PostgreSQL will not use trigram indexes
there, so matching filters the dealership's own rows.
"""

from typing import Any

from sqlalchemy import ColumnElement, and_, or_

from app.shared.text import nfc

BANGLA_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
MAX_WORDS = 6


def clean(query: str) -> str:
    return " ".join(nfc(query).translate(BANGLA_DIGITS).split())


def escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_pattern(query: str) -> str:
    """For `column.ilike(pattern)`: contains, with wildcards taken literally."""
    return f"%{escape(clean(query))}%"


def prefix_pattern(query: str) -> str:
    return f"{escape(clean(query))}%"


def all_words(query: str, *columns: Any) -> ColumnElement[bool]:
    """Every word appears in some column, in any order, English or Bangla."""
    words = clean(query).split()[:MAX_WORDS] or [""]
    return and_(*(or_(*(c.ilike(f"%{escape(w)}%") for c in columns)) for w in words))


GTIN_LENGTHS = (8, 12, 13, 14)


def is_gtin(code: str) -> bool:
    """A GTIN-8/12/13/14 with a correct check digit. Other codes remain usable, they are just
    not global product numbers."""
    if len(code) not in GTIN_LENGTHS or not code.isdigit():
        return False
    digits = [int(c) for c in code.zfill(14)]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(digits[:13]))
    return (10 - total % 10) % 10 == digits[13]
