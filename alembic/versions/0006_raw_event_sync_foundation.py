"""Add tenant-scoped raw platform events and synchronization jobs."""

import sqlalchemy as sa

from alembic import op

revision = "0006_raw_event_sync_foundation"
down_revision = "0005_unified_catalog_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("checkpoint", sa.JSON(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("lease_token_hash", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
        ),
        sa.UniqueConstraint(
            "shop_id",
            "job_type",
            "idempotency_key_hash",
            name="uq_sync_jobs_shop_type_key_hash",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 10", name="ck_sync_jobs_max_attempts"
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND attempts <= max_attempts", name="ck_sync_jobs_attempts"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCESS','PARTIAL','FAILED')",
            name="ck_sync_jobs_status",
        ),
    )
    op.create_index("ix_sync_jobs_organization_id", "sync_jobs", ["organization_id"])
    op.create_index("ix_sync_jobs_shop_id", "sync_jobs", ["shop_id"])
    op.create_index("ix_sync_jobs_job_type", "sync_jobs", ["job_type"])
    op.create_index("ix_sync_jobs_status", "sync_jobs", ["status"])
    op.create_index(
        "ix_sync_jobs_org_shop_id_unique",
        "sync_jobs",
        ["organization_id", "shop_id", "id"],
        unique=True,
    )

    op.create_table(
        "platform_raw_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("external_event_id", sa.String(length=256), nullable=False),
        sa.Column("source_event_key", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="RECEIVED"),
        sa.Column("processing_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("replay_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("processing_token_hash", sa.String(length=64), nullable=True),
        sa.Column("processing_lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
        ),
        sa.UniqueConstraint("shop_id", "source_event_key", name="uq_raw_events_shop_source_key"),
        sa.CheckConstraint("processing_attempts >= 0", name="ck_raw_events_processing_attempts"),
        sa.CheckConstraint("replay_count >= 0", name="ck_raw_events_replay_count"),
        sa.CheckConstraint(
            "status IN ('RECEIVED','PROCESSING','PROCESSED','FAILED')",
            name="ck_raw_events_status",
        ),
    )
    op.create_index(
        "ix_platform_raw_events_organization_id", "platform_raw_events", ["organization_id"]
    )
    op.create_index("ix_platform_raw_events_shop_id", "platform_raw_events", ["shop_id"])
    op.create_index("ix_platform_raw_events_platform", "platform_raw_events", ["platform"])
    op.create_index("ix_platform_raw_events_event_type", "platform_raw_events", ["event_type"])
    op.create_index("ix_platform_raw_events_status", "platform_raw_events", ["status"])
    op.create_index(
        "ix_raw_events_org_shop_id_unique",
        "platform_raw_events",
        ["organization_id", "shop_id", "id"],
        unique=True,
    )

    op.create_table(
        "sync_job_raw_events",
        sa.Column("sync_job_id", sa.Integer(), primary_key=True),
        sa.Column("raw_event_id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "sync_job_id"],
            ["sync_jobs.organization_id", "sync_jobs.shop_id", "sync_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("observation_count >= 1", name="ck_sync_job_raw_events_count"),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "raw_event_id"],
            [
                "platform_raw_events.organization_id",
                "platform_raw_events.shop_id",
                "platform_raw_events.id",
            ],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_sync_job_raw_events_organization_id",
        "sync_job_raw_events",
        ["organization_id"],
    )
    op.create_index("ix_sync_job_raw_events_shop_id", "sync_job_raw_events", ["shop_id"])


def downgrade() -> None:
    op.drop_table("sync_job_raw_events")
    op.drop_table("platform_raw_events")
    op.drop_table("sync_jobs")
