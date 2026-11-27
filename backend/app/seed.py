"""Reference data. Idempotent: existing system rows are updated, nothing is deleted.

- `run_seed` (every deploy): the permission catalog, then each dealership's system rows.
- `provision_tenant`: a new dealership with its roles, units, locations, chart of accounts,
  payment accounts, walk-in customer and settings.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.tenant_context import set_context, tenant_id_of
from app.modules.accounting.models import Account, PaymentAccount
from app.modules.catalog.models import Uom
from app.modules.inventory.models import Location
from app.modules.parties.models import Customer
from app.modules.settings.models import AppSetting
from app.modules.settings.schemas import BusinessProfile, PeriodLock, Preferences
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.service import all_tenants, create_tenant
from app.modules.users.models import Permission, Role
from app.modules.users.permissions_catalog import PERMISSIONS, ROLES
from app.shared.business import BusinessConfig

UOMS = [
    ("PCS", "Piece", "পিস"),
    ("PKT", "Packet", "প্যাকেট"),
    ("BOX", "Box", "বক্স"),
    ("CTN", "Carton", "কার্টন"),
    ("DZN", "Dozen", "ডজন"),
    ("BAG", "Bag", "ব্যাগ"),
    ("KG", "Kilogram", "কেজি"),
    ("LTR", "Litre", "লিটার"),
]

LOCATIONS = [
    ("SELLABLE", "Sellable", "বিক্রয়যোগ্য", True),
    ("DAMAGED", "Damaged", "ক্ষতিগ্রস্ত", False),
    ("EXPIRED", "Expired", "মেয়াদোত্তীর্ণ", False),
]

# code, name_en, name_bn, type, parent_code, is_postable, party_type
ACCOUNTS: list[tuple[str, str, str, str, str | None, bool, str | None]] = [
    ("1000", "Cash in Hand", "হাতে নগদ", "asset", None, True, None),
    ("1010", "Bank Accounts", "ব্যাংক হিসাব", "asset", None, True, None),
    ("1020", "bKash", "বিকাশ", "asset", None, True, None),
    ("1030", "Nagad", "নগদ (মোবাইল)", "asset", None, True, None),
    ("1100", "Accounts Receivable - Customers", "গ্রাহকের কাছে পাওনা", "asset", None, True, "customer"),
    ("1200", "Inventory", "মজুদ পণ্য", "asset", None, True, None),
    ("1300", "Company Claims Receivable", "কোম্পানির কাছে দাবি", "asset", None, True, "supplier"),
    ("2000", "Accounts Payable - Supplier", "কোম্পানির হিসাব", "liability", None, True, "supplier"),
    ("3000", "Owner's Capital", "মালিকের মূলধন", "equity", None, True, None),
    ("3100", "Owner's Drawings", "মালিকের উত্তোলন", "equity", None, True, None),
    ("3200", "Retained Earnings", "সংরক্ষিত মুনাফা", "equity", None, True, None),
    ("3900", "Opening Balance Equity", "প্রারম্ভিক জের", "equity", None, True, None),
    ("4000", "Sales Revenue", "বিক্রয়", "income", None, True, None),
    ("4100", "Sales Returns", "বিক্রয় ফেরত", "income", None, True, None),
    ("4200", "Sales Discounts (dealer funded)", "বিক্রয় ছাড়", "income", None, True, None),
    ("4300", "Scheme / Claim Income", "স্কিম আয়", "income", None, True, None),
    ("4900", "Other Income", "অন্যান্য আয়", "income", None, True, None),
    ("5000", "Cost of Goods Sold", "বিক্রীত পণ্যের ব্যয়", "expense", None, True, None),
    ("5100", "Inventory Write-off", "মজুদ অবলোপন", "expense", None, True, None),
    ("5200", "Commission & Promotion (dealer funded)", "কমিশন ও প্রচার", "expense", None, True, None),
    ("5300", "Rejected Claims Write-off", "বাতিল দাবি", "expense", None, True, None),
    ("5400", "Stock Adjustment / Shrinkage", "মজুদ সমন্বয়", "expense", None, True, None),
    ("6000", "Operating Expenses", "পরিচালন ব্যয়", "expense", None, False, None),
    ("6010", "Rent", "ভাড়া", "expense", "6000", True, None),
    ("6020", "Salaries & Wages", "বেতন ও মজুরি", "expense", "6000", True, None),
    ("6030", "Transport & Delivery", "পরিবহন ও ডেলিভারি", "expense", "6000", True, None),
    ("6040", "Utilities", "ইউটিলিটি", "expense", "6000", True, None),
    ("6050", "Mobile & Internet", "মোবাইল ও ইন্টারনেট", "expense", "6000", True, None),
    ("6060", "Bank & MFS Charges", "ব্যাংক ও এমএফএস চার্জ", "expense", "6000", True, None),
    ("6070", "Entertainment", "আপ্যায়ন", "expense", "6000", True, None),
    ("6090", "Miscellaneous", "বিবিধ", "expense", "6000", True, None),
]


def seed_permissions(session: Session) -> None:
    """Global: the same for every dealership."""
    existing = {p.code: p for p in session.scalars(select(Permission)).all()}
    for code, description in PERMISSIONS.items():
        if code in existing:
            existing[code].description = description
        else:
            session.add(Permission(code=code, description=description))
    session.flush()


def seed_roles(session: Session) -> None:
    """The system roles of the current dealership. Roles it created itself are left alone."""
    permissions = {p.code: p for p in session.scalars(select(Permission)).all()}
    roles = {r.code: r for r in session.scalars(select(Role)).all()}
    for code, (name, description, granted) in ROLES.items():
        role = roles.get(code)
        if role is None:
            role = Role(code=code, is_system=True)
            session.add(role)
        elif not role.is_system:
            continue
        role.name = name
        role.description = description
        role.permissions = [permissions[p] for p in sorted(granted)]
    session.flush()


def seed_uoms(session: Session) -> None:
    existing = set(session.scalars(select(Uom.code)).all())
    for code, name_en, name_bn in UOMS:
        if code not in existing:
            session.add(Uom(code=code, name_en=name_en, name_bn=name_bn))
    session.flush()


def seed_locations(session: Session) -> None:
    existing = set(session.scalars(select(Location.code)).all())
    for code, name_en, name_bn, sellable in LOCATIONS:
        if code not in existing:
            session.add(
                Location(
                    code=code,
                    name_en=name_en,
                    name_bn=name_bn,
                    is_sellable=sellable,
                    is_system=True,
                )
            )
    session.flush()


def seed_accounts(session: Session) -> None:
    by_code = {a.code: a for a in session.scalars(select(Account)).all()}
    for code, name_en, name_bn, type_, parent_code, postable, party_type in ACCOUNTS:
        account = by_code.get(code)
        if account is None:
            account = Account(code=code, is_system=True)
            session.add(account)
            by_code[code] = account
        account.name_en = name_en
        account.name_bn = name_bn
        account.type = type_
        account.is_postable = postable
        account.party_type = party_type
        session.flush()
        account.parent_id = by_code[parent_code].id if parent_code else None
    session.flush()


PAYMENT_ACCOUNTS = [
    ("Cash in Hand", "cash", "1000"),
    ("bKash", "mfs", "1020"),
    ("Nagad", "mfs", "1030"),
]


def seed_payment_accounts(session: Session) -> None:
    existing = set(session.scalars(select(PaymentAccount.name)).all())
    accounts = {a.code: a for a in session.scalars(select(Account)).all()}
    for name, kind, code in PAYMENT_ACCOUNTS:
        if name not in existing:
            session.add(
                PaymentAccount(name=name, kind=kind, account_id=accounts[code].id, is_system=True)
            )
    session.flush()


def seed_walk_in_customer(session: Session) -> None:
    if session.scalar(select(Customer.id).where(Customer.is_walk_in.is_(True))) is None:
        session.add(Customer(code="WALK-IN", name="Walk-in Customer", is_walk_in=True))
        session.flush()


def seed_settings(session: Session, business_name: str | None = None) -> None:
    profile = BusinessProfile(name=business_name) if business_name else BusinessProfile()
    defaults = {
        "business_profile": profile,
        "preferences": Preferences(),
        "period_lock": PeriodLock(),
    }
    for key, value in defaults.items():
        tenant_id = tenant_id_of(session)
        if session.get(AppSetting, (tenant_id, key)) is None:
            session.add(
                AppSetting(tenant_id=tenant_id, key=key, value=value.model_dump(mode="json"))
            )
    session.flush()


def seed_tenant(session: Session, business_name: str | None = None) -> None:
    """Everything the current dealership needs before its first transaction."""
    seed_roles(session)
    seed_uoms(session)
    seed_locations(session)
    seed_accounts(session)
    seed_payment_accounts(session)
    seed_walk_in_customer(session)
    seed_settings(session, business_name)


def provision_tenant(
    session: Session, *, slug: str, legal_name: str, config: BusinessConfig | None = None
) -> Tenant:
    tenant = create_tenant(session, slug=slug, legal_name=legal_name, config=config)
    seed_tenant(session, legal_name)
    return tenant


def run_seed(session: Session) -> None:
    """Commits per dealership, because each one needs its own database context."""
    seed_permissions(session)
    session.commit()
    for tenant_id in [t.id for t in all_tenants(session)]:
        set_context(session, tenant_id=tenant_id)
        seed_tenant(session)
        session.commit()
