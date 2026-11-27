"""CSV and XLSX to rows of text, defensively (data-import-and-coexistence.md §13).

Formulas and macros are never evaluated. Size, row count and, for XLSX, the unpacked size are
limited before anything is read into memory.
"""

import csv
import io
import zipfile
from datetime import date, datetime
from decimal import Decimal

from openpyxl import load_workbook

from app.core.errors import BusinessRuleError
from app.shared.text import nfc

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 50_000
MAX_COLUMNS = 60
MAX_UNPACKED = 80 * 1024 * 1024
MAX_RATIO = 150
ZIP_MAGIC = b"PK\x03\x04"


def _refuse(message: str, code: str = "bad_file") -> BusinessRuleError:
    return BusinessRuleError(message, code=code, errors=[{"field": "file", "message": message}])


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        # The shortest form of what the cell holds, without binary noise.
        return format(Decimal(repr(value)).normalize(), "f")
    return nfc(str(value)).strip()


def _check_zip(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise _refuse("The file is not a readable XLSX workbook.") from exc
    unpacked = sum(i.file_size for i in infos)
    packed = sum(i.compress_size for i in infos) or 1
    if unpacked > MAX_UNPACKED or unpacked / packed > MAX_RATIO:
        raise _refuse("The workbook unpacks to an unreasonable size.", "file_too_large")
    if any("vbaProject" in i.filename for i in infos):
        raise _refuse("Workbooks with macros are not accepted.")


def _xlsx_rows(data: bytes) -> list[list[str]]:
    _check_zip(data)
    book = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
    try:
        sheet = book.worksheets[0]
        rows: list[list[str]] = []
        for cells in sheet.iter_rows(max_col=MAX_COLUMNS, values_only=True):
            rows.append([_text(v) for v in cells])
            if len(rows) > MAX_ROWS + 1:
                break
        return rows
    finally:
        book.close()


def _csv_rows(data: bytes) -> list[list[str]]:
    if b"\x00" in data:
        raise _refuse("The file is not text. Save it as CSV (UTF-8) or XLSX.")
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise _refuse("The text encoding is not recognised. Save as CSV UTF-8.")
    first = text.split("\n", 1)[0]
    delimiter = max((",", ";", "\t"), key=first.count)
    rows = []
    for cells in csv.reader(io.StringIO(text), delimiter=delimiter):
        rows.append([_text(c) for c in cells[:MAX_COLUMNS]])
        if len(rows) > MAX_ROWS + 1:
            break
    return rows


def parse(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """(headers, rows). The type is taken from the content, not the file name."""
    if not data:
        raise _refuse("The file is empty.")
    if len(data) > MAX_BYTES:
        raise _refuse("The file is larger than 10 MB.", "file_too_large")
    rows = _xlsx_rows(data) if data.startswith(ZIP_MAGIC) else _csv_rows(data)
    rows = [r for r in rows if any(r)]
    if len(rows) < 2:
        raise _refuse("The file needs a header row and at least one data row.")
    if len(rows) - 1 > MAX_ROWS:
        raise _refuse(f"At most {MAX_ROWS} rows per file.", "file_too_large")
    headers = [h or f"column {i + 1}" for i, h in enumerate(rows[0])]
    if len(set(headers)) != len(headers):
        raise _refuse("Two columns have the same heading.")
    return headers, [
        {h: (r[i] if i < len(r) else "") for i, h in enumerate(headers)} for r in rows[1:]
    ]


def safe_cell(value: str) -> str:
    """Stop a spreadsheet from running an exported value as a formula."""
    return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value


def to_csv(headers: list[str], rows: list[list[str]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([safe_cell(h) for h in headers])
    writer.writerows([safe_cell(c) for c in row] for row in rows)
    return out.getvalue().encode("utf-8-sig")
