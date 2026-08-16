"""Add organization-scoped master catalog and platform SKU identity."""

import sqlalchemy as sa

from alembic import op

revision = "0005_unified_catalog_identity"
down_revision = "0004_shop_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_shops_org_id_unique",
        "shops",
        ["organization_id", "id"],
        unique=True,
    )
    op.create_table(
        "master_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "code", name="uq_master_products_org_code"),
    )
    op.create_index("ix_master_products_organization_id", "master_products", ["organization_id"])
    op.create_index("ix_master_products_active", "master_products", ["active"])
    op.create_index(
        "ix_master_products_org_id_unique",
        "master_products",
        ["organization_id", "id"],
        unique=True,
    )

    op.create_table(
        "master_skus",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("master_product_id", sa.Integer(), nullable=False),
        sa.Column("sku_code", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "master_product_id"],
            ["master_products.organization_id", "master_products.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "sku_code", name="uq_master_skus_org_code"),
    )
    op.create_index("ix_master_skus_organization_id", "master_skus", ["organization_id"])
    op.create_index("ix_master_skus_master_product_id", "master_skus", ["master_product_id"])
    op.create_index("ix_master_skus_active", "master_skus", ["active"])
    op.create_index(
        "ix_master_skus_org_id_unique",
        "master_skus",
        ["organization_id", "id"],
        unique=True,
    )

    op.create_table(
        "platform_skus",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("master_sku_id", sa.Integer(), nullable=False),
        sa.Column("external_product_id", sa.String(length=128), nullable=False),
        sa.Column("external_sku_id", sa.String(length=128), nullable=False),
        sa.Column("external_sku_key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "master_sku_id"],
            ["master_skus.organization_id", "master_skus.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "shop_id", "external_sku_key", name="uq_platform_skus_shop_external_sku_key"
        ),
    )
    op.create_index("ix_platform_skus_organization_id", "platform_skus", ["organization_id"])
    op.create_index("ix_platform_skus_shop_id", "platform_skus", ["shop_id"])
    op.create_index("ix_platform_skus_master_sku_id", "platform_skus", ["master_sku_id"])
    op.create_index("ix_platform_skus_active", "platform_skus", ["active"])
    op.create_index(
        "ix_platform_skus_org_shop_id_master_unique",
        "platform_skus",
        ["organization_id", "shop_id", "id", "master_sku_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("platform_skus")
    op.drop_table("master_skus")
    op.drop_table("master_products")
    op.drop_index("ix_shops_org_id_unique", table_name="shops")
