"""Import every model so SQLAlchemy metadata (and Alembic autogenerate) sees all tables."""

from app.modules.accounting.models import (
    Account,
    JournalEntry,
    JournalLine,
    OpeningBalance,
    PaymentAccount,
)
from app.modules.catalog.models import Brand, Category, Product, ProductUom, Uom
from app.modules.claims.models import ClaimableItem
from app.modules.inventory.models import (
    Batch,
    InventoryMovement,
    Location,
    ProductCost,
    StockBalance,
)
from app.modules.parties.models import Customer, Supplier
from app.modules.purchasing.models import (
    GoodsReceipt,
    GoodsReceiptItem,
    PaymentPoAllocation,
    PurchaseOrder,
    PurchaseOrderItem,
    SupplierPayment,
)
from app.modules.sales.models import (
    CustomerReceipt,
    DailySalesSummary,
    ReceiptAllocation,
    SaleItemBatch,
    SalesInvoice,
    SalesInvoiceItem,
)
from app.modules.settings.models import AppSetting
from app.modules.users.models import Permission, RefreshToken, Role, User
from app.shared.audit import AuditLog
from app.shared.idempotency import IdempotencyKey
from app.shared.models import Base
from app.shared.sequences import DocumentSequence

__all__ = [
    "Account",
    "AppSetting",
    "AuditLog",
    "Base",
    "Batch",
    "Brand",
    "Category",
    "ClaimableItem",
    "Customer",
    "CustomerReceipt",
    "DailySalesSummary",
    "DocumentSequence",
    "GoodsReceipt",
    "GoodsReceiptItem",
    "IdempotencyKey",
    "InventoryMovement",
    "JournalEntry",
    "JournalLine",
    "Location",
    "OpeningBalance",
    "PaymentAccount",
    "PaymentPoAllocation",
    "Permission",
    "Product",
    "ProductCost",
    "ProductUom",
    "PurchaseOrder",
    "PurchaseOrderItem",
    "ReceiptAllocation",
    "RefreshToken",
    "Role",
    "SaleItemBatch",
    "SalesInvoice",
    "SalesInvoiceItem",
    "StockBalance",
    "Supplier",
    "SupplierPayment",
    "Uom",
    "User",
]
