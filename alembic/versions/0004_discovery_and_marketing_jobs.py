"""Separate deterministic discovery and snapshot-bound marketing jobs.

Revision ID: 0004
Revises: 0003
All existing profiles/jobs remain readable; source binding for legacy profiles is resolved
read-only from successful extraction timestamps when required, not fabricated in migration.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("job_status", "extraction_jobs", type_="check")
    op.create_check_constraint(
        "job_status",
        "extraction_jobs",
        "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','INSUFFICIENT_DATA')",
    )
    op.add_column(
        "extraction_jobs",
        sa.Column("kind", sa.String(20), nullable=False, server_default="EXTRACTION"),
    )
    op.create_check_constraint("job_kind", "extraction_jobs", "kind IN ('EXTRACTION','MARKETING')")
    op.add_column("extraction_jobs", sa.Column("source_job_id", pg.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_job_source",
        "extraction_jobs",
        "extraction_jobs",
        ["source_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "extraction_jobs",
        sa.Column(
            "replace_reviewed", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column(
        "extraction_jobs",
        sa.Column("diagnostics", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("profiles", sa.Column("source_job_id", pg.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_profile_source",
        "profiles",
        "extraction_jobs",
        ["source_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for table, key, index in [
        ("catalog_products", "product_key", "uq_catalog_product"),
        ("customer_concerns", "concern_key", "uq_customer_concern"),
    ]:
        columns = [
            sa.Column("id", pg.UUID(), primary_key=True),
            sa.Column(
                "brand_id",
                pg.UUID(),
                sa.ForeignKey("brands.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(key, sa.String(2048 if key == "product_key" else 300), nullable=False),
            sa.Column(
                "source_job_id",
                pg.UUID(),
                sa.ForeignKey("extraction_jobs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("details", pg.JSONB(), nullable=False),
        ]
        if table == "catalog_products":
            columns.append(
                sa.Column(
                    "offering_id",
                    pg.UUID(),
                    sa.ForeignKey("offerings.id", ondelete="SET NULL"),
                    nullable=True,
                )
            )
        op.create_table(table, *columns)
        op.create_index(index, table, ["brand_id", key], unique=True)
        op.create_index("ix_" + table + "_brand_id", table, ["brand_id"])


def downgrade():
    op.drop_table("customer_concerns")
    op.drop_table("catalog_products")
    op.drop_constraint("fk_profile_source", "profiles", type_="foreignkey")
    op.drop_column("profiles", "source_job_id")
    op.drop_constraint("fk_job_source", "extraction_jobs", type_="foreignkey")
    op.drop_constraint("job_kind", "extraction_jobs", type_="check")
    for column in ("diagnostics", "replace_reviewed", "source_job_id", "kind"):
        op.drop_column("extraction_jobs", column)
    # An old binary cannot interpret this terminal result; retain it as a terminal failure.
    op.execute("UPDATE extraction_jobs SET status='FAILED' WHERE status='INSUFFICIENT_DATA'")
    op.drop_constraint("job_status", "extraction_jobs", type_="check")
    op.create_check_constraint(
        "job_status", "extraction_jobs", "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')"
    )
