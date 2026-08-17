"""创建初始数据库结构。"""

from alembic import op
from commerce import models  # noqa: F401
from commerce.database import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

# Historical migration boundary. V2 tables must be introduced by their own explicit
# revisions rather than being pulled into this legacy metadata operation.
LEGACY_TABLES = (
    "products",
    "orders",
    "order_items",
    "inventory",
    "advertising",
    "purchase_orders",
    "operation_logs",
    "competitor_products",
    "competitor_price_history",
    "competitor_contents",
    "competitor_comments",
    "crawler_tasks",
    "agent_sessions",
    "approval_tasks",
    "workflow_checkpoints",
)


def _legacy_tables() -> list[object]:
    return [Base.metadata.tables[name] for name in LEGACY_TABLES]


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), tables=_legacy_tables())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), tables=_legacy_tables())
