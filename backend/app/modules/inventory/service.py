from collections import defaultdict
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.modules.catalog.models import Product
from app.modules.inventory.models import (
    Batch,
    InventoryMovement,
    Location,
    ProductCost,
    StockBalance,
)
from app.modules.inventory.schemas import (
    BatchStockRow,
    MovementPage,
    MovementRead,
    StockRow,
    StockTotals,
    UnitInfo,
)
from app.shared.dates import business_date
from app.shared.money import ZERO
from app.shared.pagination import PageParams, paginate, paginate_rows
from app.shared.search import all_words, clean, like_pattern


def _units(product: Product) -> list[UnitInfo]:
    return [UnitInfo(uom_code=u.uom.code, factor_to_base=u.factor_to_base) for u in product.uoms]


def stock_summary(
    session: Session,
    params: PageParams,
    *,
    q: str | None,
    low_only: bool,
    in_stock_only: bool,
    category_id: int | None,
    show_cost: bool,
) -> tuple[list[StockRow], int]:
    """One row per active product: quantity per location, average cost and value."""
    stmt = (
        select(Product)
        .outerjoin(ProductCost, ProductCost.product_id == Product.id)
        .where(Product.is_active.is_(True))
        .order_by(Product.name_en)
    )
    if q:
        words = all_words(q, Product.sku, Product.name_en, Product.name_bn)
        stmt = stmt.where(or_(words, Product.barcode == clean(q)))
    if category_id is not None:
        stmt = stmt.where(Product.category_id == category_id)
    if in_stock_only:
        stmt = stmt.where(func.coalesce(ProductCost.qty_on_hand, 0) > 0)
    if low_only:
        stmt = stmt.where(
            Product.reorder_level > 0,
            func.coalesce(ProductCost.qty_on_hand, 0) <= Product.reorder_level,
        )
    products, total = paginate(session, stmt, params)
    ids = [p.id for p in products]

    costs = {
        c.product_id: c
        for c in session.scalars(select(ProductCost).where(ProductCost.product_id.in_(ids))).all()
    }
    by_location: dict[int, dict[str, Decimal]] = defaultdict(dict)
    for product_id, code, qty in session.execute(
        select(StockBalance.product_id, Location.code, func.sum(StockBalance.qty_base))
        .join(Location, Location.id == StockBalance.location_id)
        .where(StockBalance.product_id.in_(ids))
        .group_by(StockBalance.product_id, Location.code)
    ):
        by_location[product_id][code] = Decimal(qty)

    rows: list[StockRow] = []
    for p in products:
        cost = costs.get(p.id)
        qty_total = cost.qty_on_hand if cost else ZERO
        loc = by_location.get(p.id, {})
        rows.append(
            StockRow(
                product_id=p.id,
                sku=p.sku,
                name_en=p.name_en,
                name_bn=p.name_bn,
                base_uom_code=p.base_uom.code,
                uoms=_units(p),
                qty_sellable=loc.get("SELLABLE", ZERO),
                qty_damaged=loc.get("DAMAGED", ZERO),
                qty_expired=loc.get("EXPIRED", ZERO),
                qty_total=qty_total,
                avg_cost=(cost.avg_cost if cost else ZERO) if show_cost else None,
                value=(cost.total_value if cost else ZERO) if show_cost else None,
                reorder_level=p.reorder_level,
                is_low=p.reorder_level > 0 and qty_total <= p.reorder_level,
            )
        )
    return rows, total


def batch_stock(
    session: Session,
    params: PageParams,
    *,
    product_id: int | None,
    location_code: str | None,
    expiring_within_days: int | None,
    q: str | None,
) -> tuple[list[BatchStockRow], int]:
    """Stock by batch and location, earliest expiry first."""
    stmt = (
        select(StockBalance, Product)
        .join(Product, Product.id == StockBalance.product_id)
        .outerjoin(Batch, Batch.id == StockBalance.batch_id)
        .join(Location, Location.id == StockBalance.location_id)
        .where(StockBalance.qty_base > 0)
        .order_by(Batch.expiry_date.asc().nulls_last(), Product.name_en, StockBalance.id)
    )
    if product_id is not None:
        stmt = stmt.where(StockBalance.product_id == product_id)
    if location_code:
        stmt = stmt.where(Location.code == location_code)
    if expiring_within_days is not None:
        stmt = stmt.where(
            Batch.expiry_date.is_not(None),
            Batch.expiry_date <= business_date() + timedelta(days=expiring_within_days),
        )
    if q:
        like = like_pattern(q)
        stmt = stmt.where(
            or_(Product.sku.ilike(like), Product.name_en.ilike(like), Batch.batch_no.ilike(like))
        )
    pairs, total = paginate_rows(session, stmt, params)
    today = business_date()
    rows = [
        BatchStockRow(
            product_id=product.id,
            sku=product.sku,
            name_en=product.name_en,
            name_bn=product.name_bn,
            base_uom_code=product.base_uom.code,
            uoms=_units(product),
            batch_id=bal.batch_id,
            batch_no=bal.batch.batch_no if bal.batch else None,
            mfg_date=bal.batch.mfg_date if bal.batch else None,
            expiry_date=bal.batch.expiry_date if bal.batch else None,
            days_to_expiry=(
                (bal.batch.expiry_date - today).days
                if bal.batch and bal.batch.expiry_date
                else None
            ),
            location_code=bal.location.code,
            qty_base=bal.qty_base,
        )
        for bal, product in pairs
    ]
    return rows, total


def movements(
    session: Session, product_id: int, *, after: int | None, limit: int, show_cost: bool
) -> MovementPage:
    """Stock ledger of one product, newest first, keyset-paginated by movement id."""
    if session.get(Product, product_id) is None:
        raise NotFoundError("Product not found.")
    stmt = (
        select(InventoryMovement)
        .where(InventoryMovement.product_id == product_id)
        .order_by(InventoryMovement.id.desc())
        .limit(limit + 1)
    )
    if after is not None:
        stmt = stmt.where(InventoryMovement.id < after)
    rows: Sequence[InventoryMovement] = session.scalars(stmt).all()
    page, extra = rows[:limit], rows[limit:]
    return MovementPage(
        items=[
            MovementRead(
                id=m.id,
                business_date=m.business_date,
                movement_type=m.movement_type,
                source_doc_type=m.source_doc_type,
                source_doc_id=m.source_doc_id,
                source_doc_no=m.source_doc_no,
                batch_no=m.batch.batch_no if m.batch else None,
                location_code=m.location.code,
                qty_base=m.qty_base,
                unit_cost=m.unit_cost if show_cost else None,
                value=m.value if show_cost else None,
                reversal_of_id=m.reversal_of_id,
                created_at=m.created_at,
            )
            for m in page
        ],
        next_after=page[-1].id if extra else None,
    )


def stock_totals(session: Session, near_expiry_days: int, *, show_cost: bool) -> StockTotals:
    """Headline numbers for the dashboard."""
    total_value, in_stock = session.execute(
        select(
            func.coalesce(func.sum(ProductCost.total_value), 0),
            func.count().filter(ProductCost.qty_on_hand > 0),
        )
    ).one()
    low = (
        session.scalar(
            select(func.count())
            .select_from(Product)
            .outerjoin(ProductCost, ProductCost.product_id == Product.id)
            .where(
                Product.is_active.is_(True),
                Product.reorder_level > 0,
                func.coalesce(ProductCost.qty_on_hand, 0) <= Product.reorder_level,
            )
        )
        or 0
    )
    expiring = (
        session.scalar(
            select(func.count())
            .select_from(StockBalance)
            .join(Batch, Batch.id == StockBalance.batch_id)
            .join(Location, Location.id == StockBalance.location_id)
            .where(
                StockBalance.qty_base > 0,
                Location.is_sellable.is_(True),
                Batch.expiry_date.is_not(None),
                Batch.expiry_date <= business_date() + timedelta(days=near_expiry_days),
            )
        )
        or 0
    )
    return StockTotals(
        total_value=Decimal(total_value) if show_cost else None,
        products_in_stock=int(in_stock),
        low_stock_count=int(low),
        expiring_soon_count=int(expiring),
        near_expiry_days=near_expiry_days,
    )
