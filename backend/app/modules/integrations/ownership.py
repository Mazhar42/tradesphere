"""One owner per domain. While a domain is run by another system, entering new documents for
it here would count things twice, so those writes are refused. Reversing and cancelling what was
already entered stays possible."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.modules.integrations.models import DomainOwnership

DOMAINS = ("catalog", "parties", "purchasing", "stock", "sales", "receivables", "accounting")
# A receipt of goods and a sale both move stock.
NEEDS = {"sales": ("stock",), "purchasing": ("stock",)}
# Catalog, parties and accounting are only labelled; nothing is refused for them.
DOCUMENTS = {
    "purchasing": ("/api/v1/purchase-orders", "/api/v1/goods-receipts", "/api/v1/purchase-returns"),
    "stock": ("/api/v1/stock-adjustments", "/api/v1/stock-transfers"),
    "sales": ("/api/v1/sales-invoices", "/api/v1/sales-returns"),
    "receivables": ("/api/v1/customer-receipts",),
}
WRITES = ("POST", "PUT", "PATCH")
UNDOING = ("/reverse", "/cancel", "/close-short")


def owners(session: Session) -> dict[str, str]:
    stored = dict(session.execute(select(DomainOwnership.domain, DomainOwnership.owner)).all())
    return {domain: stored.get(domain, "tradesphere") for domain in DOMAINS}


def check_ownership(session: Session, method: str, path: str) -> None:
    if method not in WRITES or path.endswith(UNDOING):
        return
    for domain, prefixes in DOCUMENTS.items():
        if path.startswith(prefixes):
            owner = session.scalar(
                select(DomainOwnership.owner).where(DomainOwnership.domain == domain)
            )
            if owner == "external":
                raise ConflictError(
                    f"{domain.capitalize()} is run in another system for now.",
                    code="domain_external",
                )
