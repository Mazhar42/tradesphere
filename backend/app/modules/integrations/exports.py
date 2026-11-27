"""CSV for comparing with, or re-keying into, another system."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError
from app.modules.accounting.models import Account, JournalEntry, JournalLine
from app.modules.catalog.models import Product
from app.modules.imports.parsing import to_csv
from app.modules.inventory.models import ProductCost

MAX_DAYS = 366


def journal(session: Session, date_from: date, date_to: date) -> bytes:
    if not 0 <= (date_to - date_from).days < MAX_DAYS:
        raise BusinessRuleError("Choose a period of up to one year.", code="bad_period")
    rows = session.execute(
        select(JournalEntry, JournalLine, Account)
        .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(JournalEntry.business_date.between(date_from, date_to))
        .order_by(JournalEntry.business_date, JournalEntry.id, JournalLine.id)
    )
    return to_csv(
        ["date", "entry_no", "document", "account_code", "account", "debit", "credit", "memo"],
        [
            [
                str(entry.business_date),
                entry.entry_no,
                entry.source_doc_no or "",
                account.code,
                account.name_en,
                str(line.debit),
                str(line.credit),
                entry.memo,
            ]
            for entry, line, account in rows
        ],
    )


def stock(session: Session, *, with_cost: bool) -> bytes:
    rows = session.execute(
        select(Product, ProductCost)
        .join(ProductCost, ProductCost.product_id == Product.id)
        .where(ProductCost.qty_on_hand != 0)
        .order_by(Product.sku)
    )
    headers = ["sku", "name", "unit", "quantity"]
    if with_cost:
        headers += ["average_cost", "value"]
    lines = []
    for product, cost in rows:
        line = [product.sku, product.name_en, product.base_uom.code, str(cost.qty_on_hand)]
        if with_cost:
            line += [str(cost.avg_cost), str(cost.total_value)]
        lines.append(line)
    return to_csv(headers, lines)
