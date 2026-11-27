"""Bearer-protected media, VideoSpec, job and private playback endpoints."""

import logging
import re
import tempfile
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import Float, Integer, cast, or_, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.read_models import Page, RenderRecord, RenderStatus
from app.api.routes import authorize
from app.api.workflow_views import (
    WORKSPACE_WORKFLOW,
    InternalSearch,
    MediaRecord,
    RenderJobRecord,
    StockSearch,
    VideoSpecRecord,
)
from app.config import get_settings
from app.creative.models import Campaign, CreativeConcept
from app.creative.service import require, view
from app.db import get_session
from app.errors import DomainError
from app.models import Brand, now
from app.video.models import MediaAsset, MediaSearchCache, RenderJob, VideoArtifact, VideoSpec
from app.video.pixabay import PixabayMediaProvider, canonical_query, existing_stock
from app.video.schemas import CreateVideoSpec, ImportStock, RenderRequest, VideoPlan, VideoSpecPatch
from app.video.service import VideoService
from app.video.storage import LocalStorage


def private_media():
    if not get_settings().api_access_token:
        raise DomainError(
            "LOCAL_ACCESS_TOKEN_REQUIRED", "Configure API_ACCESS_TOKEN for private media", 503
        )


router = APIRouter(
    prefix="/v1", tags=["Video"], dependencies=[Depends(private_media), Depends(authorize)]
)
DB = Annotated[Session, Depends(get_session)]
Purpose = Literal["BACKGROUND", "PRODUCT", "BRAND"]


def asset_view(row):
    return {
        key: value
        for key, value in view(row).items()
        if key not in {"thumbnail_key", "original_key", "normalized_key", "source_identity"}
    } | {
        "acquired_at": row.created_at,
        "thumbnail_url": f"/v1/brands/{row.brand_id}/media/{row.id}/thumbnail",
        "has_normalized_derivative": row.normalized_key is not None,
    }


@router.post(
    "/brands/{brand_id}/media",
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
    responses={201: {"model": MediaRecord}},
    tags=[WORKSPACE_WORKFLOW],
)
async def upload(
    brand_id: UUID,
    request: Request,
    session: DB,
    purpose: Purpose,
    confirm_rights: Annotated[bool, Query()],
    description: Annotated[str, Query(max_length=500)] = "",
    tags: Annotated[str, Query(max_length=500)] = "",
):
    """Stream the raw binary body to a private temporary file with a byte limit."""
    if not confirm_rights:
        raise DomainError("MEDIA_PERMISSION_REQUIRED", "Confirm permission to use the media", 422)
    settings = get_settings()
    limit = settings.media_max_bytes
    count = 0
    with tempfile.TemporaryDirectory(prefix="adjacent-upload-") as temp:
        path = Path(temp) / "upload"
        with path.open("wb") as target:
            async for chunk in request.stream():
                count += len(chunk)
                if count > limit:
                    raise DomainError("MEDIA_SIZE", "The file exceeds the upload limit", 413)
                target.write(chunk)
        result = await run_in_threadpool(
            VideoService(session, settings).ingest,
            brand_id,
            path,
            purpose,
            description,
            [t.strip() for t in tags.split(",") if t.strip()],
        )
        return asset_view(result)


@router.get(
    "/brands/{brand_id}/media",
    responses={200: {"model": Page[MediaRecord]}},
    tags=[WORKSPACE_WORKFLOW],
)
def list_media(
    brand_id: UUID,
    session: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    require(session, Brand, brand_id)
    rows = session.scalars(
        select(MediaAsset)
        .where(MediaAsset.brand_id == brand_id)
        .order_by(MediaAsset.created_at.desc(), MediaAsset.id)
        .offset(offset)
        .limit(limit)
    )
    return {"items": [asset_view(row) for row in rows], "offset": offset, "limit": limit}


def scoped_asset(brand_id, asset_id, session):
    asset = session.scalar(
        select(MediaAsset).where(MediaAsset.id == asset_id, MediaAsset.brand_id == brand_id)
    )
    if asset is None:
        raise DomainError("MEDIA_NOT_FOUND", "Media is unavailable for this brand", 404)
    return asset


logger = logging.getLogger(__name__)
MediaKind = Literal["IMAGE", "VIDEO"]
Orientation = Literal["PORTRAIT", "LANDSCAPE", "SQUARE"]


@router.get(
    "/brands/{brand_id}/media/search",
    responses={200: {"model": InternalSearch | StockSearch}},
    tags=[WORKSPACE_WORKFLOW],
)
def search_media(
    brand_id: UUID,
    session: DB,
    q: Annotated[str, Query(min_length=1, max_length=100)],
    media_type: MediaKind,
    orientation: Orientation | None = None,
    min_width: Annotated[int, Query(ge=0, le=4096)] = 0,
    min_height: Annotated[int, Query(ge=0, le=4096)] = 0,
    min_duration: Annotated[float, Query(ge=0, le=180)] = 0,
    purpose: Purpose = "BACKGROUND",
    search_pixabay: bool = False,
):
    """Find useful authorized media in this brand before requesting new stock."""
    require(session, Brand, brand_id)
    query = canonical_query(q)
    if not query or (media_type == "IMAGE" and min_duration > 0):
        raise DomainError("INVALID_MEDIA_QUERY", "Provide keywords and valid media filters", 422)
    filters = [
        MediaAsset.brand_id == brand_id,
        MediaAsset.media_type == media_type,
        MediaAsset.purpose == purpose,
        MediaAsset.status == "READY",
        MediaAsset.details["usage_permission"].astext == "CAMPAIGN_RENDER",
        cast(MediaAsset.details["width"].astext, Integer) >= min_width,
        cast(MediaAsset.details["height"].astext, Integer) >= min_height,
    ]
    if orientation:
        filters.append(MediaAsset.details["orientation"].astext == orientation)
    if min_duration:
        filters.append(cast(MediaAsset.details["duration_seconds"].astext, Float) >= min_duration)
    for word in query.split():
        escaped = word.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        filters.append(
            or_(
                MediaAsset.details["description"].astext.ilike(f"%{escaped}%", escape="!"),
                MediaAsset.details["tags"].astext.ilike(f"%{escaped}%", escape="!"),
            )
        )
    rows = list(
        session.scalars(
            select(MediaAsset)
            .where(*filters)
            .order_by(MediaAsset.created_at.desc(), MediaAsset.id)
            .limit(40)
        )
    )
    if rows or not search_pixabay or purpose != "BACKGROUND":
        logger.info("media_search source=internal results=%s", len(rows))
        return {
            "items": [asset_view(row) for row in rows],
            "source": "INTERNAL",
            "search_id": None,
            "cache_hit": False,
        }
    provider = PixabayMediaProvider(get_settings())
    search_id, candidates, cache_hit = provider.search(session, query, media_type)
    matches = [
        {key: value for key, value in row.items() if key != "download_url"}
        for row in candidates
        if (orientation is None or row["orientation"] == orientation)
        and row["width"] >= min_width
        and row["height"] >= min_height
        and (row["duration_seconds"] or 0) >= min_duration
    ]
    return {"items": matches, "source": "PIXABAY", "search_id": search_id, "cache_hit": cache_hit}


@router.post(
    "/brands/{brand_id}/media/pixabay",
    status_code=201,
    responses={201: {"model": MediaRecord}},
    tags=[WORKSPACE_WORKFLOW],
)
def import_stock(brand_id: UUID, request: ImportStock, session: DB):
    """Download just one user-selected result. Stock is eligible as background only."""
    require(session, Brand, brand_id)
    if not request.confirm_rights:
        raise DomainError("MEDIA_PERMISSION_REQUIRED", "Confirm the selected stock use", 422)
    asset = existing_stock(session, brand_id, request.media_type, request.provider_asset_id)
    if asset:
        logger.info("media_acquired source=pixabay reused=true asset_id=%s", asset.id)
        return asset_view(asset)
    cached = session.get(MediaSearchCache, request.search_id)
    if not cached or cached.expires_at <= now():
        raise DomainError(
            "PIXABAY_SELECTION_EXPIRED", "Search again and select a stock result", 409
        )
    candidate = next(
        (
            row
            for row in cached.payload["items"]
            if row["provider_asset_id"] == request.provider_asset_id
            and row["media_type"] == request.media_type
        ),
        None,
    )
    if not candidate:
        raise DomainError("PIXABAY_SELECTION_INVALID", "Select a result from the stock search", 422)
    session.rollback()
    settings = get_settings()
    with tempfile.TemporaryDirectory(prefix="adjacent-stock-") as temp:
        path = Path(temp) / "download"
        PixabayMediaProvider(settings).download(candidate, path)
        asset = VideoService(session, settings).ingest(
            brand_id,
            path,
            "BACKGROUND",
            ", ".join(candidate["tags"])[:500],
            candidate["tags"],
            source={
                "source_type": "PIXABAY",
                "provider": "PIXABAY",
                "source_identity": f"{request.media_type}:{request.provider_asset_id}",
                "provider_asset_id": request.provider_asset_id,
                "expected_media_type": request.media_type,
                "source_url": candidate["source_url"],
                "creator": candidate["creator"],
                "license_name": candidate["license_name"],
                "license_url": candidate["license_url"],
            },
        )
    logger.info("media_acquired source=pixabay reused=false asset_id=%s", asset.id)
    return asset_view(asset)


@router.get(
    "/brands/{brand_id}/media/{asset_id}",
    responses={200: {"model": MediaRecord}},
    tags=[WORKSPACE_WORKFLOW],
)
def get_media(brand_id: UUID, asset_id: UUID, session: DB):
    return asset_view(scoped_asset(brand_id, asset_id, session))


@router.get("/brands/{brand_id}/media/{asset_id}/thumbnail")
def asset_thumbnail(brand_id: UUID, asset_id: UUID, session: DB):
    return object_response(scoped_asset(brand_id, asset_id, session).thumbnail_key, "image/jpeg")


@router.post(
    "/concepts/{concept_id}/video-specs",
    status_code=201,
    responses={201: {"model": VideoSpecRecord}},
    tags=[WORKSPACE_WORKFLOW],
)
def create_spec(concept_id: UUID, request: CreateVideoSpec, session: DB):
    return view(VideoService(session, get_settings()).create_spec(concept_id, request))


@router.get(
    "/video-specs/{spec_id}",
    responses={200: {"model": VideoSpecRecord}},
    tags=[WORKSPACE_WORKFLOW],
)
def get_spec(spec_id: UUID, session: DB):
    return view(require(session, VideoSpec, spec_id))


@router.patch(
    "/video-specs/{spec_id}",
    responses={200: {"model": VideoSpecRecord}},
    tags=[WORKSPACE_WORKFLOW],
)
def update_spec(spec_id: UUID, request: VideoSpecPatch, session: DB):
    return view(VideoService(session, get_settings()).edit_spec(spec_id, request))


@router.post(
    "/video-specs/{spec_id}/renders",
    status_code=202,
    responses={202: {"model": RenderJobRecord}},
    tags=[WORKSPACE_WORKFLOW],
)
def enqueue(spec_id: UUID, request: RenderRequest, session: DB):
    job = VideoService(session, get_settings()).enqueue(spec_id, request.expected_revision)
    return job_view(job, session)


def job_view(job, session):
    values = {
        key: val for key, val in view(job).items() if key not in {"lease_token", "lease_until"}
    }
    artifact = session.get(VideoArtifact, job.id)
    if artifact:
        values["artifact"] = {
            "metadata": artifact.metadata_json,
            "created_at": artifact.created_at,
            "play_url": f"/v1/renders/{job.id}/video",
            "download_url": f"/v1/renders/{job.id}/video?download=true",
            "preview_url": f"/v1/renders/{job.id}/preview",
        }
    return values | {"poll_url": f"/v1/renders/{job.id}"}


@router.get(
    "/brands/{brand_id}/renders", response_model=Page[RenderRecord], tags=["workspace-dashboard"]
)
def list_renders(
    brand_id: UUID,
    session: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[RenderStatus | None, Query()] = None,
    spec_id: UUID | None = None,
):
    """List render summaries for a brand without returning storage paths or job leases."""
    require(session, Brand, brand_id)
    statement = (
        select(RenderJob, VideoArtifact.id, CreativeConcept.content["title"].astext)
        .join(VideoSpec, RenderJob.spec_id == VideoSpec.id)
        .join(Campaign, VideoSpec.campaign_id == Campaign.id)
        .join(CreativeConcept, VideoSpec.concept_id == CreativeConcept.id)
        .outerjoin(VideoArtifact, VideoArtifact.id == RenderJob.id)
        .where(Campaign.brand_id == brand_id)
    )
    if status:
        statement = statement.where(RenderJob.status == status)
    if spec_id:
        statement = statement.where(RenderJob.spec_id == spec_id)
    rows = session.execute(
        statement.order_by(RenderJob.created_at.desc(), RenderJob.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return {
        "items": [
            RenderRecord(
                id=job.id,
                spec_id=job.spec_id,
                spec_revision=job.spec_revision,
                status=job.status,
                concept_title=title or "Video render",
                duration_seconds=job.inputs["duration_frames"] / job.inputs["fps"],
                preview_url=f"/v1/renders/{job.id}/preview" if artifact_id else None,
                created_at=job.created_at,
            )
            for job, artifact_id, title in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/renders/{job_id}",
    responses={200: {"model": RenderJobRecord}},
    tags=[WORKSPACE_WORKFLOW],
)
def get_job(job_id: UUID, session: DB):
    return job_view(require(session, RenderJob, job_id), session)


def object_response(key, content_type, byte_range=None, disposition=None):
    """Stream one HTTP byte range for seekable playback."""
    storage = LocalStorage(get_settings().media_storage_path)
    size = storage.size(key)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if disposition:
        headers["Content-Disposition"] = disposition
    start, end, status = 0, size - 1, 200
    if byte_range:
        bounds = re.fullmatch(r"bytes=(\d{0,18})-(\d{0,18})", byte_range)
        if bounds and bounds[1]:
            start = int(bounds[1])
            end = min(end, int(bounds[2])) if bounds[2] else end
        elif bounds and bounds[2]:
            start = max(0, size - int(bounds[2]))
        else:
            start = size
        if start > end:
            return Response(
                status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"}
            )
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(end - start + 1)
    return StreamingResponse(
        storage.stream(key, start, end - start + 1),
        media_type=content_type,
        headers=headers,
        status_code=status,
    )


@router.get("/renders/{job_id}/video")
def video(
    job_id: UUID,
    session: DB,
    download: bool = False,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
):
    artifact = require(session, VideoArtifact, job_id)
    mode = "attachment" if download else "inline"
    return object_response(
        artifact.output_key, "video/mp4", range_header, f'{mode}; filename="{job_id}.mp4"'
    )


@router.get("/renders/{job_id}/preview")
def preview(job_id: UUID, session: DB):
    return object_response(require(session, VideoArtifact, job_id).thumbnail_key, "image/jpeg")


@router.get("/video-specs/{spec_id}/media/{asset_id}")
def preview_spec_asset(
    spec_id: UUID,
    asset_id: UUID,
    session: DB,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
):
    """Stream only media bound to this saved spec for private composition preview."""
    spec = require(session, VideoSpec, spec_id)
    plan = VideoPlan.model_validate(spec.payload)
    refs = [scene.asset for scene in plan.products]
    if plan.background:
        refs.append(plan.background)
    ref = next((entry for entry in refs if entry.asset_id == asset_id), None)
    if ref is None:
        raise DomainError("MEDIA_NOT_FOUND", "This media is not in the saved video setup", 404)
    asset = scoped_asset(plan.brand_id, asset_id, session)
    if (
        asset.status != "READY"
        or asset.details.get("usage_permission") != "CAMPAIGN_RENDER"
        or VideoService.reference(asset) != ref
    ):
        raise DomainError("MEDIA_CHANGED", "The media no longer matches the saved setup", 409)
    content_type = {
        "mp4": "video/mp4",
        "webm": "video/webm",
        "jpg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
    }[ref.extension]
    return object_response(ref.object_key, content_type, range_header, "inline")
