from datetime import datetime

from sqlalchemy import BigInteger, Column, ForeignKey, Index, String, Table, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.models import Base, IdMixin, TenantScoped, TimestampMixin

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", BigInteger, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "permission_id",
        BigInteger,
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("tenant_id", BigInteger, server_default=text("app_current_tenant()"), nullable=False),
)

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", BigInteger, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("tenant_id", BigInteger, server_default=text("app_current_tenant()"), nullable=False),
)


class Permission(IdMixin, Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(50), unique=True)
    description: Mapped[str] = mapped_column(String(200))


class Role(TenantScoped, IdMixin, TimestampMixin, Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(300))
    # Kept up to date by the application; dealerships cannot change or delete these.
    is_system: Mapped[bool] = mapped_column(server_default="false")
    permissions: Mapped[list[Permission]] = relationship(
        secondary=role_permissions, lazy="selectin", order_by=Permission.code
    )


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(50))
    full_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(30))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    last_login_at: Mapped[datetime | None]
    # Loaded on use, never with the user: which roles appear depends on the dealership context.
    roles: Mapped[list[Role]] = relationship(secondary=user_roles, order_by=Role.code)

    __table_args__ = (Index("uq_users_username", func.lower(username), unique=True),)

    @property
    def role_codes(self) -> list[str]:
        return [r.code for r in self.roles]

    @property
    def permission_codes(self) -> frozenset[str]:
        return frozenset(p.code for r in self.roles for p in r.permissions)


class RefreshToken(IdMixin, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    replaced_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("refresh_tokens.id"))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    ip: Mapped[str | None] = mapped_column(String(64))
