from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.models import Base, TenantScoped


class AppSetting(TenantScoped, Base):
    __tablename__ = "app_settings"

    tenant_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("app_current_tenant()")
    )
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    updated_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
