"""Add deterministic before/after measurements for completed business tasks."""

import sqlalchemy as sa

from alembic import op

revision = "0015_task_effects"
down_revision = "0014_douyin_webhook_lookup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_effect_measurements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("business_task_id", sa.Integer(), nullable=False),
        sa.Column("alert_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=True),
        sa.Column("master_sku_id", sa.Integer(), nullable=True),
        sa.Column("execution_purchase_order_id", sa.Integer(), nullable=False),
        sa.Column("execution_status", sa.String(24), nullable=False),
        sa.Column("executed_at", sa.DateTime(), nullable=False),
        sa.Column("metric_name", sa.String(64), nullable=False),
        sa.Column("metric_unit", sa.String(24), nullable=False),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("profit_kind", sa.String(12), nullable=True),
        sa.Column("direction", sa.String(24), nullable=False),
        sa.Column("baseline_value", sa.Numeric(24, 10), nullable=False),
        sa.Column("outcome_value", sa.Numeric(24, 10), nullable=False),
        sa.Column("delta_value", sa.Numeric(24, 10), nullable=False),
        sa.Column("assessment", sa.String(16), nullable=False),
        sa.Column("baseline_window_start", sa.DateTime(), nullable=False),
        sa.Column("baseline_window_end", sa.DateTime(), nullable=False),
        sa.Column("outcome_window_start", sa.DateTime(), nullable=False),
        sa.Column("outcome_window_end", sa.DateTime(), nullable=False),
        sa.Column("method_version", sa.String(64), nullable=False),
        sa.Column("calculation_hash", sa.String(64), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("measured_by_user_id", sa.Integer(), nullable=False),
        sa.Column("measured_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "business_task_id"],
            ["business_tasks.organization_id", "business_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "alert_id"],
            ["commerce_alerts.organization_id", "commerce_alerts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"], ["shops.organization_id", "shops.id"]
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "master_sku_id"],
            ["master_skus.organization_id", "master_skus.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "execution_purchase_order_id"],
            ["commerce_purchase_orders.organization_id", "commerce_purchase_orders.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["measured_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("business_task_id", name="uq_task_effect_measurements_task"),
        sa.UniqueConstraint("calculation_hash", name="uq_task_effect_measurements_hash"),
        sa.CheckConstraint(
            "baseline_value >= 0 AND outcome_value >= 0",
            name="ck_task_effect_measurements_values",
        ),
        sa.CheckConstraint(
            "baseline_window_start < baseline_window_end AND "
            "outcome_window_start < outcome_window_end",
            name="ck_task_effect_measurements_windows",
        ),
        sa.CheckConstraint(
            "direction IN ('HIGHER_IS_BETTER','LOWER_IS_BETTER')",
            name="ck_task_effect_measurements_direction",
        ),
        sa.CheckConstraint(
            "assessment IN ('IMPROVED','UNCHANGED','WORSENED')",
            name="ck_task_effect_measurements_assessment",
        ),
        sa.CheckConstraint(
            "profit_kind IS NULL OR profit_kind IN ('ESTIMATED','SETTLED')",
            name="ck_task_effect_measurements_profit_kind",
        ),
        sa.CheckConstraint(
            "execution_status IN ('ORDERED','SHIPPED','RECEIVED','CLOSED')",
            name="ck_task_effect_measurements_execution_status",
        ),
    )
    for name, columns in [
        ("ix_task_effect_measurements_organization_id", ["organization_id"]),
        ("ix_task_effect_measurements_business_task_id", ["business_task_id"]),
        ("ix_task_effect_measurements_alert_id", ["alert_id"]),
        ("ix_task_effect_measurements_shop_id", ["shop_id"]),
        ("ix_task_effect_measurements_master_sku_id", ["master_sku_id"]),
        (
            "ix_task_effect_measurements_execution_purchase_order_id",
            ["execution_purchase_order_id"],
        ),
        ("ix_task_effect_measurements_measured_by_user_id", ["measured_by_user_id"]),
        ("ix_task_effect_measurements_measured_at", ["measured_at"]),
    ]:
        op.create_index(name, "task_effect_measurements", columns)
    op.create_index(
        "ix_task_effect_measurements_org_id_unique",
        "task_effect_measurements",
        ["organization_id", "id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("task_effect_measurements")
