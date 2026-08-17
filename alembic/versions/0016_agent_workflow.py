"""Bind Agent draft requests and BusinessTasks to one purchase workflow."""

import sqlalchemy as sa

from alembic import op

revision = "0016_agent_workflow"
down_revision = "0015_task_effects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # Native ADD COLUMN preserves child rows. Alembic's SQLite batch rebuild would
        # drop the parent table and cascade-delete historical task/effect evidence.
        op.execute(
            "ALTER TABLE business_tasks ADD COLUMN execution_purchase_order_id "
            "INTEGER REFERENCES commerce_purchase_orders(id) ON DELETE RESTRICT"
        )
        op.execute(
            "CREATE TRIGGER ck_business_tasks_execution_po_org_insert "
            "BEFORE INSERT ON business_tasks "
            "WHEN NEW.execution_purchase_order_id IS NOT NULL AND NOT EXISTS ("
            "SELECT 1 FROM commerce_purchase_orders po WHERE "
            "po.id = NEW.execution_purchase_order_id AND "
            "po.organization_id = NEW.organization_id) "
            "BEGIN SELECT RAISE(ABORT, 'business task purchase order tenant mismatch'); END"
        )
        op.execute(
            "CREATE TRIGGER ck_business_tasks_execution_po_org_update "
            "BEFORE UPDATE OF organization_id, execution_purchase_order_id ON business_tasks "
            "WHEN NEW.execution_purchase_order_id IS NOT NULL AND NOT EXISTS ("
            "SELECT 1 FROM commerce_purchase_orders po WHERE "
            "po.id = NEW.execution_purchase_order_id AND "
            "po.organization_id = NEW.organization_id) "
            "BEGIN SELECT RAISE(ABORT, 'business task purchase order tenant mismatch'); END"
        )
        op.execute(
            "CREATE TRIGGER ck_business_tasks_execution_po_delete "
            "BEFORE DELETE ON commerce_purchase_orders "
            "WHEN EXISTS (SELECT 1 FROM business_tasks task WHERE "
            "task.execution_purchase_order_id = OLD.id) "
            "BEGIN SELECT RAISE(ABORT, 'purchase order is linked to a business task'); END"
        )
        op.execute(
            "CREATE TRIGGER ck_commerce_purchase_orders_business_task_org_update "
            "BEFORE UPDATE OF organization_id ON commerce_purchase_orders "
            "WHEN EXISTS (SELECT 1 FROM business_tasks task WHERE "
            "task.execution_purchase_order_id = OLD.id AND "
            "task.organization_id != NEW.organization_id) "
            "BEGIN SELECT RAISE(ABORT, 'purchase order business task tenant mismatch'); END"
        )
    else:
        op.add_column(
            "business_tasks",
            sa.Column("execution_purchase_order_id", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            "fk_business_tasks_execution_purchase_order",
            "business_tasks",
            "commerce_purchase_orders",
            ["organization_id", "execution_purchase_order_id"],
            ["organization_id", "id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "ix_business_tasks_execution_purchase_order_id",
        "business_tasks",
        ["execution_purchase_order_id"],
    )
    op.execute(
        "UPDATE business_tasks SET execution_purchase_order_id = "
        "(SELECT task_effect_measurements.execution_purchase_order_id "
        "FROM task_effect_measurements WHERE "
        "task_effect_measurements.business_task_id = business_tasks.id) "
        "WHERE EXISTS (SELECT 1 FROM task_effect_measurements WHERE "
        "task_effect_measurements.business_task_id = business_tasks.id)"
    )

    op.create_table(
        "agent_draft_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key_hash",
            name="uq_agent_draft_requests_org_idempotency",
        ),
        sa.CheckConstraint(
            "action IN ('CREATE_BUSINESS_TASK','CREATE_PURCHASE_DRAFT')",
            name="ck_agent_draft_requests_action",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','SUCCESS','FAILED')",
            name="ck_agent_draft_requests_status",
        ),
    )
    op.create_index(
        "ix_agent_draft_requests_organization_id",
        "agent_draft_requests",
        ["organization_id"],
    )
    op.create_index(
        "ix_agent_draft_requests_org_id_unique",
        "agent_draft_requests",
        ["organization_id", "id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("agent_draft_requests")
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER ck_commerce_purchase_orders_business_task_org_update")
        op.execute("DROP TRIGGER ck_business_tasks_execution_po_delete")
        op.execute("DROP TRIGGER ck_business_tasks_execution_po_org_update")
        op.execute("DROP TRIGGER ck_business_tasks_execution_po_org_insert")
    op.drop_index("ix_business_tasks_execution_purchase_order_id", table_name="business_tasks")
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(
            "fk_business_tasks_execution_purchase_order",
            "business_tasks",
            type_="foreignkey",
        )
    op.drop_column("business_tasks", "execution_purchase_order_id")
