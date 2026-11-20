from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, IdMixin, TimestampMixin


class Location(IdMixin, TimestampMixin, Base):
    """Logical stock bucket: SELLABLE, DAMAGED, EXPIRED (extensible to warehouses)."""

    __tablename__ = "locations"

    code: Mapped[str] = mapped_column(String(20), unique=True)
    name_en: Mapped[str] = mapped_column(String(100))
    name_bn: Mapped[str | None] = mapped_column(String(100))
    is_sellable: Mapped[bool] = mapped_column(default=False)
    is_system: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
