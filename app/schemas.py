"""Version 1 BIP contract. Sections are retained even when no evidence is available.

Website-only onboarding is valid. A name is required at confirmation, never guessed
from a domain. Offering audience and psychology data belong to each offering.
"""

from datetime import datetime
from typing import Annotated, Any, Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.crawling.urls import normalize_url

Name = Annotated[str, Field(min_length=1, max_length=300)]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def null_for_missing_strings(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for name, value in data.items():
            field = cls.model_fields.get(name)
            if (
                field
                and isinstance(value, str)
                and not value.strip()
                and type(None) in get_args(field.annotation)
            ):
                data[name] = None
        return data

    @field_validator("*", mode="after")
    @classmethod
    def reject_blank_collection_entries(cls, value):
        if isinstance(value, list) and any(
            isinstance(item, str) and not item.strip() for item in value
        ):
            raise ValueError("Omit unknown collection entries; use an empty array")
        return value


class BrandIdentity(Schema):
    name: Name | None = None
    website_url: str
    legal_name: str | None = None
    tagline: str | None = None
    description: str | None = None
    mission: str | None = None
    vision: str | None = None
    values: list[str] = Field(default_factory=list)
    founded_year: int | None = None
    headquarters: str | None = None
    operating_regions: list[str] = Field(default_factory=list)

    @field_validator("website_url")
    @classmethod
    def website(cls, value: str) -> str:
        return normalize_url(value)


class BusinessPositioning(Schema):
    industry: str | None = None
    category: str | None = None
    business_model: str | None = None
    market_position: str | None = None
    positioning_statement: str | None = None
    differentiators: list[str] = Field(default_factory=list)
    geographic_markets: list[str] = Field(default_factory=list)


class Persona(Schema):
    name: str | None = None
    description: str | None = None
    demographics: str | None = None
    roles: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)
    buying_triggers: list[str] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)
    preferred_channels: list[str] = Field(default_factory=list)


class AudienceSegment(Schema):
    name: str | None = None
    description: str | None = None
    needs: list[str] = Field(default_factory=list)
    geography: list[str] = Field(default_factory=list)


class Audiences(Schema):
    personas: list[Persona] = Field(default_factory=list)
    segments: list[AudienceSegment] = Field(default_factory=list)


class Psychology(Schema):
    pain_points: list[str] = Field(default_factory=list)
    desired_outcomes: list[str] = Field(default_factory=list)
    motivations: list[str] = Field(default_factory=list)
    fears: list[str] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)
    purchase_triggers: list[str] = Field(default_factory=list)
    decision_criteria: list[str] = Field(default_factory=list)
    emotional_benefits: list[str] = Field(default_factory=list)


class Pricing(Schema):
    model: str | None = None
    amount: str | None = None
    currency: str | None = None
    billing_period: str | None = None
    details: str | None = None
    pricing_url: str | None = None


class OfferingData(Schema):
    name: Name
    kind: Literal["PRODUCT", "SERVICE", "OTHER"] | None = None
    description: str | None = None
    url: str | None = None
    features: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    use_cases: list[str] = Field(default_factory=list)
    pricing: Pricing = Field(default_factory=Pricing)
    audiences: Audiences = Field(default_factory=Audiences)
    customer_psychology: Psychology = Field(default_factory=Psychology)
    value_propositions: list[str] = Field(default_factory=list)


class ValuePropositions(Schema):
    primary: str | None = None
    functional_benefits: list[str] = Field(default_factory=list)
    emotional_benefits: list[str] = Field(default_factory=list)
    differentiators: list[str] = Field(default_factory=list)
    reasons_to_believe: list[str] = Field(default_factory=list)


class Testimonial(Schema):
    quote: str | None = None
    attribution: str | None = None
    company: str | None = None


class TrustAndProof(Schema):
    testimonials: list[Testimonial] = Field(default_factory=list)
    customer_names: list[str] = Field(default_factory=list)
    case_studies: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    guarantees: list[str] = Field(default_factory=list)


class BrandVoice(Schema):
    tone: list[str] = Field(default_factory=list)
    personality: list[str] = Field(default_factory=list)
    language_style: str | None = None
    preferred_terms: list[str] = Field(default_factory=list)
    avoided_terms: list[str] = Field(default_factory=list)
    example_phrases: list[str] = Field(default_factory=list)


class Color(Schema):
    name: str | None = None
    hex: str | None = None
    usage: str | None = None


class VisualIdentity(Schema):
    logo_urls: list[str] = Field(default_factory=list)
    colors: list[Color] = Field(default_factory=list)
    fonts: list[str] = Field(default_factory=list)
    imagery_style: str | None = None
    design_style: str | None = None
    guidelines_url: str | None = None


class CreativeContext(Schema):
    content_themes: list[str] = Field(default_factory=list)
    storytelling_angles: list[str] = Field(default_factory=list)
    existing_content_formats: list[str] = Field(default_factory=list)
    available_assets: list[str] = Field(default_factory=list)
    seasonal_context: list[str] = Field(default_factory=list)
    calls_to_action: list[str] = Field(default_factory=list)


class MarketingGuardrails(Schema):
    prohibited_claims: list[str] = Field(default_factory=list)
    sensitive_topics: list[str] = Field(default_factory=list)
    legal_disclaimers: list[str] = Field(default_factory=list)
    compliance_requirements: list[str] = Field(default_factory=list)
    brand_safety_rules: list[str] = Field(default_factory=list)
    approval_requirements: str | None = None


class MarketingDefaults(Schema):
    objectives: list[str] = Field(default_factory=list)
    target_channels: list[str] = Field(default_factory=list)
    content_language: str | None = None
    target_regions: list[str] = Field(default_factory=list)
    preferred_cta: str | None = None
    budget: str | None = None
    video_duration_seconds: int | None = Field(None, gt=0)
    aspect_ratio: str | None = None
    default_landing_page: str | None = None


class BIP(Schema):
    brand_identity: BrandIdentity
    business_positioning: BusinessPositioning = Field(default_factory=BusinessPositioning)
    offerings: list[OfferingData] = Field(default_factory=list)
    customer_personas_and_audience_segments: Audiences = Field(default_factory=Audiences)
    customer_psychology: Psychology = Field(default_factory=Psychology)
    value_propositions: ValuePropositions = Field(default_factory=ValuePropositions)
    trust_and_proof: TrustAndProof = Field(default_factory=TrustAndProof)
    brand_voice: BrandVoice = Field(default_factory=BrandVoice)
    visual_identity: VisualIdentity = Field(default_factory=VisualIdentity)
    creative_context: CreativeContext = Field(default_factory=CreativeContext)
    marketing_guardrails: MarketingGuardrails = Field(default_factory=MarketingGuardrails)
    marketing_defaults: MarketingDefaults = Field(default_factory=MarketingDefaults)


class Citation(Schema):
    source_url: str
    quote: str = Field(min_length=1)


class LLMEvidence(Schema):
    path: str
    origin: Literal["WEBSITE", "INFERRED"]
    citations: list[Citation] = Field(default_factory=list)
    reasoning: str | None = None


class ExtractionResult(Schema):
    profile: BIP
    evidence: list[LLMEvidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CreateBrand(Schema):
    website_url: str
    name: Name | None = None

    @field_validator("website_url")
    @classmethod
    def website(cls, value: str) -> str:
        return normalize_url(value)


class ExtractionRequest(Schema):
    replace_existing: bool = False


class ProfilePatch(Schema):
    expected_revision: int = Field(ge=0)
    changes: dict[str, Any]


class ConfirmProfile(Schema):
    expected_revision: int = Field(ge=0)


class ProvenanceCitation(Citation):
    fetched_at: datetime


class FieldProvenance(Schema):
    path: str
    origin: Literal["WEBSITE", "INFERRED", "USER"]
    citations: list[ProvenanceCitation]
    reasoning: str | None
    recorded_at: datetime


class ProfileResponse(Schema):
    brand_id: UUID
    revision: int
    status: Literal["DRAFT", "CONFIRMED"]
    schema_version: int
    profile: BIP
    provenance: list[FieldProvenance]
    warnings: list[str]
    provider: str | None
    model: str | None
    extracted_at: datetime | None
    updated_at: datetime
    confirmed_at: datetime | None
