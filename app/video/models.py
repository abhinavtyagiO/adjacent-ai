"""Database records for private media, video instructions and completed renders."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import now


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        Index(
            "uq_media_content",
            "brand_id",
            "content_hash",
            "source_type",
            "purpose",
            "source_identity",
            unique=True,
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    brand_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("brands.id", ondelete="CASCADE"), index=True
    )
    media_type: Mapped[str] = mapped_column(String(8))
    source_type: Mapped[str] = mapped_column(String(16))
    source_identity: Mapped[str] = mapped_column(String(120), default="")
    purpose: Mapped[str] = mapped_column(String(16))
    content_hash: Mapped[str] = mapped_column(String(64))
    original_key: Mapped[str] = mapped_column(String(512))
    normalized_key: Mapped[str | None] = mapped_column(String(512))
    thumbnail_key: Mapped[str] = mapped_column(String(512))
    details: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="READY")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class VideoSpec(Base):
    __tablename__ = "video_specs"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    concept_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("creative_concepts.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RenderJob(Base):
    __tablename__ = "render_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')", name="render_job_status"
        ),
        Index(
            "uq_active_render",
            "spec_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING','RUNNING')"),
        ),
        Index("ix_render_claim", "status", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    spec_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("video_specs.id", ondelete="CASCADE"), index=True
    )
    spec_revision: Mapped[int] = mapped_column(Integer)
    inputs: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    diagnostics: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VideoArtifact(Base):
    __tablename__ = "video_artifacts"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("render_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    output_key: Mapped[str] = mapped_column(String(512))
    thumbnail_key: Mapped[str] = mapped_column(String(512))
    metadata_json: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MediaSearchCache(Base):
    """Shared Pixabay results and request limits survive worker restarts."""

    __tablename__ = "media_search_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
