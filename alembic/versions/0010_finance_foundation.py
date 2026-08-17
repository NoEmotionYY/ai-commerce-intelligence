"""Add tenant-scoped costs, refunds, settlements, finance, and profit history."""

import sqlalchemy as sa

from alembic import op

revision = "0010_finance"
down_revision = "0009_inventory"
branch_labels = None
depends_on = None


def _indexes(table: str, definitions: list[tuple[str, list[str], bool]]) -> None:
    for name, columns, unique in definitions:
        op.create_index(name, table, columns, unique=unique)


def _ensure_index(table: str, name: str, columns: list[str], *, unique: bool = False) -> None:
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    _ensure_index(
        "commerce_order_items",
        "ix_commerce_order_items_org_shop_order_fk",
        ["organization_id", "shop_id", "order_id"],
    )
    op.create_index(
        "ix_commerce_order_items_org_shop_order_id_sku_unique",
        "commerce_order_items",
        ["organization_id", "shop_id", "order_id", "id", "master_sku_id"],
        unique=True,
    )

    op.create_table(
        "sku_costs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("master_sku_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("purchase_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("packaging_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("domestic_shipping_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("cross_border_shipping_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("warehouse_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("other_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("effective_from", sa.DateTime(), nullable=False),
        sa.Column("effective_to", sa.DateTime(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_reference", sa.String(length=256), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "master_sku_id"],
            ["master_skus.organization_id", "master_skus.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.UniqueConstraint(
            "organization_id",
            "master_sku_id",
            "effective_from",
            name="uq_sku_costs_org_sku_effective_from",
        ),
        sa.CheckConstraint(
            "purchase_cost >= 0 AND packaging_cost >= 0 AND domestic_shipping_cost >= 0 "
            "AND cross_border_shipping_cost >= 0 AND warehouse_cost >= 0 AND other_cost >= 0",
            name="ck_sku_costs_nonnegative",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_sku_costs_effective_range",
        ),
    )
    _indexes(
        "sku_costs",
        [
            ("ix_sku_costs_organization_id", ["organization_id"], False),
            ("ix_sku_costs_master_sku_id", ["master_sku_id"], False),
            ("ix_sku_costs_currency", ["currency"], False),
            ("ix_sku_costs_effective_from", ["effective_from"], False),
            ("ix_sku_costs_created_by_user_id", ["created_by_user_id"], False),
            (
                "ix_sku_costs_org_sku_effective",
                ["organization_id", "master_sku_id", "effective_from"],
                False,
            ),
            ("ix_sku_costs_org_id_unique", ["organization_id", "id"], True),
        ],
    )

    op.create_table(
        "refunds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("external_refund_id", sa.String(length=256), nullable=False),
        sa.Column("external_refund_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("external_status", sa.String(length=100), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("reporting_currency", sa.String(length=3), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(24, 10), nullable=False),
        sa.Column("exchange_rate_effective_at", sa.DateTime(), nullable=False),
        sa.Column("exchange_rate_source", sa.String(length=100), nullable=False),
        sa.Column("reporting_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("refunded_at", sa.DateTime(), nullable=True),
        sa.Column("last_source_event_id", sa.Integer(), nullable=False),
        sa.Column("last_source_occurred_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "order_id"],
            ["commerce_orders.organization_id", "commerce_orders.shop_id", "commerce_orders.id"],
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
        sa.UniqueConstraint("shop_id", "external_refund_key", name="uq_refunds_shop_external_key"),
        sa.CheckConstraint("amount >= 0", name="ck_refunds_amount"),
        sa.CheckConstraint("reporting_amount >= 0", name="ck_refunds_reporting_amount"),
        sa.CheckConstraint("exchange_rate > 0", name="ck_refunds_exchange_rate"),
        sa.CheckConstraint(
            "status IN ('REQUESTED','APPROVED','PROCESSING','COMPLETED','REJECTED','CANCELLED')",
            name="ck_refunds_status",
        ),
    )
    _indexes(
        "refunds",
        [
            ("ix_refunds_organization_id", ["organization_id"], False),
            ("ix_refunds_shop_id", ["shop_id"], False),
            ("ix_refunds_order_id", ["order_id"], False),
            ("ix_refunds_status", ["status"], False),
            ("ix_refunds_currency", ["currency"], False),
            ("ix_refunds_reporting_currency", ["reporting_currency"], False),
            ("ix_refunds_refunded_at", ["refunded_at"], False),
            ("ix_refunds_last_source_event_id", ["last_source_event_id"], False),
            ("ix_refunds_last_source_occurred_at", ["last_source_occurred_at"], False),
            (
                "ix_refunds_org_shop_order_id_unique",
                ["organization_id", "shop_id", "order_id", "id"],
                True,
            ),
        ],
    )

    op.create_table(
        "refund_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("refund_id", sa.Integer(), nullable=False),
        sa.Column("order_item_id", sa.Integer(), nullable=False),
        sa.Column("master_sku_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "order_id", "refund_id"],
            ["refunds.organization_id", "refunds.shop_id", "refunds.order_id", "refunds.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "order_id", "order_item_id", "master_sku_id"],
            [
                "commerce_order_items.organization_id",
                "commerce_order_items.shop_id",
                "commerce_order_items.order_id",
                "commerce_order_items.id",
                "commerce_order_items.master_sku_id",
            ],
        ),
        sa.UniqueConstraint("refund_id", "order_item_id", name="uq_refund_items_refund_order_item"),
        sa.CheckConstraint("quantity > 0", name="ck_refund_items_quantity"),
        sa.CheckConstraint("amount >= 0", name="ck_refund_items_amount"),
    )
    _indexes(
        "refund_items",
        [
            ("ix_refund_items_organization_id", ["organization_id"], False),
            ("ix_refund_items_shop_id", ["shop_id"], False),
            ("ix_refund_items_order_id", ["order_id"], False),
            ("ix_refund_items_refund_id", ["refund_id"], False),
            ("ix_refund_items_order_item_id", ["order_item_id"], False),
            ("ix_refund_items_master_sku_id", ["master_sku_id"], False),
        ],
    )

    op.create_table(
        "refund_source_events",
        sa.Column("refund_id", sa.Integer(), primary_key=True),
        sa.Column("raw_event_id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("normalized_hash", sa.String(length=64), nullable=False),
        sa.Column("normalizer_version", sa.String(length=32), nullable=False),
        sa.Column("source_occurred_at", sa.DateTime(), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "order_id", "refund_id"],
            ["refunds.organization_id", "refunds.shop_id", "refunds.order_id", "refunds.id"],
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
        sa.UniqueConstraint("raw_event_id", name="uq_refund_source_events_raw_event"),
    )
    _indexes(
        "refund_source_events",
        [
            ("ix_refund_source_events_organization_id", ["organization_id"], False),
            ("ix_refund_source_events_shop_id", ["shop_id"], False),
            ("ix_refund_source_events_order_id", ["order_id"], False),
        ],
    )

    op.create_table(
        "settlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("external_settlement_id", sa.String(length=256), nullable=False),
        sa.Column("external_settlement_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("gross_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("fee_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("refund_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("adjustment_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("net_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("reporting_currency", sa.String(length=3), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(24, 10), nullable=False),
        sa.Column("exchange_rate_effective_at", sa.DateTime(), nullable=False),
        sa.Column("exchange_rate_source", sa.String(length=100), nullable=False),
        sa.Column("reporting_net_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("settled_at", sa.DateTime(), nullable=True),
        sa.Column("last_source_event_id", sa.Integer(), nullable=False),
        sa.Column("last_source_occurred_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"], ["shops.organization_id", "shops.id"]
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
            "shop_id", "external_settlement_key", name="uq_settlements_shop_external_key"
        ),
        sa.CheckConstraint(
            "gross_amount >= 0 AND fee_amount >= 0 AND refund_amount >= 0",
            name="ck_settlements_nonnegative",
        ),
        sa.CheckConstraint("exchange_rate > 0", name="ck_settlements_exchange_rate"),
        sa.CheckConstraint("period_end >= period_start", name="ck_settlements_period"),
        sa.CheckConstraint(
            "status IN ('PENDING','PROCESSING','SETTLED','FAILED','REVERSED')",
            name="ck_settlements_status",
        ),
    )
    _indexes(
        "settlements",
        [
            ("ix_settlements_organization_id", ["organization_id"], False),
            ("ix_settlements_shop_id", ["shop_id"], False),
            ("ix_settlements_status", ["status"], False),
            ("ix_settlements_currency", ["currency"], False),
            ("ix_settlements_reporting_currency", ["reporting_currency"], False),
            ("ix_settlements_period_start", ["period_start"], False),
            ("ix_settlements_period_end", ["period_end"], False),
            ("ix_settlements_settled_at", ["settled_at"], False),
            ("ix_settlements_last_source_event_id", ["last_source_event_id"], False),
            ("ix_settlements_last_source_occurred_at", ["last_source_occurred_at"], False),
            (
                "ix_settlements_org_shop_id_unique",
                ["organization_id", "shop_id", "id"],
                True,
            ),
        ],
    )

    op.create_table(
        "settlement_source_events",
        sa.Column("settlement_id", sa.Integer(), primary_key=True),
        sa.Column("raw_event_id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("normalized_hash", sa.String(length=64), nullable=False),
        sa.Column("normalizer_version", sa.String(length=32), nullable=False),
        sa.Column("source_occurred_at", sa.DateTime(), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "settlement_id"],
            ["settlements.organization_id", "settlements.shop_id", "settlements.id"],
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
        sa.UniqueConstraint("raw_event_id", name="uq_settlement_source_events_raw_event"),
    )
    _indexes(
        "settlement_source_events",
        [
            ("ix_settlement_source_events_organization_id", ["organization_id"], False),
            ("ix_settlement_source_events_shop_id", ["shop_id"], False),
        ],
    )

    op.create_table(
        "finance_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("settlement_id", sa.Integer(), nullable=True),
        sa.Column("external_transaction_id", sa.String(length=256), nullable=False),
        sa.Column("external_transaction_key", sa.String(length=64), nullable=False),
        sa.Column("transaction_type", sa.String(length=24), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("reporting_currency", sa.String(length=3), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(24, 10), nullable=False),
        sa.Column("exchange_rate_effective_at", sa.DateTime(), nullable=False),
        sa.Column("exchange_rate_source", sa.String(length=100), nullable=False),
        sa.Column("reporting_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("last_source_event_id", sa.Integer(), nullable=False),
        sa.Column("last_source_occurred_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"], ["shops.organization_id", "shops.id"]
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "order_id"],
            ["commerce_orders.organization_id", "commerce_orders.shop_id", "commerce_orders.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "settlement_id"],
            ["settlements.organization_id", "settlements.shop_id", "settlements.id"],
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
            "shop_id", "external_transaction_key", name="uq_finance_transactions_shop_external_key"
        ),
        sa.CheckConstraint("amount >= 0", name="ck_finance_transactions_amount"),
        sa.CheckConstraint(
            "reporting_amount >= 0", name="ck_finance_transactions_reporting_amount"
        ),
        sa.CheckConstraint("exchange_rate > 0", name="ck_finance_transactions_exchange_rate"),
        sa.CheckConstraint(
            "direction IN ('CREDIT','DEBIT')", name="ck_finance_transactions_direction"
        ),
        sa.CheckConstraint(
            "transaction_type IN ('REVENUE','PLATFORM_FEE','LOGISTICS','ADVERTISING','REFUND',"
            "'TAX','ADJUSTMENT','OTHER')",
            name="ck_finance_transactions_type",
        ),
    )
    _indexes(
        "finance_transactions",
        [
            ("ix_finance_transactions_organization_id", ["organization_id"], False),
            ("ix_finance_transactions_shop_id", ["shop_id"], False),
            ("ix_finance_transactions_order_id", ["order_id"], False),
            ("ix_finance_transactions_settlement_id", ["settlement_id"], False),
            ("ix_finance_transactions_transaction_type", ["transaction_type"], False),
            ("ix_finance_transactions_direction", ["direction"], False),
            ("ix_finance_transactions_currency", ["currency"], False),
            ("ix_finance_transactions_reporting_currency", ["reporting_currency"], False),
            ("ix_finance_transactions_occurred_at", ["occurred_at"], False),
            ("ix_finance_transactions_last_source_event_id", ["last_source_event_id"], False),
            (
                "ix_finance_transactions_last_source_occurred_at",
                ["last_source_occurred_at"],
                False,
            ),
            (
                "ix_finance_transactions_org_shop_id_unique",
                ["organization_id", "shop_id", "id"],
                True,
            ),
        ],
    )

    op.create_table(
        "finance_transaction_source_events",
        sa.Column("finance_transaction_id", sa.Integer(), primary_key=True),
        sa.Column("raw_event_id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("normalized_hash", sa.String(length=64), nullable=False),
        sa.Column("normalizer_version", sa.String(length=32), nullable=False),
        sa.Column("source_occurred_at", sa.DateTime(), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "finance_transaction_id"],
            [
                "finance_transactions.organization_id",
                "finance_transactions.shop_id",
                "finance_transactions.id",
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
        sa.UniqueConstraint("raw_event_id", name="uq_finance_transaction_source_events_raw_event"),
    )
    _indexes(
        "finance_transaction_source_events",
        [
            ("ix_finance_transaction_source_events_org_id", ["organization_id"], False),
            ("ix_finance_transaction_source_events_shop_id", ["shop_id"], False),
        ],
    )

    op.create_table(
        "profit_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("settlement_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("reporting_currency", sa.String(length=3), nullable=False),
        sa.Column("revenue_currency", sa.String(length=3), nullable=False),
        sa.Column("revenue_exchange_rate", sa.Numeric(24, 10), nullable=False),
        sa.Column("revenue_exchange_rate_effective_at", sa.DateTime(), nullable=False),
        sa.Column("revenue_exchange_rate_source", sa.String(length=100), nullable=False),
        sa.Column("gross_revenue", sa.Numeric(18, 4), nullable=False),
        sa.Column("refund_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("cost_of_goods", sa.Numeric(18, 4), nullable=False),
        sa.Column("platform_fee", sa.Numeric(18, 4), nullable=False),
        sa.Column("logistics_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("advertising_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("adjustment_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("profit_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("calculation_hash", sa.String(length=64), nullable=False),
        sa.Column("calculated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "order_id"],
            ["commerce_orders.organization_id", "commerce_orders.shop_id", "commerce_orders.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "settlement_id"],
            ["settlements.organization_id", "settlements.shop_id", "settlements.id"],
        ),
        sa.UniqueConstraint(
            "order_id", "kind", "calculation_hash", name="uq_profit_snapshots_order_kind_hash"
        ),
        sa.CheckConstraint(
            "gross_revenue >= 0 AND refund_amount >= 0 AND cost_of_goods >= 0 "
            "AND platform_fee >= 0 AND logistics_cost >= 0 AND advertising_cost >= 0",
            name="ck_profit_snapshots_nonnegative",
        ),
        sa.CheckConstraint(
            "revenue_exchange_rate > 0", name="ck_profit_snapshots_revenue_exchange_rate"
        ),
        sa.CheckConstraint("kind IN ('ESTIMATED','SETTLED')", name="ck_profit_snapshots_kind"),
    )
    _indexes(
        "profit_snapshots",
        [
            ("ix_profit_snapshots_organization_id", ["organization_id"], False),
            ("ix_profit_snapshots_shop_id", ["shop_id"], False),
            ("ix_profit_snapshots_order_id", ["order_id"], False),
            ("ix_profit_snapshots_settlement_id", ["settlement_id"], False),
            ("ix_profit_snapshots_kind", ["kind"], False),
            ("ix_profit_snapshots_reporting_currency", ["reporting_currency"], False),
            ("ix_profit_snapshots_calculated_at", ["calculated_at"], False),
            (
                "ix_profit_snapshots_org_shop_id_unique",
                ["organization_id", "shop_id", "id"],
                True,
            ),
        ],
    )

    op.create_table(
        "profit_snapshot_cost_inputs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("profit_snapshot_id", sa.Integer(), nullable=False),
        sa.Column("order_item_id", sa.Integer(), nullable=False),
        sa.Column("master_sku_id", sa.Integer(), nullable=False),
        sa.Column("sku_cost_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("cost_currency", sa.String(length=3), nullable=False),
        sa.Column("purchase_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("packaging_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("domestic_shipping_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("cross_border_shipping_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("warehouse_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("other_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(24, 10), nullable=False),
        sa.Column("exchange_rate_effective_at", sa.DateTime(), nullable=False),
        sa.Column("exchange_rate_source", sa.String(length=100), nullable=False),
        sa.Column("reporting_total_cost", sa.Numeric(18, 4), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "profit_snapshot_id"],
            [
                "profit_snapshots.organization_id",
                "profit_snapshots.shop_id",
                "profit_snapshots.id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "sku_cost_id"],
            ["sku_costs.organization_id", "sku_costs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "order_id", "order_item_id", "master_sku_id"],
            [
                "commerce_order_items.organization_id",
                "commerce_order_items.shop_id",
                "commerce_order_items.order_id",
                "commerce_order_items.id",
                "commerce_order_items.master_sku_id",
            ],
        ),
        sa.UniqueConstraint(
            "profit_snapshot_id", "order_item_id", name="uq_profit_snapshot_cost_inputs_item"
        ),
        sa.CheckConstraint("quantity > 0", name="ck_profit_snapshot_cost_inputs_quantity"),
        sa.CheckConstraint(
            "purchase_cost >= 0 AND packaging_cost >= 0 AND domestic_shipping_cost >= 0 "
            "AND cross_border_shipping_cost >= 0 AND warehouse_cost >= 0 AND other_cost >= 0 "
            "AND exchange_rate > 0 AND reporting_total_cost >= 0",
            name="ck_profit_snapshot_cost_inputs_values",
        ),
    )
    _indexes(
        "profit_snapshot_cost_inputs",
        [
            ("ix_profit_snapshot_cost_inputs_org_id", ["organization_id"], False),
            ("ix_profit_snapshot_cost_inputs_shop_id", ["shop_id"], False),
            ("ix_profit_snapshot_cost_inputs_order_id", ["order_id"], False),
            ("ix_profit_snapshot_cost_inputs_snapshot_id", ["profit_snapshot_id"], False),
            ("ix_profit_snapshot_cost_inputs_order_item_id", ["order_item_id"], False),
            ("ix_profit_snapshot_cost_inputs_master_sku_id", ["master_sku_id"], False),
            ("ix_profit_snapshot_cost_inputs_sku_cost_id", ["sku_cost_id"], False),
        ],
    )

    op.create_table(
        "profit_snapshot_refund_inputs",
        sa.Column("profit_snapshot_id", sa.Integer(), primary_key=True),
        sa.Column("refund_id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("reporting_currency", sa.String(length=3), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(24, 10), nullable=False),
        sa.Column("exchange_rate_effective_at", sa.DateTime(), nullable=False),
        sa.Column("exchange_rate_source", sa.String(length=100), nullable=False),
        sa.Column("reporting_amount", sa.Numeric(18, 4), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "profit_snapshot_id"],
            [
                "profit_snapshots.organization_id",
                "profit_snapshots.shop_id",
                "profit_snapshots.id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "order_id", "refund_id"],
            ["refunds.organization_id", "refunds.shop_id", "refunds.order_id", "refunds.id"],
        ),
        sa.UniqueConstraint(
            "profit_snapshot_id", "refund_id", name="uq_profit_snapshot_refund_inputs_refund"
        ),
        sa.CheckConstraint(
            "amount >= 0 AND exchange_rate > 0 AND reporting_amount >= 0",
            name="ck_profit_snapshot_refund_inputs_values",
        ),
    )
    _indexes(
        "profit_snapshot_refund_inputs",
        [
            ("ix_profit_snapshot_refund_inputs_org_id", ["organization_id"], False),
            ("ix_profit_snapshot_refund_inputs_shop_id", ["shop_id"], False),
            ("ix_profit_snapshot_refund_inputs_order_id", ["order_id"], False),
        ],
    )

    op.create_table(
        "profit_snapshot_settlement_inputs",
        sa.Column("profit_snapshot_id", sa.Integer(), primary_key=True),
        sa.Column("settlement_id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("net_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("reporting_currency", sa.String(length=3), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(24, 10), nullable=False),
        sa.Column("exchange_rate_effective_at", sa.DateTime(), nullable=False),
        sa.Column("exchange_rate_source", sa.String(length=100), nullable=False),
        sa.Column("reporting_net_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("settled_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "profit_snapshot_id"],
            [
                "profit_snapshots.organization_id",
                "profit_snapshots.shop_id",
                "profit_snapshots.id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "settlement_id"],
            ["settlements.organization_id", "settlements.shop_id", "settlements.id"],
        ),
        sa.CheckConstraint(
            "exchange_rate > 0", name="ck_profit_snapshot_settlement_inputs_exchange_rate"
        ),
    )
    _indexes(
        "profit_snapshot_settlement_inputs",
        [
            ("ix_profit_snapshot_settlement_inputs_org_id", ["organization_id"], False),
            ("ix_profit_snapshot_settlement_inputs_shop_id", ["shop_id"], False),
        ],
    )

    op.create_table(
        "profit_snapshot_transaction_inputs",
        sa.Column("profit_snapshot_id", sa.Integer(), primary_key=True),
        sa.Column("finance_transaction_id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("transaction_type", sa.String(length=24), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("reporting_currency", sa.String(length=3), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(24, 10), nullable=False),
        sa.Column("exchange_rate_effective_at", sa.DateTime(), nullable=False),
        sa.Column("exchange_rate_source", sa.String(length=100), nullable=False),
        sa.Column("reporting_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "profit_snapshot_id"],
            [
                "profit_snapshots.organization_id",
                "profit_snapshots.shop_id",
                "profit_snapshots.id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "finance_transaction_id"],
            [
                "finance_transactions.organization_id",
                "finance_transactions.shop_id",
                "finance_transactions.id",
            ],
        ),
        sa.UniqueConstraint(
            "profit_snapshot_id",
            "finance_transaction_id",
            name="uq_profit_snapshot_transaction_inputs_transaction",
        ),
        sa.CheckConstraint(
            "amount >= 0 AND exchange_rate > 0 AND reporting_amount >= 0",
            name="ck_profit_snapshot_transaction_inputs_values",
        ),
    )
    _indexes(
        "profit_snapshot_transaction_inputs",
        [
            ("ix_profit_snapshot_transaction_inputs_org_id", ["organization_id"], False),
            ("ix_profit_snapshot_transaction_inputs_shop_id", ["shop_id"], False),
        ],
    )


def downgrade() -> None:
    op.drop_table("profit_snapshot_transaction_inputs")
    op.drop_table("profit_snapshot_settlement_inputs")
    op.drop_table("profit_snapshot_refund_inputs")
    op.drop_table("profit_snapshot_cost_inputs")
    op.drop_table("profit_snapshots")
    op.drop_table("finance_transaction_source_events")
    op.drop_table("finance_transactions")
    op.drop_table("settlement_source_events")
    op.drop_table("settlements")
    op.drop_table("refund_source_events")
    op.drop_table("refund_items")
    op.drop_table("refunds")
    op.drop_table("sku_costs")
    _ensure_index(
        "commerce_order_items",
        "ix_commerce_order_items_org_shop_order_fk",
        ["organization_id", "shop_id", "order_id"],
    )
    op.drop_index(
        "ix_commerce_order_items_org_shop_order_id_sku_unique",
        table_name="commerce_order_items",
    )
