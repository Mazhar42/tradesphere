"""Printable sales invoice as a self-contained HTML page (A4 or 80 mm thermal roll)."""

from decimal import Decimal
from html import escape
from typing import Literal

from app.modules.sales.schemas import InvoiceRead
from app.modules.settings.schemas import BusinessProfile

Paper = Literal["a4", "thermal80"]


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _qty(value: Decimal) -> str:
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def render_invoice(invoice: InvoiceRead, profile: BusinessProfile, paper: Paper) -> str:
    thermal = paper == "thermal80"
    width = "72mm" if thermal else "190mm"
    page = "80mm auto" if thermal else "A4"
    rows = []
    for item in invoice.items:
        name = escape(item.name_en)
        if item.name_bn:
            name += f"<br><span class='bn'>{escape(item.name_bn)}</span>"
        qty = f"{_qty(item.qty_entry)} {escape(item.uom_code)}"
        if item.uom_code != item.base_uom_code:
            qty += f"<br><small>= {_qty(item.qty_base)} {escape(item.base_uom_code)}</small>"
        if item.free_qty_base > 0:
            qty += f"<br><small>+ {_qty(item.free_qty_entry)} {escape(item.uom_code)} free</small>"
        discount = item.dealer_discount + item.company_discount
        rows.append(
            "<tr>"
            f"<td>{name}</td>"
            f"<td class='r'>{qty}</td>"
            f"<td class='r'>{_money(item.unit_price)}</td>"
            f"<td class='r'>{_money(discount)}</td>"
            f"<td class='r'>{_money(item.line_net)}</td>"
            "</tr>"
        )
    total_discount = invoice.dealer_discount + invoice.company_discount
    address = f"<div>{escape(profile.address)}</div>" if profile.address else ""
    phone = f"<div>{escape(profile.phone)}</div>" if profile.phone else ""
    footer = (
        f"<p class='foot'>{escape(profile.invoice_footer)}</p>" if profile.invoice_footer else ""
    )
    customer = escape(invoice.customer_name)
    if invoice.customer_shop_name:
        customer += f"<br>{escape(invoice.customer_shop_name)}"
    doc_no = escape(invoice.doc_no or "DRAFT")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Invoice {doc_no}</title>
<style>
  @page {{ size: {page}; margin: {"3mm" if thermal else "12mm"}; }}
  body {{ font-family: "Nirmala UI", "Vrinda", "Segoe UI", Arial, sans-serif; color: #111;
         width: {width}; margin: 0 auto; font-size: {"11px" if thermal else "13px"}; }}
  h1 {{ font-size: {"15px" if thermal else "20px"}; margin: 0 0 2px; text-align: center; }}
  .muted {{ color: #555; }}
  .center {{ text-align: center; }}
  .meta {{ display: flex; justify-content: space-between; gap: 8px; margin: 8px 0; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ padding: {"2px 1px" if thermal else "5px 6px"}; vertical-align: top; }}
  th {{ border-bottom: 1px solid #111; text-align: left; font-weight: 600; }}
  tr td {{ border-bottom: 1px dotted #bbb; }}
  .r {{ text-align: right; white-space: nowrap; }}
  .totals td {{ border: none; }}
  .grand td {{ font-weight: 700; border-top: 1px solid #111; }}
  .bn {{ font-size: 0.95em; }}
  .foot {{ text-align: center; margin-top: 10px; color: #555; }}
  @media print {{ .noprint {{ display: none; }} }}
</style>
</head>
<body>
<div class="center">
  <h1>{escape(profile.name)}</h1>
  {address}{phone}
</div>
<div class="meta">
  <div><strong>Invoice</strong> {doc_no}<br>
    <span class="muted">Date {invoice.business_date.isoformat()}</span></div>
  <div style="text-align:right">{customer}</div>
</div>
<table>
  <thead><tr>
    <th>Item</th><th class="r">Qty</th><th class="r">Rate</th>
    <th class="r">Disc.</th><th class="r">Amount</th>
  </tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>
<table class="totals" style="margin-top:6px">
  <tr><td></td><td class="r">Subtotal</td><td class="r">{_money(invoice.gross_amount)}</td></tr>
  <tr><td></td><td class="r">Discount</td><td class="r">-{_money(total_discount)}</td></tr>
  <tr class="grand"><td></td><td class="r">Total</td>
    <td class="r">Tk {_money(invoice.net_amount)}</td></tr>
  <tr><td></td><td class="r">Paid</td><td class="r">{_money(invoice.received_amount)}</td></tr>
  <tr><td></td><td class="r">Due</td><td class="r">{_money(invoice.due_amount)}</td></tr>
</table>
{footer}
</body>
</html>
"""
