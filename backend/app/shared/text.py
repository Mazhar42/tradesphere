"""Text is stored in one Unicode form (NFC).

Bangla vowel signs can be typed either as one code point or as two; both look the same but
compare as different, which breaks search, uniqueness and duplicate detection.
"""

import unicodedata
from typing import Any


def nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def nfc_deep(value: Any) -> Any:
    """NFC for a string, or for the strings directly inside a list."""
    if isinstance(value, str):
        return nfc(value)
    if isinstance(value, list):
        return [nfc(v) if isinstance(v, str) else v for v in value]
    return value
