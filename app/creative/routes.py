"""Authenticated campaign-to-shortlist APIs, following existing /v1 conventions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes import authorize, extraction_binding
from app.config import get_settings
from app.creative.models import Campaign, CreativeConcept, CreativeJob, ResearchSnapshot
from app.creative.schemas import (
    BriefPatch,
    CampaignBrief,
    ConceptPatch,
    ConceptSelection,
    GenerationRequest,
)
from app.creative.service import CampaignService, require, view
from app.db import get_session
from app.errors import DomainError
from app.models import Brand

router = APIRouter(prefix="/v1", tags=["creative strategy"], dependencies=[Depends(authorize)])
DB = Annotated[Session, Depends(get_session)]


def service(session):
    return CampaignService(session, get_settings())


def job_view(job):
    return {**view(job), "poll_url": f"/v1/creative-generations/{job.id}"}


@router.post("/brands/{brand_id}/campaigns", status_code=201)
def create_campaign(brand_id: UUID, brief: CampaignBrief, session: DB, response: Response):
    campaign = service(session).create(brand_id, brief)
    response.headers["Location"] = f"/v1/campaigns/{campaign.id}"
    return view(campaign)


@router.get("/brands/{brand_id}/campaigns")
def list_campaigns(
    brand_id: UUID,
    session: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    require(session, Brand, brand_id)
    rows = session.scalars(
        select(Campaign)
        .where(Campaign.brand_id == brand_id)
        .order_by(Campaign.created_at.desc(), Campaign.id)
        .limit(limit)
        .offset(offset)
    )
    return {"items": [view(r) for r in rows], "limit": limit, "offset": offset}


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: UUID, session: DB):
    return view(require(session, Campaign, campaign_id))


@router.patch("/campaigns/{campaign_id}/brief")
def update_brief(campaign_id: UUID, request: BriefPatch, session: DB):
    return view(service(session).update(campaign_id, request))


@router.post("/campaigns/{campaign_id}/generations", status_code=202)
def generate_concepts(
    campaign_id: UUID, request: GenerationRequest, session: DB, response: Response
):
    job = service(session).enqueue(
        campaign_id, request, extraction_binding(request, marketing_only=True)
    )
    response.headers["Location"] = f"/v1/creative-generations/{job.id}"
    response.headers["Retry-After"] = "2"
    return job_view(job)


@router.get("/campaigns/{campaign_id}/generations")
def list_generations(
    campaign_id: UUID,
    session: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    require(session, Campaign, campaign_id)
    rows = session.scalars(
        select(CreativeJob)
        .where(CreativeJob.campaign_id == campaign_id)
        .order_by(CreativeJob.created_at.desc(), CreativeJob.id)
        .limit(limit)
        .offset(offset)
    )
    return {"items": [job_view(row) for row in rows], "limit": limit, "offset": offset}


@router.get("/creative-generations/{job_id}")
def get_generation(job_id: UUID, session: DB):
    return job_view(require(session, CreativeJob, job_id))


@router.get("/campaigns/{campaign_id}/research/{research_id}")
def get_research(campaign_id: UUID, research_id: UUID, session: DB):
    require(session, Campaign, campaign_id)
    research = require(session, ResearchSnapshot, research_id)
    if research.campaign_id != campaign_id:
        raise DomainError("NOT_FOUND", "Research was not found in this campaign", 404)
    return view(research)


@router.get("/campaigns/{campaign_id}/concepts")
def list_concepts(
    campaign_id: UUID,
    session: DB,
    job_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    require(session, Campaign, campaign_id)
    query = select(CreativeConcept).where(CreativeConcept.campaign_id == campaign_id)
    if job_id:
        job = require(session, CreativeJob, job_id)
        if job.campaign_id != campaign_id:
            raise DomainError("NOT_FOUND", "Generation was not found in this campaign", 404)
        query = query.where(CreativeConcept.job_id == job_id)
    rows = session.scalars(
        query.order_by(CreativeConcept.created_at.desc(), CreativeConcept.id)
        .limit(limit)
        .offset(offset)
    )
    return {"items": [view(r) for r in rows], "limit": limit, "offset": offset}


@router.get("/campaigns/{campaign_id}/shortlist")
def shortlist(campaign_id: UUID, session: DB, job_id: UUID | None = None):
    campaign = require(session, Campaign, campaign_id)
    job = (
        require(session, CreativeJob, job_id)
        if job_id
        else session.scalar(
            select(CreativeJob)
            .where(CreativeJob.campaign_id == campaign_id)
            .order_by(CreativeJob.created_at.desc(), CreativeJob.id)
            .limit(1)
        )
    )
    if not job:
        return {"items": [], "job": None, "insufficient": True}
    if job.campaign_id != campaign_id:
        raise DomainError("NOT_FOUND", "Generation was not found in this campaign", 404)
    research = require(session, ResearchSnapshot, job.research_id)
    brand = require(session, Brand, campaign.brand_id)
    rows = list(
        session.scalars(
            select(CreativeConcept)
            .where(CreativeConcept.job_id == job.id, CreativeConcept.shortlisted.is_(True))
            .order_by(CreativeConcept.created_at, CreativeConcept.id)
        )
    )
    return {
        "items": [view(r) for r in rows],
        "job": job_view(job),
        "insufficient": len(rows) < 5,
        "stale": campaign.revision != research.campaign_revision
        or brand.revision != research.brand_revision,
        "scoring_semantics": "INTERNAL_CREATIVE_JUDGMENT_NOT_PERFORMANCE_PREDICTION",
    }


@router.get("/concepts/{concept_id}")
def get_concept(concept_id: UUID, session: DB):
    return view(require(session, CreativeConcept, concept_id))


@router.patch("/concepts/{concept_id}")
def edit_concept(concept_id: UUID, request: ConceptPatch, session: DB):
    return view(service(session).update_concept(concept_id, request))


@router.post("/concepts/{concept_id}/select")
def select_concept(concept_id: UUID, request: ConceptSelection, session: DB):
    return view(service(session).select(concept_id, request))


@router.post("/concepts/{concept_id}/approve")
def approve_concept(concept_id: UUID, request: ConceptSelection, session: DB):
    return view(service(session).select(concept_id, request, approve=True))


@router.get("/concepts/{concept_id}/handoff")
def production_handoff(concept_id: UUID, session: DB):
    return service(session).handoff(concept_id)
