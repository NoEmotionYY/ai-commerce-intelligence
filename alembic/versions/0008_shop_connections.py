"""Add tenant-scoped shop connection and capability state."""

import sqlalchemy as sa

from alembic import op

revision = "0008_shop_connections"
down_revision = "0007_unified_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shop_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column(
            "authorization_status",
            sa.String(length=24),
            nullable=False,
            server_default="NOT_CONFIGURED",
        ),
        sa.Column("authorization_error_code", sa.String(length=100), nullable=True),
        sa.Column("authorization_verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("shop_id", name="uq_shop_connections_shop"),
        sa.CheckConstraint(
            "authorization_status IN "
            "('NOT_CONFIGURED','CONFIGURED','AUTHORIZED','REAUTH_REQUIRED','REVOKED')",
            name="ck_shop_connections_authorization_status",
        ),
    )
    op.create_index("ix_shop_connections_organization_id", "shop_connections", ["organization_id"])
    op.create_index("ix_shop_connections_shop_id", "shop_connections", ["shop_id"])
    op.create_index(
        "ix_shop_connections_authorization_status",
        "shop_connections",
        ["authorization_status"],
    )

    op.create_table(
        "shop_capabilities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="DISABLED"),
        sa.Column("required_credential_type", sa.String(length=50), nullable=True),
        sa.Column("granted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("shop_id", "code", name="uq_shop_capabilities_shop_code"),
        sa.CheckConstraint("status IN ('ENABLED','DISABLED')", name="ck_shop_capabilities_status"),
    )
    op.create_index(
        "ix_shop_capabilities_organization_id", "shop_capabilities", ["organization_id"]
    )
    op.create_index("ix_shop_capabilities_shop_id", "shop_capabilities", ["shop_id"])
    op.create_index("ix_shop_capabilities_code", "shop_capabilities", ["code"])
    op.create_index("ix_shop_capabilities_status", "shop_capabilities", ["status"])

    op.add_column(
        "sync_jobs", sa.Column("required_capability", sa.String(length=64), nullable=True)
    )
    op.create_index("ix_sync_jobs_required_capability", "sync_jobs", ["required_capability"])

    op.execute(
        sa.text(
            """
            INSERT INTO shop_connections (
                organization_id,
                shop_id,
                authorization_status,
                authorization_error_code,
                authorization_verified_at,
                created_at,
                updated_at
            )
            SELECT
                organization_id,
                id,
                'REAUTH_REQUIRED',
                'LEGACY_REAUTH_REQUIRED',
                NULL,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM shops
            WHERE status = 'REAUTH_REQUIRED'
            """
        )
    )
    op.execute(sa.text("UPDATE shops SET status = 'DISABLED' WHERE status = 'REAUTH_REQUIRED'"))


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE shops
            SET status = 'REAUTH_REQUIRED'
            WHERE status = 'DISABLED'
              AND EXISTS (
                  SELECT 1
                  FROM shop_connections
                  WHERE shop_connections.shop_id = shops.id
                    AND shop_connections.organization_id = shops.organization_id
                    AND shop_connections.authorization_status = 'REAUTH_REQUIRED'
                    AND shop_connections.authorization_error_code = 'LEGACY_REAUTH_REQUIRED'
              )
            """
        )
    )
    op.drop_index("ix_sync_jobs_required_capability", table_name="sync_jobs")
    op.drop_column("sync_jobs", "required_capability")
    op.drop_table("shop_capabilities")
    op.drop_table("shop_connections")
