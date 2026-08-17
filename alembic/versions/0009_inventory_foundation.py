"""Add tenant-scoped physical and channel inventory."""

import sqlalchemy as sa

from alembic import op

revision = "0009_inventory"
down_revision = "0008_shop_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "warehouses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False, server_default=""),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "code", name="uq_warehouses_org_code"),
    )
    op.create_index("ix_warehouses_organization_id", "warehouses", ["organization_id"])
    op.create_index("ix_warehouses_active", "warehouses", ["active"])
    op.create_index(
        "ix_warehouses_org_id_unique", "warehouses", ["organization_id", "id"], unique=True
    )

    op.create_table(
        "warehouse_inventory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("master_sku_id", sa.Integer(), nullable=False),
        sa.Column("available", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("incoming", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("damaged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_reference", sa.String(length=256), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("last_source_shop_id", sa.Integer(), nullable=False),
        sa.Column("last_source_event_id", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "warehouse_id"],
            ["warehouses.organization_id", "warehouses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "master_sku_id"],
            ["master_skus.organization_id", "master_skus.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "last_source_shop_id", "last_source_event_id"],
            [
                "platform_raw_events.organization_id",
                "platform_raw_events.shop_id",
                "platform_raw_events.id",
            ],
        ),
        sa.UniqueConstraint(
            "warehouse_id", "master_sku_id", name="uq_warehouse_inventory_warehouse_sku"
        ),
        sa.CheckConstraint(
            "available >= 0 AND reserved >= 0 AND incoming >= 0 AND damaged >= 0",
            name="ck_warehouse_inventory_quantities",
        ),
    )
    op.create_index(
        "ix_warehouse_inventory_organization_id", "warehouse_inventory", ["organization_id"]
    )
    op.create_index("ix_warehouse_inventory_warehouse_id", "warehouse_inventory", ["warehouse_id"])
    op.create_index(
        "ix_warehouse_inventory_master_sku_id", "warehouse_inventory", ["master_sku_id"]
    )
    op.create_index(
        "ix_warehouse_inventory_source_updated_at", "warehouse_inventory", ["source_updated_at"]
    )
    op.create_index(
        "ix_warehouse_inventory_last_source_shop_id",
        "warehouse_inventory",
        ["last_source_shop_id"],
    )
    op.create_index(
        "ix_warehouse_inventory_last_source_event_id",
        "warehouse_inventory",
        ["last_source_event_id"],
    )
    op.create_index(
        "ix_warehouse_inventory_org_sku",
        "warehouse_inventory",
        ["organization_id", "master_sku_id"],
    )
    op.create_index(
        "ix_warehouse_inventory_org_id_unique",
        "warehouse_inventory",
        ["organization_id", "id"],
        unique=True,
    )

    op.create_table(
        "channel_inventory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("platform_sku_id", sa.Integer(), nullable=False),
        sa.Column("master_sku_id", sa.Integer(), nullable=False),
        sa.Column("available", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_reference", sa.String(length=256), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("last_source_event_id", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "platform_sku_id", "master_sku_id"],
            [
                "platform_skus.organization_id",
                "platform_skus.shop_id",
                "platform_skus.id",
                "platform_skus.master_sku_id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "last_source_event_id"],
            [
                "platform_raw_events.organization_id",
                "platform_raw_events.shop_id",
                "platform_raw_events.id",
            ],
        ),
        sa.UniqueConstraint(
            "shop_id", "platform_sku_id", name="uq_channel_inventory_shop_platform_sku"
        ),
        sa.CheckConstraint(
            "available >= 0 AND reserved >= 0", name="ck_channel_inventory_quantities"
        ),
    )
    op.create_index(
        "ix_channel_inventory_organization_id", "channel_inventory", ["organization_id"]
    )
    op.create_index("ix_channel_inventory_shop_id", "channel_inventory", ["shop_id"])
    op.create_index(
        "ix_channel_inventory_platform_sku_id", "channel_inventory", ["platform_sku_id"]
    )
    op.create_index("ix_channel_inventory_master_sku_id", "channel_inventory", ["master_sku_id"])
    op.create_index(
        "ix_channel_inventory_source_updated_at", "channel_inventory", ["source_updated_at"]
    )
    op.create_index(
        "ix_channel_inventory_last_source_event_id",
        "channel_inventory",
        ["last_source_event_id"],
    )
    op.create_index(
        "ix_channel_inventory_org_sku",
        "channel_inventory",
        ["organization_id", "master_sku_id"],
    )
    op.create_index(
        "ix_channel_inventory_org_shop_id_unique",
        "channel_inventory",
        ["organization_id", "shop_id", "id"],
        unique=True,
    )

    op.create_table(
        "warehouse_inventory_source_events",
        sa.Column("warehouse_inventory_id", sa.Integer(), primary_key=True),
        sa.Column("raw_event_id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("normalized_hash", sa.String(length=64), nullable=False),
        sa.Column("normalizer_version", sa.String(length=32), nullable=False),
        sa.Column("source_occurred_at", sa.DateTime(), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "warehouse_inventory_id"],
            ["warehouse_inventory.organization_id", "warehouse_inventory.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "raw_event_id"],
            [
                "platform_raw_events.organization_id",
                "platform_raw_events.shop_id",
                "platform_raw_events.id",
            ],
        ),
        sa.UniqueConstraint("raw_event_id", name="uq_warehouse_inventory_source_events_raw_event"),
    )
    op.create_index(
        "ix_warehouse_inventory_source_events_organization_id",
        "warehouse_inventory_source_events",
        ["organization_id"],
    )
    op.create_index(
        "ix_warehouse_inventory_source_events_shop_id",
        "warehouse_inventory_source_events",
        ["shop_id"],
    )

    op.create_table(
        "channel_inventory_source_events",
        sa.Column("channel_inventory_id", sa.Integer(), primary_key=True),
        sa.Column("raw_event_id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("normalized_hash", sa.String(length=64), nullable=False),
        sa.Column("normalizer_version", sa.String(length=32), nullable=False),
        sa.Column("source_occurred_at", sa.DateTime(), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "channel_inventory_id"],
            [
                "channel_inventory.organization_id",
                "channel_inventory.shop_id",
                "channel_inventory.id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "raw_event_id"],
            [
                "platform_raw_events.organization_id",
                "platform_raw_events.shop_id",
                "platform_raw_events.id",
            ],
        ),
        sa.UniqueConstraint("raw_event_id", name="uq_channel_inventory_source_events_raw_event"),
    )
    op.create_index(
        "ix_channel_inventory_source_events_organization_id",
        "channel_inventory_source_events",
        ["organization_id"],
    )
    op.create_index(
        "ix_channel_inventory_source_events_shop_id",
        "channel_inventory_source_events",
        ["shop_id"],
    )


def downgrade() -> None:
    op.drop_table("channel_inventory_source_events")
    op.drop_table("warehouse_inventory_source_events")
    op.drop_table("channel_inventory")
    op.drop_table("warehouse_inventory")
    op.drop_table("warehouses")
