"""Add tenant-scoped unified commerce orders and source lineage."""

import sqlalchemy as sa

from alembic import op

revision = "0007_unified_orders"
down_revision = "0006_raw_event_sync_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "commerce_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=False),
        sa.Column("external_order_id", sa.String(length=256), nullable=False),
        sa.Column("external_order_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("external_status", sa.String(length=100), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("ordered_at", sa.DateTime(), nullable=False),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("shipped_at", sa.DateTime(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("refunded_at", sa.DateTime(), nullable=True),
        sa.Column("settled_at", sa.DateTime(), nullable=True),
        sa.Column("last_source_event_id", sa.Integer(), nullable=False),
        sa.Column("last_source_occurred_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
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
            "shop_id", "external_order_key", name="uq_commerce_orders_shop_external_key"
        ),
        sa.CheckConstraint("total_amount >= 0", name="ck_commerce_orders_total_amount"),
        sa.CheckConstraint(
            "status IN ('PENDING_PAYMENT','PAID','READY_TO_SHIP','SHIPPED','DELIVERED',"
            "'COMPLETED','CANCELLED','PARTIALLY_REFUNDED','REFUNDED')",
            name="ck_commerce_orders_status",
        ),
    )
    op.create_index("ix_commerce_orders_organization_id", "commerce_orders", ["organization_id"])
    op.create_index("ix_commerce_orders_shop_id", "commerce_orders", ["shop_id"])
    op.create_index("ix_commerce_orders_platform", "commerce_orders", ["platform"])
    op.create_index("ix_commerce_orders_status", "commerce_orders", ["status"])
    op.create_index("ix_commerce_orders_currency", "commerce_orders", ["currency"])
    op.create_index("ix_commerce_orders_ordered_at", "commerce_orders", ["ordered_at"])
    op.create_index(
        "ix_commerce_orders_last_source_event_id", "commerce_orders", ["last_source_event_id"]
    )
    op.create_index(
        "ix_commerce_orders_last_source_occurred_at",
        "commerce_orders",
        ["last_source_occurred_at"],
    )
    op.create_index(
        "ix_commerce_orders_org_shop_id_unique",
        "commerce_orders",
        ["organization_id", "shop_id", "id"],
        unique=True,
    )

    op.create_table(
        "commerce_order_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("platform_sku_id", sa.Integer(), nullable=False),
        sa.Column("master_sku_id", sa.Integer(), nullable=False),
        sa.Column("external_item_id", sa.String(length=256), nullable=False),
        sa.Column("external_item_key", sa.String(length=64), nullable=False),
        sa.Column("external_sku_id", sa.String(length=128), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("line_amount", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "order_id"],
            ["commerce_orders.organization_id", "commerce_orders.shop_id", "commerce_orders.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "platform_sku_id", "master_sku_id"],
            [
                "platform_skus.organization_id",
                "platform_skus.shop_id",
                "platform_skus.id",
                "platform_skus.master_sku_id",
            ],
        ),
        sa.UniqueConstraint(
            "order_id", "external_item_key", name="uq_commerce_order_items_order_external_key"
        ),
        sa.CheckConstraint("quantity > 0", name="ck_commerce_order_items_quantity"),
        sa.CheckConstraint("unit_price >= 0", name="ck_commerce_order_items_unit_price"),
        sa.CheckConstraint("line_amount >= 0", name="ck_commerce_order_items_line_amount"),
    )
    op.create_index(
        "ix_commerce_order_items_organization_id", "commerce_order_items", ["organization_id"]
    )
    op.create_index("ix_commerce_order_items_shop_id", "commerce_order_items", ["shop_id"])
    op.create_index("ix_commerce_order_items_order_id", "commerce_order_items", ["order_id"])
    op.create_index(
        "ix_commerce_order_items_platform_sku_id", "commerce_order_items", ["platform_sku_id"]
    )
    op.create_index(
        "ix_commerce_order_items_master_sku_id", "commerce_order_items", ["master_sku_id"]
    )

    op.create_table(
        "commerce_order_source_events",
        sa.Column("order_id", sa.Integer(), primary_key=True),
        sa.Column("raw_event_id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("normalized_hash", sa.String(length=64), nullable=False),
        sa.Column("normalizer_version", sa.String(length=32), nullable=False),
        sa.Column("source_occurred_at", sa.DateTime(), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "order_id"],
            ["commerce_orders.organization_id", "commerce_orders.shop_id", "commerce_orders.id"],
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
        sa.UniqueConstraint("raw_event_id", name="uq_commerce_order_source_events_raw_event"),
    )
    op.create_index(
        "ix_commerce_order_source_events_organization_id",
        "commerce_order_source_events",
        ["organization_id"],
    )
    op.create_index(
        "ix_commerce_order_source_events_shop_id", "commerce_order_source_events", ["shop_id"]
    )


def downgrade() -> None:
    op.drop_table("commerce_order_source_events")
    op.drop_table("commerce_order_items")
    op.drop_table("commerce_orders")
