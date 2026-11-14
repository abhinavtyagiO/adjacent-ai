"""Bind queued extraction to a local ChatGPT account and model selection.

Revision ID: 0003
Revises: 0002
Tokens are stored only in protected runtime storage, never job records.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "extraction_jobs", sa.Column("chatgpt_account_id", postgresql.UUID(), nullable=True)
    )
    op.add_column("extraction_jobs", sa.Column("factual_model", sa.String(200), nullable=True))
    op.add_column("extraction_jobs", sa.Column("marketing_model", sa.String(200), nullable=True))


def downgrade():
    for name in ["marketing_model", "factual_model", "chatgpt_account_id"]:
        op.drop_column("extraction_jobs", name)
