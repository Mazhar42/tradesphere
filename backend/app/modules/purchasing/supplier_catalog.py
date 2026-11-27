"""A company's price list lines and the trail of their prices."""

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.purchasing.models import SupplierPrice, SupplierProduct
from app.shared.dates import now_utc


def upsert_supplier_product(
    session: Session,
    *,
    supplier_id: int,
    supplier_sku: str,
    source: str,
    values: dict[str, Any],
) -> SupplierProduct:
    """Add or refresh one line of a company's list, keeping a trail of price changes. A link
    to one of our products is never removed or guessed here."""
    item = session.scalar(
        select(SupplierProduct)
        .where(
            SupplierProduct.supplier_id == supplier_id,
            SupplierProduct.supplier_sku == supplier_sku,
        )
        .with_for_update()
    )
    price: Decimal | None = values.get("price")
    if item is None:
        item = SupplierProduct(
            supplier_id=supplier_id, supplier_sku=supplier_sku, source=source, **values
        )
        session.add(item)
        changed = price is not None
    else:
        changed = price is not None and price != item.price
        for name, value in values.items():
            if value is not None and not (name == "product_id" and item.product_id):
                setattr(item, name, value)
        item.source = source
        item.last_seen_at = now_utc()
    session.flush()
    if changed and price is not None:
        session.add(
            SupplierPrice(
                supplier_product_id=item.id,
                price=price,
                valid_from=values.get("valid_from"),
                source=source,
            )
        )
    return item
