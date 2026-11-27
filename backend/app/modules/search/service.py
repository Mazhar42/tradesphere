"""Global search and product discovery. Matching order: exact code or number, then prefix, then
all words; fuzzy only when nothing else matched (search-and-discovery.md §3). Each group is
searched only if the person may read it."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.core.permissions import CurrentUser
from app.modules.accounting.models import Account
from app.modules.catalog.models import Brand, Category, Product, ProductUom
from app.modules.imports.models import ImportedDocument
from app.modules.inventory.models import Batch
from app.modules.parties.models import Customer, Supplier
from app.modules.purchasing.models import (
    GoodsReceipt,
    PurchaseOrder,
    SupplierPayment,
    SupplierProduct,
)
from app.modules.sales.models import CustomerReceipt, SalesInvoice, SalesReturn
from app.shared.audit import record_audit
from app.shared.search import all_words, clean, escape, is_gtin, prefix_pattern

PER_GROUP = 5
FUZZY_THRESHOLD = 0.4


@dataclass(frozen=True)
class Hit:
    id: int
    title: str
    subtitle: str
    url: str


@dataclass(frozen=True)
class Group:
    name: str
    permission: str
    model: Any
    exact: tuple[Any, ...]  # codes and numbers
    words: tuple[Any, ...]  # names and free text
    show: Callable[[Any], tuple[str, str, str]]
    fuzzy: Any = None
    numbered: bool = False  # found by document number, which always has a digit


def _doc(path: str) -> Callable[[Any], tuple[str, str, str]]:
    return lambda row: (row.doc_no or "—", row.status, f"/{path}/{row.id}")


GROUPS: tuple[Group, ...] = (
    Group(
        "products",
        "catalog:read",
        Product,
        (Product.barcode, Product.sku),
        (Product.sku, Product.name_en, Product.name_bn),
        lambda p: (p.name_en, p.sku, "/products"),
        Product.name_en,
    ),
    Group(
        "batches",
        "catalog:read",
        Batch,
        (Batch.batch_no,),
        (Batch.batch_no,),
        lambda b: (b.batch_no, str(b.expiry_date or ""), "/stock"),
    ),
    Group(
        "categories",
        "catalog:read",
        Category,
        (),
        (Category.name_en, Category.name_bn),
        lambda c: (c.name_en, c.name_bn or "", "/catalog"),
    ),
    Group(
        "brands",
        "catalog:read",
        Brand,
        (),
        (Brand.name_en, Brand.name_bn),
        lambda c: (c.name_en, c.name_bn or "", "/catalog"),
    ),
    Group(
        "customers",
        "customers:read",
        Customer,
        (Customer.code, Customer.phone),
        (Customer.code, Customer.name, Customer.shop_name, Customer.phone),
        lambda c: (c.name, c.code, f"/customers/{c.id}"),
        Customer.name,
    ),
    Group(
        "suppliers",
        "suppliers:read",
        Supplier,
        (Supplier.code, Supplier.phone),
        (Supplier.code, Supplier.name, Supplier.phone),
        lambda s: (s.name, s.code, "/suppliers"),
        Supplier.name,
    ),
    Group(
        "purchase_orders",
        "purchasing:read",
        PurchaseOrder,
        (PurchaseOrder.doc_no,),
        (PurchaseOrder.doc_no,),
        _doc("purchases"),
        numbered=True,
    ),
    Group(
        "goods_receipts",
        "purchasing:read",
        GoodsReceipt,
        (GoodsReceipt.doc_no, GoodsReceipt.challan_no),
        (GoodsReceipt.doc_no, GoodsReceipt.challan_no),
        lambda g: (g.doc_no or "—", g.challan_no or "", f"/purchases/{g.po_id}"),
        numbered=True,
    ),
    Group(
        "invoices",
        "sales:read",
        SalesInvoice,
        (SalesInvoice.doc_no,),
        (SalesInvoice.doc_no,),
        _doc("sales"),
        numbered=True,
    ),
    Group(
        "sales_returns",
        "sales:read",
        SalesReturn,
        (SalesReturn.doc_no,),
        (SalesReturn.doc_no,),
        lambda r: (r.doc_no or "—", r.status, "/returns"),
        numbered=True,
    ),
    Group(
        "receipts",
        "sales:read",
        CustomerReceipt,
        (CustomerReceipt.doc_no,),
        (CustomerReceipt.doc_no, CustomerReceipt.reference),
        lambda r: (r.doc_no, r.reference or "", f"/customers/{r.customer_id}"),
        numbered=True,
    ),
    Group(
        "supplier_payments",
        "purchasing:pay",
        SupplierPayment,
        (SupplierPayment.doc_no,),
        (SupplierPayment.doc_no, SupplierPayment.reference),
        lambda p: (p.doc_no, p.reference or "", f"/suppliers/{p.supplier_id}"),
        numbered=True,
    ),
    Group(
        "accounts",
        "finance:read",
        Account,
        (Account.code,),
        (Account.code, Account.name_en, Account.name_bn),
        lambda a: (a.name_en, a.code, "/accounts"),
    ),
    Group(
        "history",
        "reports:read",
        ImportedDocument,
        (ImportedDocument.number,),
        (ImportedDocument.number, ImportedDocument.party),
        lambda d: (d.number or d.external_id, f"{d.doc_date} {d.party or ''}", "/imports"),
    ),
)


def _ranked(group: Group, query: str) -> Select[Any]:
    exact = [func.lower(c) == query.lower() for c in group.exact]
    starts = [c.ilike(prefix_pattern(query)) for c in (*group.exact, *group.words)]
    rank = (
        case((or_(*exact), 0), (or_(*starts), 1), else_=2)
        if exact
        else case((or_(*starts), 1), else_=2)
    )
    matched = or_(all_words(query, *group.words), *exact)
    active = getattr(group.model, "is_active", None)
    order = [rank, *([active.desc()] if active is not None else []), group.model.id.desc()]
    return select(group.model).where(matched).order_by(*order).limit(PER_GROUP)


def _fuzzy(session: Session, group: Group, query: str) -> list[Any]:
    similarity = func.word_similarity(query, group.fuzzy)
    return list(
        session.scalars(
            select(group.model)
            .where(similarity > FUZZY_THRESHOLD)
            .order_by(similarity.desc())
            .limit(PER_GROUP)
        )
    )


def search(session: Session, query: str, user: CurrentUser) -> list[tuple[str, list[Hit]]]:
    query = clean(query)
    digits = any(c.isdigit() for c in query)
    allowed = [g for g in GROUPS if user.has(g.permission) and (digits or not g.numbered)]
    found = {g.name: list(session.scalars(_ranked(g, query))) for g in allowed}
    if len(query) >= 3 and not any(found.values()):
        found = {g.name: _fuzzy(session, g, query) for g in allowed if g.fuzzy is not None}
    result = []
    for group in allowed:
        hits = []
        for row in found.get(group.name, []):
            title, subtitle, url = group.show(row)
            hits.append(Hit(row.id, title, subtitle, url))
        if hits:
            result.append((group.name, hits))
    return result


# --- Finding a product that is not there (§6) ----------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    source: str  # catalog | supplier_catalog
    confidence: str  # exact | likely | possible
    product_id: int | None
    supplier_product_id: int | None
    title: str
    detail: str


def discover(session: Session, query: str) -> list[Candidate]:
    """Where this might already be known. Nothing is created or linked here."""
    query = clean(query)
    pattern = escape(query.lower())
    found: dict[tuple[str, int], Candidate] = {}

    def add(candidate: Candidate) -> None:
        key = (candidate.source, candidate.product_id or candidate.supplier_product_id or 0)
        found.setdefault(key, candidate)

    unit_barcode = select(ProductUom.product_id).where(ProductUom.barcode == query)
    for product in session.scalars(
        select(Product)
        .where(
            or_(
                Product.barcode == query,
                Product.id.in_(unit_barcode),
                func.lower(Product.sku) == query.lower(),
            )
        )
        .limit(10)
    ):
        how = "barcode" if is_gtin(query) else "code"
        add(Candidate("catalog", "exact", product.id, None, product.name_en, f"{how} {query}"))
    for product in session.scalars(
        select(Product)
        .where(all_words(query, Product.name_en, Product.name_bn, Product.sku))
        .limit(10)
    ):
        add(Candidate("catalog", "possible", product.id, None, product.name_en, product.sku))
    rows = session.execute(
        select(SupplierProduct, Supplier.name)
        .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
        .where(
            or_(
                func.lower(SupplierProduct.supplier_sku).like(pattern),
                all_words(query, SupplierProduct.description, SupplierProduct.supplier_sku),
            )
        )
        .limit(15)
    ).all()
    for item, supplier in rows:
        exact = item.supplier_sku.lower() == query.lower()
        confidence = "exact" if exact and item.product_id else "likely" if exact else "possible"
        add(
            Candidate(
                "supplier_catalog",
                confidence,
                item.product_id,
                item.id,
                item.description,
                f"{supplier} · {item.supplier_sku} · {item.last_seen_at.date()}",
            )
        )
    order = {"exact": 0, "likely": 1, "possible": 2}
    return sorted(found.values(), key=lambda c: (order[c.confidence], c.source != "catalog"))


def link(
    session: Session, supplier_product_id: int, product_id: int, actor: CurrentUser
) -> SupplierProduct:
    item = session.scalar(
        select(SupplierProduct).where(SupplierProduct.id == supplier_product_id).with_for_update()
    )
    product = session.scalar(select(Product).where(Product.id == product_id))
    if item is None or product is None:
        raise NotFoundError("Supplier line or product not found.")
    if item.product_id not in (None, product.id):
        raise ConflictError(
            "This line is already linked to another product.", code="already_linked"
        )
    item.product_id = product.id
    record_audit(
        session,
        actor_id=actor.id,
        action="link",
        entity_type="supplier_product",
        entity_id=item.id,
        after={"product_id": product.id, "supplier_sku": item.supplier_sku},
    )
    return item
