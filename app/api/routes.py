import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.errors import DomainError
from app.models import ExtractionJob
from app.schemas import (
    BIP,
    ConfirmProfile,
    CreateBrand,
    ExtractionRequest,
    ProfilePatch,
    ProfileResponse,
)
from app.services import BrandService


def authorize(authorization: Annotated[str | None, Header()] = None):
    token = get_settings().api_access_token
    if token and token.get_secret_value():
        expected = "Bearer " + token.get_secret_value()
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise DomainError("UNAUTHORIZED", "A valid bearer token is required", 401)


router = APIRouter(prefix="/v1", dependencies=[Depends(authorize)])
DB = Annotated[Session, Depends(get_session)]


def job_view(job: ExtractionJob) -> dict:
    return {
        "id": job.id,
        "brand_id": job.brand_id,
        "status": job.status,
        "attempts": job.attempts,
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "poll_url": f"/v1/extractions/{job.id}",
    }


@router.post("/brands", status_code=201)
def create_brand(data: CreateBrand, session: DB, response: Response):
    """Register a website. Crawling starts only after POST /brands/{id}/extractions."""
    brand = BrandService(session).create(data)
    response.headers["Location"] = f"/v1/brands/{brand.id}"
    return {
        "id": brand.id,
        "website_url": brand.website_url,
        "name": brand.name,
        "revision": brand.revision,
        "created_at": brand.created_at,
    }


@router.get("/brands")
def list_brands(
    session: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return {
        "items": BrandService(session).list_brands(limit, offset),
        "limit": limit,
        "offset": offset,
    }


@router.get("/brands/{brand_id}")
def get_brand(brand_id: uuid.UUID, session: DB):
    service = BrandService(session)
    brand = service.require_brand(brand_id)
    profile = service.repo.profile(brand_id)
    return {
        "id": brand.id,
        "website_url": brand.website_url,
        "name": brand.name,
        "revision": brand.revision,
        "profile_status": profile.status if profile else None,
        "created_at": brand.created_at,
    }


@router.post("/brands/{brand_id}/extractions", status_code=202)
def extract_brand(
    brand_id: uuid.UUID, session: DB, response: Response, request: ExtractionRequest | None = None
):
    """Enqueue durable work and return immediately. Repeated triggers reuse an active job."""
    job = BrandService(session).enqueue(brand_id, request.replace_existing if request else False)
    response.headers["Location"] = f"/v1/extractions/{job.id}"
    response.headers["Retry-After"] = "2"
    return job_view(job)


@router.get("/extractions/{job_id}")
def get_extraction(job_id: uuid.UUID, session: DB):
    job = session.get(ExtractionJob, job_id)
    if not job:
        raise DomainError("JOB_NOT_FOUND", "Extraction job was not found", 404)
    return job_view(job)


@router.get("/profile-schema")
def profile_schema():
    """Complete version 1 JSON schema for clients building review/edit interfaces."""
    return {"schema_version": 1, "schema": BIP.model_json_schema()}


@router.get("/brands/{brand_id}/profile", response_model=ProfileResponse)
def get_profile(brand_id: uuid.UUID, session: DB):
    return BrandService(session).read(brand_id)


@router.patch("/brands/{brand_id}/profile", response_model=ProfileResponse)
def patch_profile(brand_id: uuid.UUID, request: ProfilePatch, session: DB):
    """Recursively merge fields; replace supplied arrays; mark changed leaves USER and DRAFT."""
    return BrandService(session).update(brand_id, request)


@router.post("/brands/{brand_id}/profile/confirm", response_model=ProfileResponse)
def confirm_profile(brand_id: uuid.UUID, request: ConfirmProfile, session: DB):
    return BrandService(session).confirm(brand_id, request.expected_revision)


@router.get("/brands/{brand_id}/offerings")
def list_offerings(brand_id: uuid.UUID, session: DB):
    service = BrandService(session)
    service.require_brand(brand_id)
    return {
        "items": [
            {"id": row.id, "name": row.name, **row.details}
            for row in service.repo.offerings(brand_id)
        ]
    }


@router.get("/brands/{brand_id}/sources")
def list_sources(brand_id: uuid.UUID, session: DB):
    service = BrandService(session)
    service.require_brand(brand_id)
    return {
        "items": [
            {
                "id": row.id,
                "job_id": row.job_id,
                "source_type": row.source_type,
                "url": row.url,
                "title": row.title,
                "fetched_at": row.fetched_at,
                "content_hash": row.content_hash,
            }
            for row in service.repo.source_documents(brand_id)
        ]
    }
