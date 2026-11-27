"""Import every model so SQLAlchemy metadata (and Alembic autogenerate) sees all tables."""

from app.modules.accounting.models import (
    Account,
    JournalEntry,
    JournalLine,
    OpeningBalance,
    PaymentAccount,
)
from app.modules.catalog.models import Brand, Category, Product, ProductUom, Uom
from app.modules.claims.models import (
    ClaimableItem,
    ClaimSettlement,
    ClaimSettlementLine,
    CommissionPayment,
    CompanyClaim,
    CompanyClaimLine,
)
from app.modules.finance.models import Expense, FundTransfer, OwnerTransaction
from app.modules.inventory.models import (
    Batch,
    InventoryMovement,
    Location,
    ProductCost,
    StockAdjustment,
    StockAdjustmentItem,
    StockBalance,
    StockTransfer,
    StockTransferItem,
)
from app.modules.parties.models import Customer, Supplier
from app.modules.platform.models import PlatformAuditLog, PlatformOperator, SupportGrant
from app.modules.purchasing.models import (
    GoodsReceipt,
    GoodsReceiptItem,
    PaymentPoAllocation,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReturn,
    PurchaseReturnItem,
    SupplierPayment,
)
from app.modules.sales.models import (
    CustomerReceipt,
    DailySalesSummary,
    ReceiptAllocation,
    SaleItemBatch,
    SalesInvoice,
    SalesInvoiceItem,
    SalesReturn,
    SalesReturnItem,
)
from app.modules.settings.models import AppSetting
from app.modules.tenancy.models import AuthEvent, Tenant, TenantInvitation, TenantMembership
from app.modules.users.models import (
    MfaFactor,
    MfaRecoveryCode,
    Permission,
    RefreshToken,
    Role,
    User,
    UserToken,
)
from app.shared.audit import AuditLog
from app.shared.idempotency import IdempotencyKey
from app.shared.models import Base
from app.shared.sequences import DocumentSequence

__all__ = [
    "Account",
    "AppSetting",
    "AuditLog",
    "AuthEvent",
    "Base",
    "Batch",
    "Brand",
    "Category",
    "ClaimSettlement",
    "ClaimSettlementLine",
    "ClaimableItem",
    "CommissionPayment",
    "CompanyClaim",
    "CompanyClaimLine",
    "Customer",
    "CustomerReceipt",
    "DailySalesSummary",
    "DocumentSequence",
    "Expense",
    "FundTransfer",
    "GoodsReceipt",
    "GoodsReceiptItem",
    "IdempotencyKey",
    "InventoryMovement",
    "JournalEntry",
    "JournalLine",
    "Location",
    "MfaFactor",
    "MfaRecoveryCode",
    "OpeningBalance",
    "OwnerTransaction",
    "PaymentAccount",
    "PaymentPoAllocation",
    "Permission",
    "PlatformAuditLog",
    "PlatformOperator",
    "Product",
    "ProductCost",
    "ProductUom",
    "PurchaseOrder",
    "PurchaseOrderItem",
    "PurchaseReturn",
    "PurchaseReturnItem",
    "ReceiptAllocation",
    "RefreshToken",
    "Role",
    "SaleItemBatch",
    "SalesInvoice",
    "SalesInvoiceItem",
    "SalesReturn",
    "SalesReturnItem",
    "StockAdjustment",
    "StockAdjustmentItem",
    "StockBalance",
    "StockTransfer",
    "StockTransferItem",
    "Supplier",
    "SupplierPayment",
    "SupportGrant",
    "Tenant",
    "TenantInvitation",
    "TenantMembership",
    "Uom",
    "User",
    "UserToken",
]
