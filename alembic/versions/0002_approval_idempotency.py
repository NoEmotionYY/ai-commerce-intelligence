"""为采购审批增加业务请求幂等键。"""

import sqlalchemy as sa

from alembic import op

revision = "0002_approval_idempotency"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("approval_tasks")}
    if "idempotency_key" not in columns:
        op.add_column(
            "approval_tasks",
            sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        )
    op.execute(
        "UPDATE approval_tasks "
        "SET idempotency_key = CONCAT('legacy-approval-', id) "
        "WHERE idempotency_key IS NULL"
    )
    column = next(
        item
        for item in sa.inspect(bind).get_columns("approval_tasks")
        if item["name"] == "idempotency_key"
    )
    if column["nullable"]:
        op.alter_column(
            "approval_tasks",
            "idempotency_key",
            existing_type=sa.String(length=128),
            nullable=False,
        )
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("approval_tasks")}
    if "ix_approval_tasks_idempotency_key" not in indexes:
        op.create_index(
            "ix_approval_tasks_idempotency_key",
            "approval_tasks",
            ["idempotency_key"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index("ix_approval_tasks_idempotency_key", table_name="approval_tasks")
    op.drop_column("approval_tasks", "idempotency_key")
