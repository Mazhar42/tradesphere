from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError
from app.modules.catalog.models import Product
from app.modules.inventory.models import Batch


def resolve_batch(
    session: Session,
    product: Product,
    *,
    batch_no: str | None,
    mfg_date: date | None,
    expiry_date: date | None,
    received_on: date,
    allow_expired: bool = False,
    field_prefix: str = "",
) -> Batch | None:
    """Find or create the batch for a received line, applying the product's tracking rules.

    Products that do not track batches always return None (stock has no batch).
    """
    if not product.track_batch:
        return None
    batch_no = (batch_no or "").strip()
    label = f"{product.sku} - {product.name_en}"
    if not batch_no:
        raise BusinessRuleError(
            f"Batch number is required for {label}.",
            code="batch_required",
            errors=[{"field": f"{field_prefix}batch_no", "message": "Required"}],
        )
    if product.track_expiry:
        if expiry_date is None:
            raise BusinessRuleError(
                f"Expiry date is required for {label}.",
                code="expiry_required",
                errors=[{"field": f"{field_prefix}expiry_date", "message": "Required"}],
            )
        if not allow_expired and expiry_date <= received_on:
            raise BusinessRuleError(
                f"{label}: expiry date must be after the receipt date.",
                code="already_expired",
                errors=[{"field": f"{field_prefix}expiry_date", "message": "Already expired"}],
            )
    if mfg_date and expiry_date and mfg_date >= expiry_date:
        raise BusinessRuleError(
            f"{label}: manufacturing date must be before the expiry date.",
            errors=[{"field": f"{field_prefix}mfg_date", "message": "Must be before expiry"}],
        )

    batch = session.scalar(
        select(Batch).where(Batch.product_id == product.id, Batch.batch_no == batch_no)
    )
    if batch is None:
        batch = Batch(
            product_id=product.id,
            batch_no=batch_no,
            mfg_date=mfg_date,
            expiry_date=expiry_date if product.track_expiry else None,
        )
        session.add(batch)
        session.flush()
        return batch
    if product.track_expiry and expiry_date and batch.expiry_date != expiry_date:
        raise BusinessRuleError(
            f"Batch {batch_no} of {label} already exists with expiry "
            f"{batch.expiry_date.isoformat() if batch.expiry_date else 'none'}.",
            code="batch_expiry_mismatch",
            errors=[{"field": f"{field_prefix}expiry_date", "message": "Differs from batch"}],
        )
    return batch
