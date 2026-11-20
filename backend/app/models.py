"""Import every model so SQLAlchemy metadata (and Alembic autogenerate) sees all tables."""

from app.modules.accounting.models import Account
from app.modules.catalog.models import Brand, Category, Product, ProductUom, Uom
from app.modules.inventory.models import Location
from app.modules.parties.models import Customer, Supplier
from app.modules.settings.models import AppSetting
from app.modules.users.models import Permission, RefreshToken, Role, User
from app.shared.audit import AuditLog
from app.shared.models import Base
from app.shared.sequences import DocumentSequence

__all__ = [
    "Account",
    "AppSetting",
    "AuditLog",
    "Base",
    "Brand",
    "Category",
    "Customer",
    "DocumentSequence",
    "Location",
    "Permission",
    "Product",
    "ProductUom",
    "RefreshToken",
    "Role",
    "Supplier",
    "Uom",
    "User",
]
