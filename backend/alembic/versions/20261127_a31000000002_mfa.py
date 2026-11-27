"""second factor

Revision ID: a31000000002
Revises: a31000000001
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a31000000002"
down_revision: str | Sequence[str] | None = "a31000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mfa_factors",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, primary_key=True),
        sa.Column("secret_encrypted", sa.String(300), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_step", sa.BigInteger(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_mfa_factors_user_id_users", ondelete="CASCADE"
        ),
    )
    op.create_table(
        "mfa_recovery_codes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_mfa_recovery_codes_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_mfa_recovery_codes_user_id", "mfa_recovery_codes", ["user_id"])


def downgrade() -> None:
    op.drop_table("mfa_recovery_codes")
    op.drop_table("mfa_factors")
