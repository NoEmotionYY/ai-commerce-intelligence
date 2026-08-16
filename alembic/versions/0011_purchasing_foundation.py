"""Add tenant-scoped suppliers, purchasing, and inbound shipments."""

import sqlalchemy as sa

from alembic import op

revision = "0011_purchasing"
down_revision = "0010_finance"
branch_labels = None
depends_on = None


def _indexes(table: str, definitions: list[tuple[str, list[str], bool]]) -> None:
    for name, columns, unique in definitions:
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("payment_terms", sa.String(length=200), nullable=True),
        sa.Column("contact_name", sa.String(length=200), nullable=True),
        sa.Column("contact_email", sa.String(length=320), nullable=True),
        sa.Column("contact_phone", sa.String(length=50), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "code", name="uq_suppliers_org_code"),
    )
    _indexes(
        "suppliers",
        [
            ("ix_suppliers_organization_id", ["organization_id"], False),
            ("ix_suppliers_active", ["active"], False),
            ("ix_suppliers_org_id_unique", ["organization_id", "id"], True),
        ],
    )

    op.create_table(
        "supplier_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("master_sku_id", sa.Integer(), nullable=False),
        sa.Column("supplier_product_code", sa.String(length=128), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("purchase_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("moq", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("package_size", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("lead_time_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "supplier_id"],
            ["suppliers.organization_id", "suppliers.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "master_sku_id"],
            ["master_skus.organization_id", "master_skus.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "supplier_id", "master_sku_id", name="uq_supplier_products_supplier_sku"
        ),
        sa.UniqueConstraint(
            "supplier_id", "supplier_product_code", name="uq_supplier_products_supplier_code"
        ),
        sa.CheckConstraint(
            "purchase_cost >= 0 AND moq >= 1 AND package_size >= 1 AND lead_time_days >= 0",
            name="ck_supplier_products_commercial_terms",
        ),
    )
    _indexes(
        "supplier_products",
        [
            ("ix_supplier_products_organization_id", ["organization_id"], False),
            ("ix_supplier_products_supplier_id", ["supplier_id"], False),
            ("ix_supplier_products_master_sku_id", ["master_sku_id"], False),
            ("ix_supplier_products_active", ["active"], False),
            ("ix_supplier_products_org_id_unique", ["organization_id", "id"], True),
            ("ix_supplier_products_org_sku", ["organization_id", "master_sku_id"], False),
        ],
    )

    op.create_table(
        "commerce_purchase_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("po_number", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total_amount", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("approved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("ordered_at", sa.DateTime(), nullable=True),
        sa.Column("shipped_at", sa.DateTime(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "supplier_id"],
            ["suppliers.organization_id", "suppliers.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "warehouse_id"],
            ["warehouses.organization_id", "warehouses.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "organization_id", "po_number", name="uq_commerce_purchase_orders_org_number"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key_hash",
            name="uq_commerce_purchase_orders_org_idempotency",
        ),
        sa.CheckConstraint("total_amount >= 0", name="ck_commerce_purchase_orders_total"),
        sa.CheckConstraint(
            "status IN ('DRAFT','PENDING_APPROVAL','APPROVED','REJECTED','ORDERED','SHIPPED',"
            "'RECEIVED','CLOSED','CANCELLED')",
            name="ck_commerce_purchase_orders_status",
        ),
    )
    _indexes(
        "commerce_purchase_orders",
        [
            ("ix_commerce_purchase_orders_organization_id", ["organization_id"], False),
            ("ix_commerce_purchase_orders_supplier_id", ["supplier_id"], False),
            ("ix_commerce_purchase_orders_warehouse_id", ["warehouse_id"], False),
            ("ix_commerce_purchase_orders_status", ["status"], False),
            ("ix_commerce_purchase_orders_org_id_unique", ["organization_id", "id"], True),
        ],
    )

    op.create_table(
        "commerce_purchase_order_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("purchase_order_id", sa.Integer(), nullable=False),
        sa.Column("supplier_product_id", sa.Integer(), nullable=False),
        sa.Column("master_sku_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("received_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unit_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("total_amount", sa.Numeric(18, 4), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "purchase_order_id"],
            ["commerce_purchase_orders.organization_id", "commerce_purchase_orders.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "supplier_product_id"],
            ["supplier_products.organization_id", "supplier_products.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "master_sku_id"],
            ["master_skus.organization_id", "master_skus.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "purchase_order_id",
            "supplier_product_id",
            name="uq_commerce_purchase_order_items_product",
        ),
        sa.CheckConstraint(
            "quantity > 0 AND received_quantity >= 0 AND received_quantity <= quantity "
            "AND unit_cost >= 0 AND total_amount >= 0",
            name="ck_commerce_purchase_order_items_values",
        ),
    )
    _indexes(
        "commerce_purchase_order_items",
        [
            ("ix_commerce_purchase_order_items_organization_id", ["organization_id"], False),
            ("ix_commerce_purchase_order_items_purchase_order_id", ["purchase_order_id"], False),
            (
                "ix_commerce_purchase_order_items_supplier_product_id",
                ["supplier_product_id"],
                False,
            ),
            ("ix_commerce_purchase_order_items_master_sku_id", ["master_sku_id"], False),
            (
                "ix_commerce_purchase_order_items_org_id_unique",
                ["organization_id", "id"],
                True,
            ),
        ],
    )

    op.create_table(
        "inbound_shipments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("purchase_order_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("shipment_number", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("expected_at", sa.DateTime(), nullable=False),
        sa.Column("shipped_at", sa.DateTime(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "purchase_order_id"],
            ["commerce_purchase_orders.organization_id", "commerce_purchase_orders.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "warehouse_id"],
            ["warehouses.organization_id", "warehouses.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "organization_id", "shipment_number", name="uq_inbound_shipments_org_number"
        ),
        sa.CheckConstraint(
            "status IN ('PLANNED','SHIPPED','PARTIALLY_RECEIVED','RECEIVED','CANCELLED')",
            name="ck_inbound_shipments_status",
        ),
    )
    _indexes(
        "inbound_shipments",
        [
            ("ix_inbound_shipments_organization_id", ["organization_id"], False),
            ("ix_inbound_shipments_purchase_order_id", ["purchase_order_id"], False),
            ("ix_inbound_shipments_warehouse_id", ["warehouse_id"], False),
            ("ix_inbound_shipments_status", ["status"], False),
            ("ix_inbound_shipments_expected_at", ["expected_at"], False),
            ("ix_inbound_shipments_org_id_unique", ["organization_id", "id"], True),
        ],
    )

    op.create_table(
        "inbound_shipment_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("inbound_shipment_id", sa.Integer(), nullable=False),
        sa.Column("purchase_order_item_id", sa.Integer(), nullable=False),
        sa.Column("master_sku_id", sa.Integer(), nullable=False),
        sa.Column("quantity_shipped", sa.Integer(), nullable=False),
        sa.Column("quantity_received", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["organization_id", "inbound_shipment_id"],
            ["inbound_shipments.organization_id", "inbound_shipments.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "purchase_order_item_id"],
            ["commerce_purchase_order_items.organization_id", "commerce_purchase_order_items.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "master_sku_id"],
            ["master_skus.organization_id", "master_skus.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "inbound_shipment_id",
            "purchase_order_item_id",
            name="uq_inbound_shipment_items_order_item",
        ),
        sa.CheckConstraint(
            "quantity_shipped > 0 AND quantity_received >= 0 "
            "AND quantity_received <= quantity_shipped",
            name="ck_inbound_shipment_items_quantities",
        ),
    )
    _indexes(
        "inbound_shipment_items",
        [
            ("ix_inbound_shipment_items_organization_id", ["organization_id"], False),
            ("ix_inbound_shipment_items_inbound_shipment_id", ["inbound_shipment_id"], False),
            ("ix_inbound_shipment_items_purchase_order_item_id", ["purchase_order_item_id"], False),
            ("ix_inbound_shipment_items_master_sku_id", ["master_sku_id"], False),
            ("ix_inbound_shipment_items_org_id_unique", ["organization_id", "id"], True),
        ],
    )


def downgrade() -> None:
    op.drop_table("inbound_shipment_items")
    op.drop_table("inbound_shipments")
    op.drop_table("commerce_purchase_order_items")
    op.drop_table("commerce_purchase_orders")
    op.drop_table("supplier_products")
    op.drop_table("suppliers")
