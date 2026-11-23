"""Minimal Excel (.xlsx) writer for report exports. One sheet, text and numbers, no dependencies."""

import io
import zipfile
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

_OOXML = "application/vnd.openxmlformats-officedocument"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'

CONTENT_TYPES = (
    _XML_HEAD
    + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    + '<Default Extension="rels" ContentType="application/'
    + 'vnd.openxmlformats-package.relationships+xml"/>'
    + '<Default Extension="xml" ContentType="application/xml"/>'
    + f'<Override PartName="/xl/workbook.xml" ContentType="{_OOXML}'
    + 'spreadsheetml.sheet.main+xml"/>'
    + f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{_OOXML}'
    + 'spreadsheetml.worksheet+xml"/>'
    + "</Types>"
)

ROOT_RELS = (
    _XML_HEAD
    + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    + f'<Relationship Id="rId1" Type="{_REL}/officeDocument" Target="xl/workbook.xml"/>'
    + "</Relationships>"
)

WORKBOOK_RELS = (
    _XML_HEAD
    + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    + f'<Relationship Id="rId1" Type="{_REL}/worksheet" Target="worksheets/sheet1.xml"/>'
    + "</Relationships>"
)


def _column(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _cell(ref: str, value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{int(value)}</v></c>'
    if isinstance(value, Decimal):
        return f'<c r="{ref}"><v>{value:f}</v></c>'
    if isinstance(value, int | float):
        return f'<c r="{ref}"><v>{value}</v></c>'
    if isinstance(value, date):
        return f'<c r="{ref}" t="inlineStr"><is><t>{value.isoformat()}</t></is></c>'
    return (
        f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'
    )


def workbook_bytes(sheet_name: str, headers: list[str], rows: list[list[object]]) -> bytes:
    """A workbook with one sheet: a bold-free header row followed by the data rows."""
    body = []
    table: list[list[object]] = [list(headers), *rows]
    for r, values in enumerate(table, start=1):
        cells = "".join(_cell(f"{_column(c)}{r}", v) for c, v in enumerate(values))
        body.append(f'<row r="{r}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(body)}</sheetData></worksheet>"
    )
    name = escape(sheet_name[:31] or "Report", {'"': "&quot;"})
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", ROOT_RELS)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", WORKBOOK_RELS)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()
