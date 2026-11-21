"""Persist two-stage pipeline diagnostics and structured source context.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    for table, column in [
        ("profiles", "pipeline_metadata"),
        ("extraction_jobs", "metrics"),
        ("source_documents", "source_metadata"),
    ]:
        op.add_column(
            table,
            sa.Column(
                column, postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
            ),
        )
    op.add_column(
        "extraction_jobs", sa.Column("selected_offering_key", sa.String(300), nullable=True)
    )


def downgrade():
    op.drop_column("extraction_jobs", "selected_offering_key")
    for table, column in [
        ("source_documents", "source_metadata"),
        ("extraction_jobs", "metrics"),
        ("profiles", "pipeline_metadata"),
    ]:
        op.drop_column(table, column)
