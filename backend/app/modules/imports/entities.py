"""What can be imported. For each: its columns, how a row is checked and matched, and how it
is applied through the same service functions the screens use."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.permissions import CurrentUser
from app.modules.accounting.models import PaymentAccount
from app.modules.catalog import service as catalog
from app.modules.catalog.models import Brand, Category, Product, ProductUom, Uom
from app.modules.catalog.schemas import (
    ProductCreate,
    ProductUomIn,
    ProductUpdate,
    UomCreate,
)
from app.modules.imports import fields
from app.modules.imports.models import ExternalRef, ImportedDocument
from app.modules.imports.openings import Item
from app.modules.parties import service as parties
from app.modules.parties.models import Customer, Supplier
from app.modules.parties.schemas import (
    CustomerCreate,
    CustomerUpdate,
    SupplierCreate,
    SupplierUpdate,
)
from app.modules.purchasing import service as purchasing
from app.modules.purchasing.schemas import POCreate, POLineIn

Row = dict[str, str]


@dataclass(frozen=True)
class Column:
    name: str
    bangla: str
    required: bool = False
    aliases: tuple[str, ...] = ()


@dataclass
class Checked:
    """The outcome of looking at one row."""

    values: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    target_id: int | None = None
    item: Item | None = None  # opening loads


@dataclass(frozen=True)
class Context:
    session: Session
    options: dict[str, Any]
    source: str

    def external(self, entity: str, external_id: str) -> int | None:
        if not external_id:
            return None
        return self.session.scalar(
            select(ExternalRef.internal_id).where(
                ExternalRef.source_system == self.source,
                ExternalRef.entity == entity,
                ExternalRef.external_id == external_id,
            )
        )


@dataclass(frozen=True)
class Entity:
    name: str
    columns: tuple[Column, ...]
    check: Callable[[Context, Row], Checked]
    key: Callable[[Row], str]  # to find duplicates inside one file
    create: Callable[[Context, Checked, CurrentUser], int] | None = None
    update: Callable[[Context, Checked, CurrentUser], None] | None = None
    opening_domain: str | None = None
    grouped: bool = False  # several rows make one record (purchase orders)


def _need(checked: Checked, row: Row, *names: str) -> None:
    for name in names:
        if not row.get(name):
            checked.errors.append(f"{name}: is required")


def _parse(checked: Checked, row: Row, name: str, reader: Callable[[str], Any]) -> None:
    if row.get(name):
        try:
            checked.values[name] = reader(row[name])
        except fields.Invalid as exc:
            checked.errors.append(f"{name}: {exc}")


def _copy(checked: Checked, row: Row, *names: str, limit: int = 200) -> None:
    for name in names:
        if row.get(name):
            if len(row[name]) > limit:
                checked.errors.append(f"{name}: is longer than {limit} characters")
            checked.values[name] = row[name]


def _by_name(session: Session, model: type[Brand] | type[Category], name: str) -> int | None:
    return session.scalar(select(model.id).where(func.lower(model.name_en) == name.lower()))


def _find(session: Session, model: Any, column: Any, value: str) -> int | None:
    if not value:
        return None
    return session.scalar(select(model.id).where(func.lower(column) == value.lower()))


# --- Units, categories, brands -----------------------------------------------------------------


def _check_unit(context: Context, row: Row) -> Checked:
    checked = Checked()
    _need(checked, row, "code", "name_en")
    _copy(checked, row, "name_en", "name_bn", limit=50)
    value = row.get("code", "").upper()
    if value and (not value.isalnum() or len(value) > 10):
        checked.errors.append("code: use up to 10 letters or digits")
    checked.values["code"] = value
    checked.target_id = context.external("units", row.get("external_id", "")) or _find(
        context.session, Uom, Uom.code, value
    )
    return checked


def _create_unit(context: Context, checked: Checked, actor: CurrentUser) -> int:
    data = UomCreate(**{k: checked.values.get(k) for k in ("code", "name_en", "name_bn")})
    return catalog.create_simple(context.session, Uom, data.model_dump(), actor).id


def _named(model: Any, entity: str) -> Entity:
    def check(context: Context, row: Row) -> Checked:
        checked = Checked()
        _need(checked, row, "name_en")
        _copy(checked, row, "name_en", "name_bn", limit=100)
        checked.target_id = context.external(entity, row.get("external_id", "")) or _by_name(
            context.session, model, row.get("name_en", "")
        )
        return checked

    def create(context: Context, checked: Checked, actor: CurrentUser) -> int:
        data = {k: checked.values.get(k) for k in ("name_en", "name_bn")}
        return int(catalog.create_simple(context.session, model, data, actor).id)

    def update(context: Context, checked: Checked, actor: CurrentUser) -> None:
        assert checked.target_id is not None
        catalog.update_simple(context.session, model, checked.target_id, checked.values, actor)

    return Entity(
        entity,
        (
            Column("external_id", "বাহ্যিক আইডি"),
            Column("name_en", "নাম (ইংরেজি)", True, ("name",)),
            Column("name_bn", "নাম (বাংলা)"),
        ),
        check,
        lambda row: row.get("name_en", "").lower(),
        create,
        update,
    )


# --- Suppliers and customers ---------------------------------------------------------------------


def _check_party(model: Any, entity: str, texts: tuple[str, ...]) -> Callable[..., Checked]:
    def check(context: Context, row: Row) -> Checked:
        checked = Checked()
        _need(checked, row, "name")
        _copy(checked, row, "name", *texts)
        _parse(checked, row, "code", fields.code)
        if model is Customer:
            _parse(checked, row, "credit_limit", lambda v: fields.number(v, places=2))
        checked.target_id = context.external(entity, row.get("external_id", "")) or _find(
            context.session, model, model.code, checked.values.get("code", "")
        )
        return checked

    return check


def _party_entity(
    model: Any, entity: str, create_schema: Any, update_schema: Any, extra: tuple[Column, ...]
) -> Entity:
    texts = tuple(c.name for c in extra if c.name != "credit_limit")

    def create(context: Context, checked: Checked, actor: CurrentUser) -> int:
        data = create_schema(**checked.values)
        return int(parties.create_party(context.session, model, data, actor).id)

    def update(context: Context, checked: Checked, actor: CurrentUser) -> None:
        assert checked.target_id is not None
        changes = {k: v for k, v in checked.values.items() if k != "code"}
        if not context.options.get("update_credit_limit"):
            changes.pop("credit_limit", None)
        parties.update_party(
            context.session, model, checked.target_id, update_schema(**changes), actor
        )

    return Entity(
        entity,
        (
            Column("external_id", "বাহ্যিক আইডি"),
            Column("code", "কোড"),
            Column("name", "নাম", True),
            *extra,
        ),
        _check_party(model, entity, texts),
        lambda row: row.get("code", "").lower() or f"{row.get('name', '')}|{row.get('phone', '')}",
        create,
        update,
    )


# --- Products ------------------------------------------------------------------------------------

PRICES = ("dp", "tp", "mrp")


def _check_product(context: Context, row: Row) -> Checked:
    checked = Checked()
    session = context.session
    _need(checked, row, "sku", "name_en", "unit")
    _copy(checked, row, "name_en", "name_bn")
    _parse(checked, row, "sku", fields.code)
    _parse(checked, row, "barcode", fields.code)
    for name in PRICES:
        _parse(checked, row, name, lambda v: fields.number(v, places=2))
    _parse(checked, row, "reorder_level", lambda v: fields.number(v, places=3))
    _parse(checked, row, "track_batch", fields.yes_no)
    _parse(checked, row, "track_expiry", fields.yes_no)
    sku = checked.values.get("sku", "")
    checked.target_id = context.external("products", row.get("external_id", "")) or _find(
        session, Product, Product.sku, sku
    )
    # Missing references block the row; nothing is created behind the person's back.
    for column, model, key, attribute in (
        ("unit", Uom, "base_uom_id", Uom.code),
        ("category", Category, "category_id", Category.name_en),
        ("brand", Brand, "brand_id", Brand.name_en),
    ):
        if row.get(column):
            found = _find(session, model, attribute, row[column])
            if found is None:
                checked.errors.append(f"{column}: '{row[column]}' does not exist; import it first")
            checked.values[key] = found
    barcode = checked.values.get("barcode")
    if barcode:
        other = session.scalar(select(Product.id).where(Product.barcode == barcode))
        if other is not None and other != checked.target_id:
            checked.errors.append("barcode: another product already has it")
    if row.get("carton_size"):
        _parse(checked, row, "carton_size", lambda v: fields.number(v, places=3, positive=True))
        carton = _find(session, Uom, Uom.code, "CTN")
        if carton is None or checked.values.get("carton_size", Decimal(2)) <= 1:
            checked.errors.append("carton_size: needs the unit CTN and a size above 1")
        checked.values["carton_uom"] = carton
    return checked


def _create_product(context: Context, checked: Checked, actor: CurrentUser) -> int:
    values = dict(checked.values)
    size, carton = values.pop("carton_size", None), values.pop("carton_uom", None)
    uoms = [ProductUomIn(uom_id=carton, factor_to_base=size)] if size else []
    data = ProductCreate(**values, uoms=uoms)
    return catalog.create_product(context.session, data, actor).id


def _update_product(context: Context, checked: Checked, actor: CurrentUser) -> None:
    assert checked.target_id is not None
    allowed = ["name_en", "name_bn", "category_id", "brand_id", "reorder_level", "barcode"]
    if context.options.get("update_prices"):
        allowed += PRICES
    changes = {k: v for k, v in checked.values.items() if k in allowed}
    catalog.update_product(context.session, checked.target_id, ProductUpdate(**changes), actor)


def _check_product_unit(context: Context, row: Row) -> Checked:
    checked = Checked()
    session = context.session
    _need(checked, row, "sku", "unit", "factor")
    _parse(checked, row, "factor", lambda v: fields.number(v, places=3, positive=True))
    _parse(checked, row, "barcode", fields.code)
    product_id = _find(session, Product, Product.sku, row.get("sku", ""))
    uom_id = _find(session, Uom, Uom.code, row.get("unit", ""))
    if product_id is None:
        checked.errors.append("sku: no such product")
    if uom_id is None:
        checked.errors.append("unit: no such unit")
    if checked.values.get("factor", Decimal(2)) <= 1:
        checked.errors.append("factor: must be above 1")
    checked.values.update(product_id=product_id, uom_id=uom_id)
    if product_id and uom_id:
        checked.target_id = session.scalar(
            select(ProductUom.id).where(
                ProductUom.product_id == product_id, ProductUom.uom_id == uom_id
            )
        )
    return checked


def _create_product_unit(context: Context, checked: Checked, actor: CurrentUser) -> int:
    product = catalog.get_product(context.session, checked.values["product_id"])
    kept = [
        ProductUomIn(
            uom_id=u.uom_id,
            factor_to_base=u.factor_to_base,
            is_purchase_default=u.is_purchase_default,
            is_sales_default=u.is_sales_default,
            barcode=u.barcode,
        )
        for u in product.uoms
    ]
    added = ProductUomIn(
        uom_id=checked.values["uom_id"],
        factor_to_base=checked.values["factor"],
        barcode=checked.values.get("barcode"),
    )
    catalog.update_product(context.session, product.id, ProductUpdate(uoms=[*kept, added]), actor)
    return int(
        context.session.scalar(
            select(ProductUom.id).where(
                ProductUom.product_id == product.id,
                ProductUom.uom_id == checked.values["uom_id"],
            )
        )
        or 0
    )


# --- Opening positions ---------------------------------------------------------------------------


def _check_stock(context: Context, row: Row) -> Checked:
    checked = Checked()
    session = context.session
    fmt = context.options.get("date_format", "YYYY-MM-DD")
    _need(checked, row, "sku", "qty", "unit_cost")
    _parse(checked, row, "qty", lambda v: fields.number(v, places=3, positive=True))
    _parse(checked, row, "unit_cost", lambda v: fields.number(v, places=4))
    _parse(checked, row, "mfg_date", lambda v: fields.day(v, fmt))
    _parse(checked, row, "expiry_date", lambda v: fields.day(v, fmt))
    location = row.get("location", "").upper() or "SELLABLE"
    if location not in ("SELLABLE", "DAMAGED", "EXPIRED"):
        checked.errors.append("location: SELLABLE, DAMAGED or EXPIRED")
    product = session.scalar(
        select(Product).where(func.lower(Product.sku) == row.get("sku", "").lower())
    )
    if product is None:
        checked.errors.append("sku: no such product")
        return checked
    batch = row.get("batch_no") or None
    if product.track_batch and not batch:
        checked.errors.append("batch_no: this product tracks batches")
    if product.track_expiry and not row.get("expiry_date"):
        checked.errors.append("expiry_date: this product tracks expiry")
    if not checked.errors:
        qty, cost = checked.values["qty"], checked.values["unit_cost"]
        checked.item = Item(
            key=f"{product.sku}|{batch or ''}|{location}",
            product_id=product.id,
            location=location,
            batch_no=batch,
            mfg_date=checked.values.get("mfg_date"),
            expiry_date=checked.values.get("expiry_date"),
            qty=qty,
            amount=qty * cost,
        )
    return checked


def _check_balance(domain: str) -> Callable[[Context, Row], Checked]:
    def check(context: Context, row: Row) -> Checked:
        checked = Checked()
        session = context.session
        _parse(checked, row, "amount", lambda v: fields.number(v, places=2, positive=True))
        _need(checked, row, "amount")
        reference = row.get("reference", "")
        party_id = account_id = None
        amount = checked.values.get("amount")
        if domain == "cash":
            _need(checked, row, "account")
            account_id = _find(session, PaymentAccount, PaymentAccount.name, row.get("account", ""))
            if account_id is None:
                checked.errors.append("account: no such payment account")
            key = row.get("account", "").lower()
        else:
            model, column = (
                (Customer, "customer_code")
                if domain == "receivables"
                else (Supplier, "supplier_code")
            )
            _need(checked, row, column)
            party_id = _find(session, model, model.code, row.get(column, ""))
            if party_id is None:
                checked.errors.append(f"{column}: no {model.__name__.lower()} has this code")
            key = f"{row.get(column, '').upper()}|{reference}"
        if domain == "payables" and amount is not None:
            direction = row.get("direction", "").lower() or "payable"
            if direction not in ("payable", "advance"):
                checked.errors.append("direction: payable or advance")
            if direction == "advance":
                amount = -amount
        if not checked.errors:
            assert amount is not None  # "amount" is required above
            checked.item = Item(
                key=key, amount=amount, party_id=party_id, payment_account_id=account_id
            )
        return checked

    return check


def _opening(name: str, domain: str, columns: tuple[Column, ...]) -> Entity:
    check = _check_stock if domain == "stock" else _check_balance(domain)

    def key(row: Row) -> str:
        return "|".join(row.get(c.name, "").lower() for c in columns if c.name != "amount")

    return Entity(name, columns, check, key, opening_domain=domain)


AMOUNT = Column("amount", "পরিমাণ (টাকা)", True)
REFERENCE = Column("reference", "রেফারেন্স")

# --- Outstanding purchase orders ------------------------------------------------------------------


def _check_order(context: Context, row: Row) -> Checked:
    checked = Checked()
    session = context.session
    _need(checked, row, "external_id", "supplier_code", "sku", "qty", "unit_price")
    fmt = context.options.get("date_format", "YYYY-MM-DD")
    _parse(checked, row, "qty", lambda v: fields.number(v, places=3, positive=True))
    _parse(checked, row, "unit_price", lambda v: fields.number(v, places=4))
    _parse(checked, row, "date", lambda v: fields.day(v, fmt))
    supplier_id = _find(session, Supplier, Supplier.code, row.get("supplier_code", ""))
    product_id = _find(session, Product, Product.sku, row.get("sku", ""))
    if supplier_id is None:
        checked.errors.append("supplier_code: no supplier has this code")
    if product_id is None:
        checked.errors.append("sku: no such product")
    checked.values.update(
        supplier_id=supplier_id, product_id=product_id, external_id=row.get("external_id", "")
    )
    checked.target_id = context.external("purchase_orders", row.get("external_id", ""))
    return checked


def create_order(context: Context, lines: list[Checked], actor: CurrentUser) -> int:
    """One order from its rows, with only what is still to be received. No past receipt."""
    first = lines[0].values
    if len({line.values["supplier_id"] for line in lines}) > 1:
        raise fields.Invalid("rows of one order name different suppliers")
    data = POCreate(
        supplier_id=first["supplier_id"],
        business_date=first.get("date"),
        notes=f"Imported order {first['external_id']} from {context.source}",
        items=[
            POLineIn(
                product_id=line.values["product_id"],
                qty=line.values["qty"],
                unit_price=line.values["unit_price"],
            )
            for line in lines
        ],
    )
    order = purchasing.create_po(context.session, data, actor)
    purchasing.confirm_po(context.session, order.id, actor)
    return order.id


# --- History --------------------------------------------------------------------------------------


def _check_history(context: Context, row: Row) -> Checked:
    checked = Checked()
    fmt = context.options.get("date_format", "YYYY-MM-DD")
    _need(checked, row, "external_id", "type", "date", "amount")
    _parse(checked, row, "date", lambda v: fields.day(v, fmt))
    _parse(checked, row, "amount", lambda v: fields.number(v, places=2))
    _copy(checked, row, "number", "party", "note", "external_id", limit=150)
    kind = row.get("type", "").lower()
    if kind not in ("sale", "purchase", "receipt", "payment", "other"):
        checked.errors.append("type: sale, purchase, receipt, payment or other")
    checked.values["type"] = kind
    cutover: date | None = context.options.get("tenant_cutover")
    if cutover and checked.values.get("date") and checked.values["date"] >= cutover:
        checked.errors.append(f"date: history must be before the cutover date {cutover}")
    checked.target_id = context.session.scalar(
        select(ImportedDocument.id).where(
            ImportedDocument.source_system == context.source,
            ImportedDocument.external_id == row.get("external_id", ""),
        )
    )
    return checked


def _create_history(context: Context, checked: Checked, _: CurrentUser) -> int:
    v = checked.values
    document = ImportedDocument(
        source_system=context.source,
        external_id=v["external_id"],
        doc_type=v["type"],
        doc_date=v["date"],
        number=v.get("number"),
        party=v.get("party"),
        amount=v["amount"],
        note=v.get("note"),
    )
    context.session.add(document)
    context.session.flush()
    return document.id


EXTERNAL = Column("external_id", "বাহ্যিক আইডি")

ENTITIES: dict[str, Entity] = {
    e.name: e
    for e in (
        Entity(
            "units",
            (
                EXTERNAL,
                Column("code", "কোড", True),
                Column("name_en", "নাম (ইংরেজি)", True, ("name",)),
                Column("name_bn", "নাম (বাংলা)"),
            ),
            _check_unit,
            lambda row: row.get("code", "").lower(),
            _create_unit,
        ),
        _named(Category, "categories"),
        _named(Brand, "brands"),
        _party_entity(
            Supplier,
            "suppliers",
            SupplierCreate,
            SupplierUpdate,
            (
                Column("contact_person", "যোগাযোগের ব্যক্তি"),
                Column("phone", "ফোন"),
                Column("email", "ইমেইল"),
                Column("address", "ঠিকানা"),
            ),
        ),
        _party_entity(
            Customer,
            "customers",
            CustomerCreate,
            CustomerUpdate,
            (
                Column("shop_name", "দোকানের নাম"),
                Column("phone", "ফোন"),
                Column("address", "ঠিকানা"),
                Column("area", "এলাকা"),
                Column("credit_limit", "বাকির সীমা"),
            ),
        ),
        Entity(
            "products",
            (
                EXTERNAL,
                Column("sku", "এসকেইউ", True, ("product code", "item code")),
                Column("barcode", "বারকোড"),
                Column("name_en", "নাম (ইংরেজি)", True, ("name", "product name")),
                Column("name_bn", "নাম (বাংলা)"),
                Column("unit", "একক", True, ("uom",)),
                Column("category", "শ্রেণি"),
                Column("brand", "ব্র্যান্ড"),
                Column("dp", "ডিপি"),
                Column("tp", "টিপি", aliases=("trade price",)),
                Column("mrp", "এমআরপি"),
                Column("reorder_level", "রিঅর্ডার মাত্রা"),
                Column("track_batch", "ব্যাচ ট্র্যাক"),
                Column("track_expiry", "মেয়াদ ট্র্যাক"),
                Column("carton_size", "কার্টনে সংখ্যা"),
            ),
            _check_product,
            lambda row: row.get("sku", "").lower(),
            _create_product,
            _update_product,
        ),
        Entity(
            "product_units",
            (
                Column("sku", "এসকেইউ", True),
                Column("unit", "একক", True),
                Column("factor", "মূল এককে সংখ্যা", True),
                Column("barcode", "বারকোড"),
            ),
            _check_product_unit,
            lambda row: f"{row.get('sku', '')}|{row.get('unit', '')}".lower(),
            _create_product_unit,
        ),
        _opening(
            "opening_stock",
            "stock",
            (
                Column("sku", "এসকেইউ", True),
                Column("location", "অবস্থান"),
                Column("batch_no", "ব্যাচ নম্বর", aliases=("batch",)),
                Column("mfg_date", "উৎপাদনের তারিখ"),
                Column("expiry_date", "মেয়াদের তারিখ", aliases=("expiry",)),
                Column("qty", "পরিমাণ", True, ("quantity",)),
                Column("unit_cost", "একক ক্রয়মূল্য", True, ("cost",)),
            ),
        ),
        _opening(
            "opening_receivables",
            "receivables",
            (Column("customer_code", "গ্রাহক কোড", True), AMOUNT, REFERENCE),
        ),
        _opening(
            "opening_payables",
            "payables",
            (
                Column("supplier_code", "কোম্পানি কোড", True),
                Column("direction", "ধরন (payable/advance)"),
                AMOUNT,
                REFERENCE,
            ),
        ),
        _opening(
            "opening_claims",
            "claims",
            (Column("supplier_code", "কোম্পানি কোড", True), AMOUNT, REFERENCE),
        ),
        _opening("opening_cash", "cash", (Column("account", "হিসাবের নাম", True), AMOUNT)),
        Entity(
            "purchase_orders",
            (
                Column("external_id", "অর্ডার নম্বর", True, ("order no",)),
                Column("supplier_code", "কোম্পানি কোড", True),
                Column("date", "তারিখ"),
                Column("sku", "এসকেইউ", True),
                Column("qty", "বাকি পরিমাণ", True, ("remaining",)),
                Column("unit_price", "একক দর", True),
            ),
            _check_order,
            lambda row: f"{row.get('external_id', '')}|{row.get('sku', '')}".lower(),
            grouped=True,
        ),
        Entity(
            "history",
            (
                Column("external_id", "বাহ্যিক আইডি", True),
                Column("type", "ধরন", True),
                Column("date", "তারিখ", True),
                Column("number", "নম্বর"),
                Column("party", "পক্ষ"),
                AMOUNT,
                Column("note", "মন্তব্য"),
            ),
            _check_history,
            lambda row: row.get("external_id", "").lower(),
            _create_history,
        ),
    )
}


def auto_mapping(entity: Entity, headers: list[str]) -> dict[str, str]:
    """Match headings by our name, its Bangla label or a common alias."""
    by_lower = {h.strip().lower().replace(" ", "_"): h for h in headers}
    mapping = {}
    for column in entity.columns:
        for candidate in (column.name, column.bangla, *column.aliases):
            found = by_lower.get(candidate.lower().replace(" ", "_"))
            if found:
                mapping[column.name] = found
                break
    return mapping
