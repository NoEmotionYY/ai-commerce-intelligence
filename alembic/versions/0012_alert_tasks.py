"""Add tenant-scoped commerce alerts and business tasks."""

import sqlalchemy as sa

from alembic import op

revision = "0012_alert_tasks"
down_revision = "0011_purchasing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "commerce_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=True),
        sa.Column("master_sku_id", sa.Integer(), nullable=True),
        sa.Column("alert_type", sa.String(24), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("deduplication_key_hash", sa.String(64), nullable=False),
        sa.Column("metric_name", sa.String(64), nullable=False),
        sa.Column("metric_value", sa.Numeric(24, 10), nullable=False),
        sa.Column("threshold_value", sa.Numeric(24, 10), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("window_start", sa.DateTime(), nullable=False),
        sa.Column("window_end", sa.DateTime(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"], ["shops.organization_id", "shops.id"]
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "master_sku_id"],
            ["master_skus.organization_id", "master_skus.id"],
        ),
        sa.UniqueConstraint(
            "organization_id", "deduplication_key_hash", name="uq_commerce_alerts_org_dedup"
        ),
        sa.CheckConstraint("metric_value >= 0 AND threshold_value >= 0", name="ck_alert_metrics"),
        sa.CheckConstraint(
            "alert_type IN ('SALES_DROP','SALES_SPIKE','STOCKOUT_RISK','REFUND_SPIKE',"
            "'MARGIN_DROP','PRICE_ANOMALY','ORDER_ANOMALY','FINANCE_ANOMALY')",
            name="ck_commerce_alerts_type",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','ACKNOWLEDGED','RESOLVED','DISMISSED')",
            name="ck_commerce_alerts_status",
        ),
        sa.CheckConstraint("window_start < window_end", name="ck_commerce_alerts_window"),
    )
    op.create_index("ix_commerce_alerts_organization_id", "commerce_alerts", ["organization_id"])
    op.create_index("ix_commerce_alerts_shop_id", "commerce_alerts", ["shop_id"])
    op.create_index("ix_commerce_alerts_master_sku_id", "commerce_alerts", ["master_sku_id"])
    op.create_index("ix_commerce_alerts_alert_type", "commerce_alerts", ["alert_type"])
    op.create_index("ix_commerce_alerts_status", "commerce_alerts", ["status"])
    op.create_index("ix_commerce_alerts_window_end", "commerce_alerts", ["window_end"])
    op.create_index(
        "ix_commerce_alerts_org_id_unique",
        "commerce_alerts",
        ["organization_id", "id"],
        unique=True,
    )

    op.create_table(
        "business_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("alert_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=True),
        sa.Column("master_sku_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("assigned_to_user_id", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
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
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_to_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "organization_id", "idempotency_key_hash", name="uq_business_tasks_org_idempotency"
        ),
        sa.CheckConstraint(
            "status IN ('TODO','IN_PROGRESS','WAITING_APPROVAL','DONE','DISMISSED')",
            name="ck_business_tasks_status",
        ),
    )
    for name, columns in [
        ("ix_business_tasks_organization_id", ["organization_id"]),
        ("ix_business_tasks_alert_id", ["alert_id"]),
        ("ix_business_tasks_shop_id", ["shop_id"]),
        ("ix_business_tasks_master_sku_id", ["master_sku_id"]),
        ("ix_business_tasks_status", ["status"]),
    ]:
        op.create_index(name, "business_tasks", columns)
    op.create_index(
        "ix_business_tasks_org_id_unique", "business_tasks", ["organization_id", "id"], unique=True
    )

    op.create_table(
        "business_task_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("business_task_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(24), nullable=True),
        sa.Column("to_status", sa.String(24), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "business_task_id"],
            ["business_tasks.organization_id", "business_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status IN "
            "('TODO','IN_PROGRESS','WAITING_APPROVAL','DONE','DISMISSED')",
            name="ck_business_task_history_from_status",
        ),
        sa.CheckConstraint(
            "to_status IN ('TODO','IN_PROGRESS','WAITING_APPROVAL','DONE','DISMISSED')",
            name="ck_business_task_history_to_status",
        ),
    )
    op.create_index(
        "ix_business_task_history_organization_id", "business_task_history", ["organization_id"]
    )
    op.create_index(
        "ix_business_task_history_business_task_id", "business_task_history", ["business_task_id"]
    )
    op.create_index(
        "ix_business_task_history_org_task",
        "business_task_history",
        ["organization_id", "business_task_id"],
    )


def downgrade() -> None:
    op.drop_table("business_task_history")
    op.drop_table("business_tasks")
    op.drop_table("commerce_alerts")
