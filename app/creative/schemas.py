"""Bounded campaign, generator, evaluator and human review contracts."""

from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import Field, StrictBool, field_validator, model_validator

from app.crawling.urls import normalize_url
from app.schemas import Schema

Short = Annotated[str, Field(min_length=1, max_length=200)]
Text = Annotated[str, Field(min_length=1, max_length=700)]
Hook = Annotated[str, Field(min_length=1, max_length=180)]
Scope = Literal["BRAND", "FAMILY", "PRODUCT"]


class CampaignBrief(Schema):
    objective: Short
    audience: Short | None = None
    concern: Short | None = None
    promotion_scope: Scope = "BRAND"
    offering_key: Short | None = None
    target_platform: Short | None = None
    language: Short | None = None
    region: Short | None = None
    cta: Short | None = None
    destination_url: str | None = Field(None, max_length=2048)
    creative_exclusions: list[Short] = Field(default_factory=list, max_length=20)
    creative_references: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(
        default_factory=list, max_length=5
    )
    aspect_ratio: Literal["9:16"] = "9:16"
    duration_seconds: int = Field(15, ge=5, le=60)

    @field_validator("destination_url")
    @classmethod
    def destination(cls, value):
        if not value:
            return value
        original = urlsplit(value)
        validated = urlsplit(normalize_url(value))
        # Validate host/scheme without discarding campaign tracking or landing anchors.
        return urlunsplit(validated._replace(query=original.query, fragment=original.fragment))

    @model_validator(mode="after")
    def targeting(self):
        if not self.audience and not self.concern:
            raise ValueError("Supply an audience or concern")
        if (self.promotion_scope != "BRAND") != bool(self.offering_key):
            raise ValueError("FAMILY/PRODUCT scope needs an offering_key; BRAND scope omits it")
        return self


class BriefPatch(Schema):
    expected_revision: int = Field(ge=1)
    changes: dict = Field(max_length=15)


class GenerationRequest(Schema):
    expected_revision: int = Field(ge=1)
    chatgpt_account_id: UUID | None = None
    marketing_model: Short | None = None


class Story(Schema):
    opening: Annotated[str, Field(min_length=1, max_length=260)]
    development: Annotated[str, Field(min_length=1, max_length=260)]
    product_connection: Annotated[str, Field(min_length=1, max_length=260)]
    payoff: Annotated[str, Field(min_length=1, max_length=260)]


class AssetRequirement(Schema):
    kind: Literal["STOCK_BROLL", "APP_SCREENSHOT", "PRODUCT_DEMO", "BRAND_ASSET"]
    description: Short
    requires_creation: bool = True
    research_reference_ids: list[Short] = Field(default_factory=list, max_length=4)


class SingleCaption(Schema):
    treatment: Literal["SINGLE_CAPTION"]
    text: Hook
    closing_brand_message: Hook | None = None


class CaptionBeat(Schema):
    beat: Literal["HOOK", "DEVELOPMENT", "PRODUCT_CONNECTION", "PAYOFF"]
    text: Hook


class SequentialCaptions(Schema):
    treatment: Literal["SEQUENTIAL_CAPTIONS"]
    captions: list[CaptionBeat] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def ordered_beats(self):
        if [caption.beat for caption in self.captions] != [
            "HOOK",
            "DEVELOPMENT",
            "PRODUCT_CONNECTION",
            "PAYOFF",
        ]:
            raise ValueError("Supply hook, development, product connection and payoff in order")
        return self


class Candidate(Schema):
    candidate_key: Annotated[str, Field(min_length=1, max_length=50, pattern=r"^[a-z0-9-]+$")]
    title: Annotated[str, Field(min_length=1, max_length=120)]
    core_insight: Text
    psychological_driver: Literal["RECOGNITION", "CURIOSITY", "DESIRE", "RELEVANCE"]
    strategic_mechanism: Short
    audience: Short
    audience_situation: Text
    problem_or_aspiration: Text
    product_capability: Text
    capability_reference_ids: list[Short] = Field(min_length=1, max_length=5)
    primary_hook: Hook
    alternative_hooks: list[Hook] = Field(min_length=2, max_length=3)
    story: Story
    production_format: Literal["TEXT_OVER_VIDEO"] = "TEXT_OVER_VIDEO"
    on_screen_copy: SingleCaption | SequentialCaptions | None = None
    b_roll: list[Short] = Field(min_length=1, max_length=5)
    asset_requirements: list[AssetRequirement] = Field(default_factory=list, max_length=5)
    closing_line: Short
    cta: Short
    research_reference_ids: list[Short] = Field(min_length=1, max_length=10)
    factual_assumptions: list[Short] = Field(default_factory=list, max_length=8)
    potential_risks: list[Short] = Field(default_factory=list, max_length=8)


class GeneratedCandidate(Candidate):
    """New model responses require captions. Saved Phase 2A ideas may predate them."""

    on_screen_copy: SingleCaption | SequentialCaptions


class GeneratorResult(Schema):
    candidates: list[GeneratedCandidate] = Field(min_length=1, max_length=8)
    limitations: list[Short] = Field(default_factory=list, max_length=8)


class Scores(Schema):
    audience_relevance: float = Field(ge=0, le=10)
    hook_clarity: float = Field(ge=0, le=10)
    distinctiveness: float = Field(ge=0, le=10)
    truthful_benefit_connection: float = Field(ge=0, le=10)
    brand_consistency: float = Field(ge=0, le=10)
    production_feasibility: float = Field(ge=0, le=10)
    evidence_and_risk: float = Field(ge=0, le=10)


GroundingStatus = Literal[
    "SUPPORTED",
    "NEEDS_REVIEW",
    "UNSUPPORTED_PROMISE",
    "PRODUCT_SCOPE_MISMATCH",
    "UNSUPPORTED_SAFETY_ASSURANCE",
]


class CopyGrounding(Schema):
    hooks: GroundingStatus
    story: GroundingStatus
    closing_line: GroundingStatus
    payoff: GroundingStatus
    on_screen_copy: GroundingStatus
    explanation: Short


class OverlapGroup(Schema):
    candidate_keys: list[Short] = Field(min_length=2, max_length=8)
    representative_key: Short
    rationale: Short


class Evaluation(Schema):
    candidate_key: Short
    scores: Scores
    strengths: list[Short] = Field(min_length=1, max_length=3)
    weaknesses: list[Short] = Field(default_factory=list, max_length=3)
    selection_rationale: Text
    selected: bool
    grounding: CopyGrounding
    distinction: Short | None


class EvaluatorResult(Schema):
    evaluations: list[Evaluation] = Field(min_length=1, max_length=8)
    overlap_groups: list[OverlapGroup] = Field(max_length=4)
    insufficiency_reason: Text | None = None


class ConceptPatch(Schema):
    expected_revision: int = Field(ge=1)
    changes: dict = Field(max_length=12)


class ConceptSelection(Schema):
    expected_revision: int = Field(ge=1)
    human_review_completed: StrictBool = False
    acknowledge_claim_risks: StrictBool = False
    acknowledge_asset_requirements: StrictBool = False
