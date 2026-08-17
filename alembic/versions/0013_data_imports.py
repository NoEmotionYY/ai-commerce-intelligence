"""Add tenant-scoped CSV/XLSX import jobs and record lineage."""

import sqlalchemy as sa

from alembic import op

revision = "0013_data_imports"
down_revision = "0012_alert_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_import_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("import_type", sa.String(16), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_format", sa.String(8), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("mapping", sa.JSON(), nullable=False),
        sa.Column("mapping_hash", sa.String(64), nullable=False),
        sa.Column("source_identity_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("total_records", sa.Integer(), nullable=False),
        sa.Column("valid_records", sa.Integer(), nullable=False),
        sa.Column("invalid_records", sa.Integer(), nullable=False),
        sa.Column("processed_records", sa.Integer(), nullable=False),
        sa.Column("failed_records", sa.Integer(), nullable=False),
        sa.Column("execution_attempts", sa.Integer(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "organization_id", "idempotency_key_hash", name="uq_data_import_jobs_org_idempotency"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "shop_id",
            "source_identity_hash",
            name="uq_data_import_jobs_org_shop_source",
        ),
        sa.CheckConstraint(
            "import_type IN ('CATALOG','ORDER','INVENTORY','COST')",
            name="ck_data_import_jobs_type",
        ),
        sa.CheckConstraint(
            "status IN ('PREVIEWED','INVALID','RUNNING','SUCCESS','PARTIAL','FAILED')",
            name="ck_data_import_jobs_status",
        ),
        sa.CheckConstraint(
            "total_records >= 0 AND valid_records >= 0 AND invalid_records >= 0 "
            "AND processed_records >= 0 AND failed_records >= 0 AND execution_attempts >= 0",
            name="ck_data_import_jobs_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "valid_records + invalid_records = total_records",
            name="ck_data_import_jobs_preview_counts",
        ),
        sa.CheckConstraint(
            "processed_records + failed_records <= valid_records",
            name="ck_data_import_jobs_execution_counts",
        ),
        sa.CheckConstraint(
            "file_format IN ('csv','xlsx')",
            name="ck_data_import_jobs_file_format",
        ),
        sa.CheckConstraint(
            "status != 'SUCCESS' OR (processed_records = valid_records "
            "AND failed_records = 0 AND invalid_records = 0)",
            name="ck_data_import_jobs_success_counts",
        ),
        sa.CheckConstraint(
            "status != 'PARTIAL' OR (processed_records > 0 "
            "AND processed_records + failed_records = valid_records "
            "AND (failed_records > 0 OR invalid_records > 0))",
            name="ck_data_import_jobs_partial_counts",
        ),
        sa.CheckConstraint(
            "status NOT IN ('PREVIEWED','INVALID') "
            "OR (processed_records = 0 AND failed_records = 0)",
            name="ck_data_import_jobs_preview_execution_counts",
        ),
    )
    for name, columns in [
        ("ix_data_import_jobs_organization_id", ["organization_id"]),
        ("ix_data_import_jobs_shop_id", ["shop_id"]),
        ("ix_data_import_jobs_import_type", ["import_type"]),
        ("ix_data_import_jobs_status", ["status"]),
        ("ix_data_import_jobs_created_by_user_id", ["created_by_user_id"]),
    ]:
        op.create_index(name, "data_import_jobs", columns)
    op.create_index(
        "ix_data_import_jobs_org_shop_id_unique",
        "data_import_jobs",
        ["organization_id", "shop_id", "id"],
        unique=True,
    )

    op.create_table(
        "data_import_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("import_job_id", sa.Integer(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("record_key", sa.String(64), nullable=False),
        sa.Column("raw_values", sa.JSON(), nullable=False),
        sa.Column("normalized_payload", sa.JSON(), nullable=True),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("raw_event_id", sa.Integer(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "shop_id", "import_job_id"],
            [
                "data_import_jobs.organization_id",
                "data_import_jobs.shop_id",
                "data_import_jobs.id",
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
        sa.UniqueConstraint("import_job_id", "record_key", name="uq_data_import_records_job_key"),
        sa.UniqueConstraint("raw_event_id", name="uq_data_import_records_raw_event"),
        sa.CheckConstraint(sa.column("row_number") >= 2, name="ck_data_import_records_row_number"),
        sa.CheckConstraint(
            "status IN ('PENDING','VALID','INVALID','PROCESSING','SUCCESS','FAILED')",
            name="ck_data_import_records_status",
        ),
    )
    for name, columns in [
        ("ix_data_import_records_organization_id", ["organization_id"]),
        ("ix_data_import_records_shop_id", ["shop_id"]),
        ("ix_data_import_records_import_job_id", ["import_job_id"]),
        ("ix_data_import_records_status", ["status"]),
        ("ix_data_import_records_raw_event_id", ["raw_event_id"]),
    ]:
        op.create_index(name, "data_import_records", columns)
    op.create_index(
        "ix_data_import_records_org_job_id_unique",
        "data_import_records",
        ["organization_id", "import_job_id", "id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("data_import_records")
    op.drop_table("data_import_jobs")
