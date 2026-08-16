"""Add encrypted shop credential storage."""

import sqlalchemy as sa

from alembic import op

revision = "0004_shop_credentials"
down_revision = "0003_tenant_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shop_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("credential_type", sa.String(length=50), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=False),
        sa.Column("encrypted_payload", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["shop_id"], ["shops.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("shop_id", "credential_type", name="uq_shop_credential_type"),
    )
    op.create_index("ix_shop_credentials_shop_id", "shop_credentials", ["shop_id"])
    op.create_index("ix_shop_credentials_key_id", "shop_credentials", ["key_id"])
    op.create_index("ix_shop_credentials_status", "shop_credentials", ["status"])


def downgrade() -> None:
    op.drop_table("shop_credentials")
