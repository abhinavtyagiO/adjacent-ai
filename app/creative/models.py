"""Append-only generations and immutable research; edits live on versioned concept rows."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import now


class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("brands.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    brief: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ResearchSnapshot(Base):
    __tablename__ = "research_snapshots"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    brand_revision: Mapped[int] = mapped_column(Integer)
    campaign_revision: Mapped[int] = mapped_column(Integer)
    source_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("extraction_jobs.id", ondelete="RESTRICT")
    )
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CreativeJob(Base):
    __tablename__ = "creative_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','PARTIAL','FAILED','INSUFFICIENT_DATA')",
            name="creative_job_status",
        ),
        Index(
            "uq_active_creative_job",
            "campaign_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING','RUNNING')"),
        ),
        Index("ix_creative_claim", "status", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    research_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("research_snapshots.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chatgpt_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    model: Mapped[str] = mapped_column(String(200))
    diagnostics: Mapped[dict] = mapped_column(JSONB, default=dict)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CreativeConcept(Base):
    __tablename__ = "creative_concepts"
    __table_args__ = (
        CheckConstraint("state IN ('DRAFT','SELECTED','APPROVED')", name="creative_concept_state"),
        Index("uq_creative_candidate", "job_id", "candidate_key", unique=True),
        Index(
            "uq_selected_creative",
            "campaign_id",
            unique=True,
            postgresql_where=text("state IN ('SELECTED','APPROVED')"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("creative_jobs.id", ondelete="CASCADE"), index=True
    )
    candidate_key: Mapped[str] = mapped_column(String(50))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[dict] = mapped_column(JSONB)
    original_content: Mapped[dict] = mapped_column(JSONB)
    validation: Mapped[dict] = mapped_column(JSONB)
    evaluation: Mapped[dict | None] = mapped_column(JSONB)
    shortlisted: Mapped[bool] = mapped_column(Boolean, default=False)
    edited_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(20), default="DRAFT")
    review: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
