"""Wire models for the existing brand and campaign lists and new render summaries."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.creative.schemas import CampaignBrief

RenderStatus = Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED"]


class Page[T](BaseModel):
    items: list[T]
    limit: int
    offset: int


class BrandProfileStatus(StrEnum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"


class BrandRecord(BaseModel):
    id: UUID
    name: str | None
    website_url: str
    revision: int
    created_at: datetime
    profile_status: BrandProfileStatus | None
    offerings: list[dict]


class CampaignRecord(BaseModel):
    id: UUID
    brand_id: UUID
    revision: int
    brief: CampaignBrief
    created_at: datetime
    updated_at: datetime


class RenderRecord(BaseModel):
    id: UUID
    spec_id: UUID
    spec_revision: int
    status: RenderStatus
    concept_title: str
    duration_seconds: float
    preview_url: str | None
    created_at: datetime
