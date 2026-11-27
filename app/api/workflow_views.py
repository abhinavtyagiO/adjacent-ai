"""OpenAPI descriptions for the JSON returned by existing workflow endpoints."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.api.read_models import BrandProfileStatus, Page
from app.creative.schemas import CampaignBrief, Candidate
from app.schemas import OfferingData
from app.video.schemas import VideoPlan

WORKSPACE_WORKFLOW = "workspace-workflow"


class RegisteredBrand(BaseModel):
    id: UUID
    website_url: str
    name: str | None
    revision: int
    created_at: datetime


class BrandDetail(RegisteredBrand):
    profile_status: BrandProfileStatus | None
    catalog: dict
    customer_concerns: list[dict]


class JobError(BaseModel):
    code: str
    message: str
    details: dict | None = None


class WorkflowJob(BaseModel):
    id: UUID
    status: str
    error: JobError | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    diagnostics: dict
    poll_url: str


class ExtractionRecord(WorkflowJob):
    brand_id: UUID
    kind: Literal["EXTRACTION", "MARKETING"]
    attempts: int
    metrics: dict
    source_job_id: UUID | None


class OfferingRecord(OfferingData):
    id: UUID


class Offerings(BaseModel):
    items: list[OfferingRecord]


class SourceRecord(BaseModel):
    id: UUID
    job_id: UUID
    source_type: str
    url: str
    title: str | None
    fetched_at: datetime
    content_hash: str


class Sources(BaseModel):
    items: list[SourceRecord]


class CatalogItem(BaseModel):
    id: UUID
    name: str
    url: str | None = None
    image_url: str | None = None
    categories: list[str]
    price: str | None = None
    currency: str | None = None
    linked_offering_key: str | None = None


class CatalogPage(Page[CatalogItem]):
    total: int
    available_categories: list[str]


class CreativeGeneration(WorkflowJob):
    campaign_id: UUID
    research_id: UUID
    model: str
    attempts: int
    metrics: dict


class ReviewFlag(BaseModel):
    code: str
    severity: str
    field: str | None = None
    message: str | None = None


class ConceptValidation(BaseModel):
    eligible: bool
    flags: list[ReviewFlag]


class ConceptReview(BaseModel):
    approved_brand_label: str | None = None
    human_review_completed: bool | None = None
    reviewed_at: datetime | None = None


class CaptionGrounding(BaseModel):
    explanation: str


class ConceptEvaluation(BaseModel):
    selection_rationale: str
    distinction: str | None = None
    grounding: CaptionGrounding | None = None


class ConceptRecord(BaseModel):
    id: UUID
    campaign_id: UUID
    job_id: UUID
    revision: int
    state: Literal["DRAFT", "SELECTED", "APPROVED"]
    shortlisted: bool
    content: Candidate
    review: ConceptReview
    validation: ConceptValidation
    evaluation: ConceptEvaluation | None


class Shortlist(BaseModel):
    items: list[ConceptRecord]
    job: CreativeGeneration | None
    insufficient: bool
    stale: bool = False


class ResearchItem(BaseModel):
    id: str
    text: str
    origin: str
    source_urls: list[str]


class ResearchPayload(BaseModel):
    items: list[ResearchItem]


class ResearchRecord(BaseModel):
    id: UUID
    payload: ResearchPayload


class ProductionHandoff(BaseModel):
    concept_id: UUID
    concept_revision: int
    campaign_id: UUID
    campaign_revision: int
    brand_id: UUID
    brief: CampaignBrief
    concept: Candidate
    review: ConceptReview
    validation: ConceptValidation
    state: str


class MediaDetails(BaseModel):
    description: str
    tags: list[str]
    width: int
    height: int
    orientation: str
    duration_seconds: float | None
    audio_streams: int
    source_url: str | None = None
    creator: str | None = None


class MediaRecord(BaseModel):
    id: UUID
    brand_id: UUID
    media_type: Literal["IMAGE", "VIDEO"]
    purpose: Literal["BACKGROUND", "PRODUCT", "BRAND"]
    source_type: Literal["UPLOAD", "PIXABAY"]
    status: str
    details: MediaDetails
    thumbnail_url: str
    acquired_at: datetime


class StockRecord(BaseModel):
    provider_asset_id: str
    media_type: Literal["IMAGE", "VIDEO"]
    source_url: str
    preview_url: str | None
    creator: str | None
    license_url: str
    tags: list[str]
    width: int
    height: int
    duration_seconds: float | None


class InternalSearch(BaseModel):
    source: Literal["INTERNAL"]
    items: list[MediaRecord]
    search_id: None


class StockSearch(BaseModel):
    source: Literal["PIXABAY"]
    items: list[StockRecord]
    search_id: str
    cache_hit: bool


class VideoSpecRecord(BaseModel):
    id: UUID
    concept_id: UUID
    campaign_id: UUID
    revision: int
    payload: VideoPlan


class RenderMediaInfo(BaseModel):
    codec: str
    duration_seconds: float
    frame_rate: float
    audio_streams: int
    file_bytes: int


class ArtifactMetadata(BaseModel):
    media: RenderMediaInfo
    validated: bool


class RenderOutput(BaseModel):
    metadata: ArtifactMetadata
    play_url: str
    download_url: str
    preview_url: str


class RenderJobRecord(WorkflowJob):
    spec_id: UUID
    spec_revision: int
    inputs: VideoPlan
    artifact: RenderOutput | None = None
