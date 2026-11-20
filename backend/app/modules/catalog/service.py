from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.catalog.models import Brand, Category, Product, ProductUom, Uom
from app.modules.catalog.schemas import (
    ProductCreate,
    ProductRead,
    ProductUomIn,
    ProductUomRead,
    ProductUpdate,
)
from app.shared.audit import record_audit, snapshot
from app.shared.pagination import PageParams, paginate

_ENTITY_NAMES = {Category: "category", Brand: "brand", Uom: "uom"}


# --- Simple master data (categories, brands, units) -------------------------
def list_simple[M: (Category, Brand, Uom)](
    session: Session, model: type[M], q: str | None, is_active: bool | None
) -> Sequence[M]:
    order_col = model.code if model is Uom else model.name_en  # type: ignore[attr-defined]
    stmt = select(model).order_by(order_col)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(model.name_en.ilike(like), model.name_bn.ilike(like)))
    if is_active is not None:
        stmt = stmt.where(model.is_active == is_active)
    return session.scalars(stmt).all()


def get_simple[M: (Category, Brand, Uom)](session: Session, model: type[M], entity_id: int) -> M:
    obj = session.get(model, entity_id)
    if obj is None:
        raise NotFoundError(f"{_ENTITY_NAMES[model].capitalize()} not found.")
    return obj


def _ensure_unique_name(
    session: Session, model: type[Category] | type[Brand], name: str, exclude_id: int | None = None
) -> None:
    stmt = select(model.id).where(func.lower(model.name_en) == name.lower())
    if exclude_id is not None:
        stmt = stmt.where(model.id != exclude_id)
    if session.scalar(stmt) is not None:
        raise ConflictError(
            "Name already exists.",
            code="duplicate",
            errors=[{"field": "name_en", "message": "Already exists"}],
        )


def create_simple[M: (Category, Brand, Uom)](
    session: Session, model: type[M], data: dict[str, Any], actor: CurrentUser
) -> M:
    if model is Uom:
        if session.scalar(select(Uom.id).where(Uom.code == data["code"])) is not None:
            raise ConflictError(
                "Unit code already exists.",
                code="duplicate",
                errors=[{"field": "code", "message": "Already exists"}],
            )
    else:
        _ensure_unique_name(session, model, data["name_en"])  # type: ignore[arg-type]
    if model is Category and data.get("parent_id"):
        get_simple(session, Category, data["parent_id"])
    obj = model(**data)
    session.add(obj)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="create",
        entity_type=_ENTITY_NAMES[model],
        entity_id=obj.id,
        after=snapshot(obj),
    )
    return obj


def update_simple[M: (Category, Brand, Uom)](
    session: Session, model: type[M], entity_id: int, changes: dict[str, Any], actor: CurrentUser
) -> M:
    obj = get_simple(session, model, entity_id)
    before = snapshot(obj)
    if model is not Uom and changes.get("name_en"):
        _ensure_unique_name(session, model, changes["name_en"], exclude_id=obj.id)  # type: ignore[arg-type]
    if model is Category and changes.get("parent_id"):
        if changes["parent_id"] == obj.id:
            raise BusinessRuleError("A category cannot be its own parent.")
        get_simple(session, Category, changes["parent_id"])
    for field, value in changes.items():
        setattr(obj, field, value)
    session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="update",
        entity_type=_ENTITY_NAMES[model],
        entity_id=obj.id,
        before=before,
        after=snapshot(obj),
    )
    return obj


# --- Products -----------------------------------------------------------------
def product_to_read(p: Product) -> ProductRead:
    return ProductRead(
        id=p.id,
        sku=p.sku,
        barcode=p.barcode,
        name_en=p.name_en,
        name_bn=p.name_bn,
        category_id=p.category_id,
        category_name=p.category.name_en if p.category else None,
        brand_id=p.brand_id,
        brand_name=p.brand.name_en if p.brand else None,
        base_uom_id=p.base_uom_id,
        base_uom_code=p.base_uom.code,
        dp=p.dp,
        tp=p.tp,
        mrp=p.mrp,
        reorder_level=p.reorder_level,
        track_batch=p.track_batch,
        track_expiry=p.track_expiry,
        is_active=p.is_active,
        uoms=[
            ProductUomRead(
                id=u.id,
                uom_id=u.uom_id,
                uom_code=u.uom.code,
                factor_to_base=u.factor_to_base,
                is_purchase_default=u.is_purchase_default,
                is_sales_default=u.is_sales_default,
                barcode=u.barcode,
            )
            for u in p.uoms
        ],
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _product_audit(p: Product) -> dict[str, Any]:
    return snapshot(
        p,
        extra={"uoms": [{"uom_id": u.uom_id, "factor_to_base": u.factor_to_base} for u in p.uoms]},
    )


def list_products(
    session: Session,
    params: PageParams,
    *,
    q: str | None,
    category_id: int | None,
    brand_id: int | None,
    is_active: bool | None,
) -> tuple[Sequence[Product], int]:
    stmt = select(Product).order_by(Product.name_en)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Product.sku.ilike(like),
                Product.name_en.ilike(like),
                Product.name_bn.ilike(like),
                Product.barcode == q,
            )
        )
    if category_id is not None:
        stmt = stmt.where(Product.category_id == category_id)
    if brand_id is not None:
        stmt = stmt.where(Product.brand_id == brand_id)
    if is_active is not None:
        stmt = stmt.where(Product.is_active == is_active)
    return paginate(session, stmt, params)


def get_product(session: Session, product_id: int) -> Product:
    product = session.get(Product, product_id)
    if product is None:
        raise NotFoundError("Product not found.")
    return product


def _validate_refs(
    session: Session,
    *,
    category_id: int | None,
    brand_id: int | None,
    base_uom_id: int | None,
    uoms: list[ProductUomIn] | None,
    effective_base_uom_id: int,
) -> None:
    if category_id is not None:
        get_simple(session, Category, category_id)
    if brand_id is not None:
        get_simple(session, Brand, brand_id)
    if base_uom_id is not None:
        get_simple(session, Uom, base_uom_id)
    for u in uoms or []:
        get_simple(session, Uom, u.uom_id)
        if u.uom_id == effective_base_uom_id:
            raise BusinessRuleError(
                "The base unit cannot also be an alternative unit.",
                errors=[{"field": "uoms", "message": "Remove the base unit from the unit list"}],
            )


def _ensure_unique_sku(session: Session, sku: str, exclude_id: int | None = None) -> None:
    stmt = select(Product.id).where(Product.sku == sku)
    if exclude_id is not None:
        stmt = stmt.where(Product.id != exclude_id)
    if session.scalar(stmt) is not None:
        raise ConflictError(
            "SKU already exists.",
            code="duplicate",
            errors=[{"field": "sku", "message": "Already exists"}],
        )


def create_product(session: Session, data: ProductCreate, actor: CurrentUser) -> Product:
    _ensure_unique_sku(session, data.sku)
    _validate_refs(
        session,
        category_id=data.category_id,
        brand_id=data.brand_id,
        base_uom_id=data.base_uom_id,
        uoms=data.uoms,
        effective_base_uom_id=data.base_uom_id,
    )
    product = Product(
        **data.model_dump(exclude={"uoms"}),
        created_by_id=actor.id,
        uoms=[ProductUom(**u.model_dump()) for u in data.uoms],
    )
    session.add(product)
    session.flush()
    session.refresh(product)
    record_audit(
        session,
        actor_id=actor.id,
        action="create",
        entity_type="product",
        entity_id=product.id,
        after=_product_audit(product),
    )
    return product


def update_product(
    session: Session, product_id: int, data: ProductUpdate, actor: CurrentUser
) -> Product:
    product = get_product(session, product_id)
    before = _product_audit(product)
    changes = data.model_dump(exclude_unset=True, exclude={"uoms"})

    for required in ("sku", "name_en", "base_uom_id", "dp", "tp", "mrp", "reorder_level"):
        if required in changes and changes[required] is None:
            raise BusinessRuleError(
                f"{required} cannot be empty.", errors=[{"field": required, "message": "Required"}]
            )
    if "sku" in changes:
        _ensure_unique_sku(session, changes["sku"], exclude_id=product.id)

    tp = changes.get("tp", product.tp)
    mrp = changes.get("mrp", product.mrp)
    if mrp > 0 and tp > mrp:
        raise BusinessRuleError(
            "Trade price (TP) cannot exceed MRP.",
            errors=[{"field": "tp", "message": "TP cannot exceed MRP"}],
        )

    _validate_refs(
        session,
        category_id=changes.get("category_id"),
        brand_id=changes.get("brand_id"),
        base_uom_id=changes.get("base_uom_id"),
        uoms=data.uoms if data.uoms is not None else None,
        effective_base_uom_id=changes.get("base_uom_id", product.base_uom_id),
    )
    for field, value in changes.items():
        setattr(product, field, value)

    if data.uoms is not None:
        existing = {u.uom_id: u for u in product.uoms}
        wanted = {u.uom_id: u for u in data.uoms}
        for uom_id, row in existing.items():
            if uom_id not in wanted:
                product.uoms.remove(row)
        for uom_id, incoming in wanted.items():
            if uom_id in existing:
                for field, value in incoming.model_dump().items():
                    setattr(existing[uom_id], field, value)
            else:
                product.uoms.append(ProductUom(**incoming.model_dump()))

    session.flush()
    session.refresh(product)
    record_audit(
        session,
        actor_id=actor.id,
        action="update",
        entity_type="product",
        entity_id=product.id,
        before=before,
        after=_product_audit(product),
    )
    return product
