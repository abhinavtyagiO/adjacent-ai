"""Campaign transactions and review lifecycle; no model/network calls in request handlers."""

from pydantic import ValidationError
from sqlalchemy import select

from app.creative.models import Campaign, CreativeConcept, CreativeJob, ResearchSnapshot
from app.creative.research import build_context, validate_focus
from app.creative.schemas import CampaignBrief, Candidate
from app.creative.validation import check_edit
from app.errors import DomainError
from app.models import Brand, CustomerConcern, Profile, SourceDocument, now
from app.repository import Repository
from app.services import BrandService


def require(session, cls, key, lock=False):
    query = select(cls).where(cls.id == key)
    row = session.scalar(
        query.with_for_update().execution_options(populate_existing=True) if lock else query
    )
    if row is None:
        raise DomainError("NOT_FOUND", f"{cls.__name__} was not found", 404)
    return row


def revision(row, expected):
    if row.revision != expected:
        raise DomainError(
            "REVISION_CONFLICT", "The resource changed; fetch its current revision", 409
        )


def validate(schema, data):
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise DomainError(
            "INVALID_CREATIVE_DATA",
            "Creative data does not match the schema",
            422,
            details={
                "fields": [
                    {"path": list(e["loc"]), "type": e["type"]}
                    for e in exc.errors(include_input=False)[:15]
                ]
            },
        ) from exc


def view(row):
    """ORM transport projection; JSONB does not contain credentials or private reasoning."""
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


class CampaignService:
    def __init__(self, session, settings):
        self.session, self.settings = session, settings

    def brand_context(self, brand_id, lock=False):
        brand = require(self.session, Brand, brand_id, lock)
        profile = self.session.get(Profile, brand_id)
        if not profile:
            raise DomainError("PROFILE_REQUIRED", "Create a brand profile before a campaign", 409)
        return brand, profile, Repository(self.session).bip(profile)

    def create(self, brand_id, brief):
        _, _, bip = self.brand_context(brand_id, True)
        validate_focus(brief, bip)
        campaign = Campaign(brand_id=brand_id, brief=brief.model_dump(mode="json"))
        self.session.add(campaign)
        self.session.commit()
        return campaign

    def update(self, campaign_id, request):
        campaign = require(self.session, Campaign, campaign_id, True)
        revision(campaign, request.expected_revision)
        brief = validate(CampaignBrief, {**campaign.brief, **request.changes})
        _, _, bip = self.brand_context(campaign.brand_id)
        validate_focus(brief, bip)
        campaign.brief = brief.model_dump(mode="json")
        campaign.revision += 1
        campaign.updated_at = now()
        # Previously selected work is preserved but cannot be handed off for a new brief.
        self.session.commit()
        return campaign

    def enqueue(self, campaign_id, request, binding):
        # Consistent lock order with the worker: brand -> campaign -> job.
        initial = require(self.session, Campaign, campaign_id)
        brand, profile, bip = self.brand_context(initial.brand_id, True)
        campaign = require(self.session, Campaign, campaign_id, True)
        revision(campaign, request.expected_revision)
        active = self.session.scalar(
            select(CreativeJob).where(
                CreativeJob.campaign_id == campaign_id,
                CreativeJob.status.in_(["PENDING", "RUNNING"]),
            )
        )
        if active:
            previous = require(self.session, ResearchSnapshot, active.research_id)
            if (
                previous.campaign_revision != campaign.revision
                or previous.brand_revision != brand.revision
            ):
                raise DomainError(
                    "GENERATION_ACTIVE_STALE",
                    "An older active generation must finish before starting a replacement",
                    409,
                )
            return active
        source_id = BrandService(self.session).source_snapshot_id(profile)
        docs = list(
            self.session.scalars(
                select(SourceDocument)
                .where(SourceDocument.brand_id == brand.id, SourceDocument.job_id == source_id)
                .order_by(SourceDocument.fetched_at, SourceDocument.id)
            )
        )
        if not docs:
            raise DomainError(
                "SOURCE_SNAPSHOT_MISSING", "Saved source documents are unavailable", 409
            )
        concerns = list(
            self.session.scalars(
                select(CustomerConcern)
                .where(
                    CustomerConcern.brand_id == brand.id, CustomerConcern.source_job_id == source_id
                )
                .order_by(CustomerConcern.concern_key)
            )
        )
        research = ResearchSnapshot(
            campaign_id=campaign.id,
            brand_revision=brand.revision,
            campaign_revision=campaign.revision,
            source_job_id=source_id,
            payload=build_context(
                bip,
                profile.provenance,
                docs,
                CampaignBrief.model_validate(campaign.brief),
                [c.details for c in concerns],
                self.settings.creative_context_max_chars,
            ),
        )
        self.session.add(research)
        self.session.flush()
        model = (
            binding.get("marketing_model")
            or self.settings.marketing_model
            or self.settings.openai_model
        )
        job = CreativeJob(
            campaign_id=campaign.id,
            research_id=research.id,
            model=model,
            chatgpt_account_id=binding.get("chatgpt_account_id"),
            diagnostics={
                "runtime": {"api_build_revision": self.settings.app_build_revision or None}
            },
        )
        self.session.add(job)
        self.session.commit()
        return job

    def concept_context(self, concept_id):
        concept = require(self.session, CreativeConcept, concept_id)
        campaign = require(self.session, Campaign, concept.campaign_id, True)
        concept = require(self.session, CreativeConcept, concept_id, True)
        job = require(self.session, CreativeJob, concept.job_id)
        research = require(self.session, ResearchSnapshot, job.research_id)
        return concept, campaign, job, research

    def update_concept(self, concept_id, request):
        concept, campaign, _, research = self.concept_context(concept_id)
        revision(concept, request.expected_revision)
        allowed = {
            "primary_hook",
            "alternative_hooks",
            "core_insight",
            "audience_situation",
            "problem_or_aspiration",
            "story",
            "closing_line",
            "cta",
            "title",
            "b_roll",
            "asset_requirements",
            "on_screen_copy",
        }
        if set(request.changes) - allowed:
            raise DomainError(
                "CONCEPT_EDIT_FIELDS",
                "Only wording, hooks, story, CTA and production suggestions are editable",
                422,
            )
        merged = {**concept.content, **request.changes}
        if isinstance(request.changes.get("story"), dict):
            merged["story"] = {**concept.content["story"], **request.changes["story"]}
        candidate = validate(Candidate, merged)
        # Validate against the current brief; preserve the original research and generation.
        context = {**research.payload, "brief": campaign.brief}
        previous = Candidate.model_validate(concept.content)
        concept.validation = check_edit(previous, candidate, context, concept.validation)
        concept.content = candidate.model_dump(mode="json")
        if "on_screen_copy" not in merged:
            concept.content.pop("on_screen_copy")
        concept.revision += 1
        concept.edited_by_user = True
        concept.state, concept.review, concept.shortlisted = "DRAFT", {}, False
        concept.evaluation = None  # Scores for old copy cannot approve changed wording.
        concept.updated_at = now()
        self.session.commit()
        return concept

    def select(self, concept_id, request, approve=False):
        # Lock brand first; selections must be current against both immutable bindings.
        initial = require(self.session, CreativeConcept, concept_id)
        parent = require(self.session, Campaign, initial.campaign_id)
        brand = require(self.session, Brand, parent.brand_id, True)
        concept, campaign, job, research = self.concept_context(concept_id)
        revision(concept, request.expected_revision)
        if (
            campaign.revision != research.campaign_revision
            or brand.revision != research.brand_revision
        ):
            raise DomainError(
                "RESEARCH_STALE",
                "Brand or brief changed; create a new generation before selection",
                409,
            )
        if job.status in {"PENDING", "RUNNING"}:
            raise DomainError(
                "GENERATION_ACTIVE", "Wait for generation to finish before selection", 409
            )
        if not concept.validation.get("eligible"):
            raise DomainError(
                "CONCEPT_BLOCKED", "Resolve blocking validation flags before selection", 422
            )
        if approve and not (
            request.human_review_completed
            and request.acknowledge_claim_risks
            and request.acknowledge_asset_requirements
        ):
            raise DomainError(
                "HUMAN_REVIEW_REQUIRED",
                "Approval requires explicit review and acknowledgment of claims and assets",
                422,
            )
        others = self.session.scalars(
            select(CreativeConcept)
            .where(
                CreativeConcept.campaign_id == campaign.id,
                CreativeConcept.state.in_(["SELECTED", "APPROVED"]),
                CreativeConcept.id != concept.id,
            )
            .with_for_update()
        )
        for other in others:
            other.state, other.review = "DRAFT", {}
            other.revision += 1
            other.updated_at = now()
        self.session.flush()  # Free partial unique selection index before assigning.
        concept.state = "APPROVED" if approve else "SELECTED"
        concept.revision += 1
        concept.review = {
            **request.model_dump(),
            "reviewed_at": now().isoformat(),
            "production_approval_only": True,
            "advertising_claims_approved": False,
        }
        concept.updated_at = now()
        self.session.commit()
        return concept

    def handoff(self, concept_id):
        concept = require(self.session, CreativeConcept, concept_id)
        campaign = require(self.session, Campaign, concept.campaign_id)
        brand = require(self.session, Brand, campaign.brand_id)
        job = require(self.session, CreativeJob, concept.job_id)
        research = require(self.session, ResearchSnapshot, job.research_id)
        if concept.state not in {"SELECTED", "APPROVED"}:
            raise DomainError(
                "CONCEPT_NOT_SELECTED", "Select a concept before requesting production handoff", 409
            )
        if (
            brand.revision != research.brand_revision
            or campaign.revision != research.campaign_revision
        ):
            raise DomainError(
                "RESEARCH_STALE", "The selected concept is based on an older brand or brief", 409
            )
        return {
            "handoff_version": 1,
            "concept_id": concept.id,
            "concept_revision": concept.revision,
            "campaign_id": campaign.id,
            "campaign_revision": campaign.revision,
            "brand_id": brand.id,
            "bip_revision": research.brand_revision,
            "research_id": research.id,
            "source_job_id": research.source_job_id,
            "brief": campaign.brief,
            "concept": concept.content,
            "review": concept.review,
            "validation": concept.validation,
            "state": concept.state,
            "advertising_claims_approved": False,
            "video_rendered": False,
        }
