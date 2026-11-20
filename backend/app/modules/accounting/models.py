from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, IdMixin, TimestampMixin

ACCOUNT_TYPES = ("asset", "liability", "equity", "income", "expense")


class Account(IdMixin, TimestampMixin, Base):
    """Chart of accounts. Journal tables arrive in phase 2."""

    __tablename__ = "accounts"

    code: Mapped[str] = mapped_column(String(10), unique=True)
    name_en: Mapped[str] = mapped_column(String(120))
    name_bn: Mapped[str | None] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(10))
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("accounts.id"))
    is_system: Mapped[bool] = mapped_column(default=False)
    is_postable: Mapped[bool] = mapped_column(default=True)
    party_type: Mapped[str | None] = mapped_column(String(10))  # customer / supplier sub-ledger
    is_active: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (
        CheckConstraint(
            "type IN ('asset','liability','equity','income','expense')", name="type_valid"
        ),
        CheckConstraint(
            "party_type IS NULL OR party_type IN ('customer','supplier')", name="party_type_valid"
        ),
    )
