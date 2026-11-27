"""Media ingestion and deterministic compilation from reviewed, current creative concepts."""

import hashlib
import logging
import math
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import select, text

from app.creative.models import Campaign, CreativeConcept
from app.creative.schemas import Candidate, SingleCaption
from app.creative.service import CampaignService, require, revision, validate
from app.errors import DomainError
from app.models import Brand, now
from app.video.models import MediaAsset, RenderJob, VideoSpec
from app.video.process import MediaProcessor
from app.video.schemas import AssetRef, DisplayOptions, VideoPlan
from app.video.storage import LocalStorage, StorageProvider

logger = logging.getLogger(__name__)


def sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def compile_captions(copy, duration):
    """Allocate half-open frame ranges according to reading length without rewriting copy."""
    if isinstance(copy, SingleCaption):
        lines = [copy.text]
        if copy.closing_brand_message:
            lines.append(copy.closing_brand_message)
    else:
        lines = [beat.text for beat in copy.captions]
    minimums = [math.ceil(30 * max(1.4, len(line) / 21, len(line.split()) / 3.2)) for line in lines]
    if sum(minimums) > duration:
        raise DomainError("CAPTION_TOO_LONG", "The approved captions need more reading time", 422)
    slack, remainder = divmod(duration - sum(minimums), len(lines))
    cursor, captions = 0, []
    for i, (line, minimum) in enumerate(zip(lines, minimums, strict=True)):
        end = cursor + minimum + slack + int(i < remainder)
        captions.append({"text": line, "start_frame": cursor, "end_frame": end})
        cursor = end
    return captions


class VideoService:
    """Manage brand media, compile reviewed copy and queue exact spec revisions."""

    def __init__(self, session, settings, storage: StorageProvider | None = None):
        self.session = session
        self.settings = settings
        self.storage = storage if storage is not None else LocalStorage(settings.media_storage_path)
        self.processor = MediaProcessor(settings.media_process_timeout_seconds)

    def ingest(self, brand_id, path, purpose, description, tags, source=None):
        """Inspect and store a locally staged file. Reuse only within the same brand and rights."""
        require(self.session, Brand, brand_id)
        if not 0 < path.stat().st_size <= self.settings.media_max_bytes:
            raise DomainError("MEDIA_SIZE", "The file is empty or exceeds the upload limit", 413)
        content_hash = sha256(path)
        origin = source or {"source_type": "UPLOAD", "provider": None, "source_identity": ""}
        statement = select(MediaAsset).where(
            MediaAsset.brand_id == brand_id,
            MediaAsset.content_hash == content_hash,
            MediaAsset.source_type == origin["source_type"],
            MediaAsset.purpose == purpose,
            MediaAsset.source_identity == origin["source_identity"],
        )
        existing = self.session.scalar(statement)
        if existing:
            return existing
        self.session.rollback()  # Decode outside the database transaction.
        with tempfile.TemporaryDirectory(prefix="adjacent-ingest-") as folder:
            details, derivative, thumb = self.processor.process(path, Path(folder))
            if source and details["media_type"] != source["expected_media_type"]:
                raise DomainError(
                    "MEDIA_TYPE_MISMATCH", "Downloaded media differs from the selection", 422
                )
            if purpose in {"PRODUCT", "BRAND"} and details["media_type"] != "IMAGE":
                raise DomainError(
                    "MEDIA_TYPE_MISMATCH", "Product and brand assets must be images", 422
                )
            asset_id = uuid.uuid4()
            base = f"media/{brand_id}/{asset_id}"
            original_key = f"{base}/original.{details['extension']}"
            normalized_key = f"{base}/normalized.mp4" if derivative else None
            thumbnail_key = f"{base}/thumbnail.jpg"
            row = MediaAsset(
                id=asset_id,
                brand_id=brand_id,
                purpose=purpose,
                content_hash=content_hash,
                media_type=details["media_type"],
                source_type=origin["source_type"],
                source_identity=origin["source_identity"],
                original_key=original_key,
                normalized_key=normalized_key,
                thumbnail_key=thumbnail_key,
                details={
                    **details,
                    **origin,
                    "description": description,
                    "tags": tags,
                    "ownership_scope": str(brand_id),
                    "usage_permission": "CAMPAIGN_RENDER",
                    "render_hash": sha256(derivative) if derivative else content_hash,
                },
            )
            # Serialize the deduplication decision across concurrent uploads.
            self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {
                    "key": f"{brand_id}/{content_hash}/{row.source_type}/{purpose}/"
                    f"{row.source_identity}"
                },
            )
            if existing := self.session.scalar(statement):
                self.session.commit()
                return existing
            stored = []
            try:
                for item, key in [
                    (path, original_key),
                    (thumb, thumbnail_key),
                    (derivative, normalized_key),
                ]:
                    if item is not None:
                        with item.open("rb") as handle:
                            self.storage.write(key, handle)
                        stored.append(key)
                self.session.add(row)
                self.session.flush()
            except Exception:
                self.session.rollback()
                for key in stored:
                    self.storage.delete(key)
                raise
            self.session.commit()
            logger.info(
                "media_acquired source=%s asset_id=%s bytes=%s",
                row.source_type,
                row.id,
                details["file_bytes"],
            )
            return row

    def approved_handoff(self, concept_id, expected):
        """Lock in the existing creative-review order and require production approval."""
        original = require(self.session, CreativeConcept, concept_id)
        campaign = require(self.session, Campaign, original.campaign_id)
        require(self.session, Brand, campaign.brand_id, True)
        require(self.session, Campaign, campaign.id, True)
        concept = require(self.session, CreativeConcept, concept_id, True)
        revision(concept, expected)
        if (
            concept.state != "APPROVED"
            or not concept.validation.get("eligible")
            or any(flag.get("severity") == "BLOCK" for flag in concept.validation.get("flags", []))
            or not all(
                concept.review.get(key) is True
                for key in (
                    "human_review_completed",
                    "acknowledge_claim_risks",
                    "acknowledge_asset_requirements",
                )
            )
        ):
            raise DomainError(
                "PRODUCTION_REVIEW_REQUIRED", "Review and approve the concept first", 409
            )
        return CampaignService(self.session, self.settings).handoff(concept_id)

    def asset(self, brand_id, asset_id, media_type, purpose):
        row = self.session.scalar(
            select(MediaAsset).where(MediaAsset.id == asset_id, MediaAsset.brand_id == brand_id)
        )
        if not row:
            raise DomainError("MEDIA_NOT_FOUND", "Media is unavailable for this brand", 404)
        if (
            row.status != "READY"
            or row.media_type != media_type
            or row.purpose != purpose
            or row.details.get("usage_permission") != "CAMPAIGN_RENDER"
        ):
            raise DomainError(
                "MEDIA_NOT_AUTHORIZED", "Media is not ready or authorized for this use", 422
            )
        return row

    def product_asset(self, brand_id, asset_id):
        asset = self.asset(brand_id, asset_id, "IMAGE", "PRODUCT")
        if asset.source_type != "UPLOAD":
            raise DomainError(
                "PRODUCT_RIGHTS_REQUIRED", "Use a customer-authorized product upload", 422
            )
        if min(asset.details["width"], asset.details["height"]) < 240:
            raise DomainError(
                "PRODUCT_RESOLUTION", "Choose a photo at least 240 pixels per side", 422
            )
        return asset

    @staticmethod
    def reference(asset):
        return AssetRef(
            asset_id=asset.id,
            content_hash=asset.content_hash,
            render_hash=asset.details["render_hash"],
            object_key=asset.normalized_key or asset.original_key,
            extension="mp4" if asset.normalized_key else asset.details["extension"],
        )

    @staticmethod
    def validate_background(asset, frames, audio):
        details = asset.details.get("normalized") or asset.details
        if details["duration_seconds"] * 30 + 0.01 < frames:
            raise DomainError(
                "BACKGROUND_TOO_SHORT", "Choose a video at least as long as the ad", 422
            )
        if min(details["width"], details["height"]) < 240:
            raise DomainError(
                "BACKGROUND_RESOLUTION", "Choose a video at least 240 pixels per side", 422
            )
        if audio == "BACKGROUND" and not details["audio_streams"]:
            raise DomainError("BACKGROUND_AUDIO_MISSING", "The chosen background has no audio", 422)

    def create_spec(self, concept_id, request):
        handoff = self.approved_handoff(concept_id, request.expected_concept_revision)
        if not request.confirm_media_use:
            raise DomainError(
                "MEDIA_PERMISSION_REQUIRED", "Confirm permission to use the media", 422
            )
        copy = self.approved_copy(handoff)
        if request.final_captions is not None and request.final_captions != copy:
            raise DomainError(
                "CAPTION_OVERRIDE", "Edit and reapprove the concept to change copy", 422
            )
        options = DisplayOptions.model_validate(
            request.model_dump(include=set(DisplayOptions.model_fields))
        )
        self.validate_overlays(handoff, options)
        frames = handoff["brief"]["duration_seconds"] * 30
        background = None
        products = []
        if request.template == "TEXT_OVER_BROLL":
            asset = self.asset(
                handoff["brand_id"], request.background_asset_id, "VIDEO", "BACKGROUND"
            )
            self.validate_background(asset, frames, options.audio)
            background = self.reference(asset)
        else:
            count = len(request.product_asset_ids)
            if frames // count < 40:
                raise DomainError(
                    "PRODUCT_TIMELINE_TOO_SHORT", "Use fewer photos or more time", 422
                )
            for i, asset_id in enumerate(request.product_asset_ids):
                asset = self.product_asset(handoff["brand_id"], asset_id)
                products.append(
                    {
                        "asset": self.reference(asset),
                        "start_frame": i * frames // count,
                        "end_frame": (i + 1) * frames // count,
                    }
                )
        plan = VideoPlan(
            **options.model_dump(),
            template=request.template,
            brand_id=handoff["brand_id"],
            campaign_id=handoff["campaign_id"],
            campaign_revision=handoff["campaign_revision"],
            concept_id=concept_id,
            concept_revision=request.expected_concept_revision,
            background=background,
            products=products,
            duration_frames=frames,
            caption_treatment=copy.treatment,
            captions=compile_captions(copy, frames),
        )
        row = VideoSpec(
            concept_id=concept_id,
            campaign_id=plan.campaign_id,
            payload=plan.model_dump(mode="json"),
        )
        self.session.add(row)
        self.session.commit()
        logger.info("video_spec_compiled spec_id=%s template=%s", row.id, plan.template)
        return row

    @staticmethod
    def approved_copy(handoff):
        """Use only structured captions from the production-approved concept revision."""
        if not handoff["concept"].get("on_screen_copy"):
            raise DomainError(
                "FINAL_CAPTIONS_REQUIRED",
                "Add structured captions to the concept and approve that revision",
                422,
            )
        return validate(Candidate, handoff["concept"]).on_screen_copy

    @staticmethod
    def validate_overlays(handoff, options):
        if options.cta_overlay and options.cta_overlay != handoff["concept"]["cta"]:
            raise DomainError("CTA_OVERRIDE", "Use the approved CTA exactly as written", 422)
        if options.brand_label and len(options.brand_label) > 40:
            raise DomainError(
                "BRAND_OVERLAY_TOO_LONG", "Use a brand label of up to 40 characters", 422
            )
        if options.brand_label and options.brand_label != handoff["review"].get(
            "approved_brand_label"
        ):
            raise DomainError(
                "BRAND_OVERLAY_NOT_APPROVED",
                "Use the brand name recorded at concept approval, or leave it blank",
                422,
            )
        if options.cta_overlay and len(options.cta_overlay) > 60:
            raise DomainError("CTA_TOO_LONG", "The approved CTA is too long for this overlay", 422)

    def current(self, plan):
        handoff = self.approved_handoff(plan.concept_id, plan.concept_revision)
        if (
            handoff["brand_id"] != plan.brand_id
            or handoff["campaign_id"] != plan.campaign_id
            or handoff["campaign_revision"] != plan.campaign_revision
            or handoff["brief"]["duration_seconds"] * plan.fps != plan.duration_frames
        ):
            raise DomainError(
                "VIDEO_SPEC_STALE", "Recompile the video from the current concept", 409
            )
        copy = self.approved_copy(handoff)
        captions = [caption.model_dump() for caption in plan.captions]
        if copy.treatment != plan.caption_treatment or captions != compile_captions(
            copy, plan.duration_frames
        ):
            raise DomainError(
                "CAPTION_OVERRIDE", "The video does not match the approved caption copy", 422
            )
        self.validate_overlays(handoff, plan)
        if plan.background:
            asset = self.asset(plan.brand_id, plan.background.asset_id, "VIDEO", "BACKGROUND")
            if self.reference(asset) != plan.background:
                raise DomainError(
                    "MEDIA_CHANGED", "The frozen media no longer matches the asset", 409
                )
            self.validate_background(asset, plan.duration_frames, plan.audio)
        for scene in plan.products:
            if (
                self.reference(self.product_asset(plan.brand_id, scene.asset.asset_id))
                != scene.asset
            ):
                raise DomainError("MEDIA_CHANGED", "The frozen product photo changed", 409)
        return handoff

    def edit_spec(self, spec_id, request):
        row = require(self.session, VideoSpec, spec_id, True)
        revision(row, request.expected_revision)
        changes = request.changes.model_dump(exclude_unset=True)
        if changes.get("audio") == "BACKGROUND" and not request.confirm_media_use:
            raise DomainError(
                "MEDIA_PERMISSION_REQUIRED", "Confirm permission to use the audio", 422
            )
        updated = validate(VideoPlan, {**row.payload, **changes})
        self.current(updated)
        row.payload, row.updated_at = updated.model_dump(mode="json"), now()
        row.revision += 1
        self.session.commit()
        return row

    def enqueue(self, spec_id, expected):
        spec = require(self.session, VideoSpec, spec_id, True)
        revision(spec, expected)
        plan = VideoPlan.model_validate(spec.payload)
        self.current(plan)
        active = self.session.scalar(
            select(RenderJob).where(
                RenderJob.spec_id == spec_id, RenderJob.status.in_(["PENDING", "RUNNING"])
            )
        )
        if active:
            if active.spec_revision != expected:
                raise DomainError("RENDER_ACTIVE_STALE", "Wait for the prior render to stop", 409)
            return active
        job = RenderJob(
            spec_id=spec_id,
            spec_revision=expected,
            inputs=spec.payload,
            diagnostics={"stage": "queued"},
        )
        self.session.add(job)
        self.session.commit()
        return job
