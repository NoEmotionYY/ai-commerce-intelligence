"""Add a non-reversible public identifier lookup for verified platform callbacks."""

import sqlalchemy as sa

from alembic import op

revision = "0014_douyin_webhook_lookup"
down_revision = "0013_data_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shop_credentials",
        sa.Column("public_identifier_hash", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_shop_credentials_public_identifier_hash",
        "shop_credentials",
        ["public_identifier_hash"],
    )
    op.create_table(
        "platform_sku_source_events",
        sa.Column("platform_sku_id", sa.Integer(), primary_key=True),
        sa.Column("raw_event_id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("master_sku_id", sa.Integer(), nullable=False),
        sa.Column("normalized_hash", sa.String(64), nullable=False),
        sa.Column("source_occurred_at", sa.DateTime(), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
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
            ["organization_id", "shop_id", "raw_event_id"],
            [
                "platform_raw_events.organization_id",
                "platform_raw_events.shop_id",
                "platform_raw_events.id",
            ],
        ),
    )
    for name, columns in [
        ("ix_platform_sku_source_events_organization_id", ["organization_id"]),
        ("ix_platform_sku_source_events_shop_id", ["shop_id"]),
        ("ix_platform_sku_source_events_master_sku_id", ["master_sku_id"]),
        ("ix_platform_sku_source_events_raw_event_id", ["raw_event_id"]),
        ("ix_platform_sku_source_events_source_occurred_at", ["source_occurred_at"]),
    ]:
        op.create_index(name, "platform_sku_source_events", columns)


def downgrade() -> None:
    op.drop_table("platform_sku_source_events")
    op.drop_index(
        "ix_shop_credentials_public_identifier_hash",
        table_name="shop_credentials",
    )
    op.drop_column("shop_credentials", "public_identifier_hash")
