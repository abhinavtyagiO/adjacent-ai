"""campaigns research snapshots and creative jobs

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("brand_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("brief", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["brand_id"], ["brands.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_campaigns_brand_id"), "campaigns", ["brand_id"], unique=False)
    op.create_table(
        "research_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("brand_revision", sa.Integer(), nullable=False),
        sa.Column("campaign_revision", sa.Integer(), nullable=False),
        sa.Column("source_job_id", sa.UUID(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_job_id"], ["extraction_jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_research_snapshots_campaign_id"),
        "research_snapshots",
        ["campaign_id"],
        unique=False,
    )
    op.create_table(
        "creative_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("research_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.UUID(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("chatgpt_account_id", sa.UUID(), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("diagnostics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','PARTIAL','FAILED','INSUFFICIENT_DATA')",
            name="creative_job_status",
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["research_id"], ["research_snapshots.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_creative_claim", "creative_jobs", ["status", "created_at"], unique=False)
    op.create_index(
        op.f("ix_creative_jobs_campaign_id"), "creative_jobs", ["campaign_id"], unique=False
    )
    op.create_index(
        "uq_active_creative_job",
        "creative_jobs",
        ["campaign_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING','RUNNING')"),
    )
    op.create_table(
        "creative_concepts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("candidate_key", sa.String(length=50), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("original_content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("validation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evaluation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("shortlisted", sa.Boolean(), nullable=False),
        sa.Column("edited_by_user", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("review", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('DRAFT','SELECTED','APPROVED')", name="creative_concept_state"
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["creative_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_creative_concepts_campaign_id"), "creative_concepts", ["campaign_id"], unique=False
    )
    op.create_index(
        op.f("ix_creative_concepts_job_id"), "creative_concepts", ["job_id"], unique=False
    )
    op.create_index(
        "uq_creative_candidate", "creative_concepts", ["job_id", "candidate_key"], unique=True
    )
    op.create_index(
        "uq_selected_creative",
        "creative_concepts",
        ["campaign_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('SELECTED','APPROVED')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_selected_creative",
        table_name="creative_concepts",
        postgresql_where=sa.text("state IN ('SELECTED','APPROVED')"),
    )
    op.drop_index("uq_creative_candidate", table_name="creative_concepts")
    op.drop_index(op.f("ix_creative_concepts_job_id"), table_name="creative_concepts")
    op.drop_index(op.f("ix_creative_concepts_campaign_id"), table_name="creative_concepts")
    op.drop_table("creative_concepts")
    op.drop_index(
        "uq_active_creative_job",
        table_name="creative_jobs",
        postgresql_where=sa.text("status IN ('PENDING','RUNNING')"),
    )
    op.drop_index(op.f("ix_creative_jobs_campaign_id"), table_name="creative_jobs")
    op.drop_index("ix_creative_claim", table_name="creative_jobs")
    op.drop_table("creative_jobs")
    op.drop_index(op.f("ix_research_snapshots_campaign_id"), table_name="research_snapshots")
    op.drop_table("research_snapshots")
    op.drop_index(op.f("ix_campaigns_brand_id"), table_name="campaigns")
    op.drop_table("campaigns")
