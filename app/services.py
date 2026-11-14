"""Brand onboarding and review rules, independent from HTTP and LLM provider details."""

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import DomainError
from app.models import Brand, ExtractionJob, Profile, now
from app.provenance import corrected_provenance
from app.repository import Repository
from app.schemas import BIP, CreateBrand, ProfilePatch


def merge_profile(base: dict, changes: dict) -> dict:
    """Merge objects recursively; arrays replace atomically; scalar null clears a field.

    Section objects cannot be null: BIP validation keeps all twelve sections present.
    """
    result = dict(base)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_profile(result[key], value)
        else:
            result[key] = value
    return result


class BrandService:
    def __init__(self, session: Session):
        self.session = session
        self.repo = Repository(session)

    def require_brand(self, brand_id: uuid.UUID, lock: bool = False) -> Brand:
        brand = self.repo.brand(brand_id, lock)
        if not brand:
            raise DomainError("BRAND_NOT_FOUND", "Brand was not found", 404)
        return brand

    def require_profile(self, brand_id: uuid.UUID) -> Profile:
        profile = self.repo.profile(brand_id)
        if not profile:
            raise DomainError("PROFILE_NOT_FOUND", "No profile yet; trigger extraction first", 404)
        return profile

    def create(self, data: CreateBrand) -> Brand:
        brand = Brand(website_url=data.website_url, name=data.name)
        self.session.add(brand)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise DomainError("BRAND_EXISTS", "This website is already registered", 409) from exc
        return brand

    def enqueue(
        self,
        brand_id: uuid.UUID,
        replace_existing: bool,
        offering_key: str | None = None,
        chatgpt_account_id=None,
        factual_model=None,
        marketing_model=None,
    ) -> ExtractionJob:
        brand = self.require_brand(brand_id, lock=True)
        active = self.repo.active_job(brand_id)
        if active:
            self.session.commit()
            return active
        if self.repo.profile(brand_id) and not replace_existing:
            raise DomainError(
                "PROFILE_EXISTS", "Use replace_existing=true to extract a new draft", 409
            )
        job = ExtractionJob(
            brand_id=brand_id,
            base_revision=brand.revision,
            selected_offering_key=offering_key,
            chatgpt_account_id=chatgpt_account_id,
            factual_model=factual_model,
            marketing_model=marketing_model,
        )
        self.session.add(job)
        self.session.commit()
        return job

    def read(self, brand_id: uuid.UUID) -> dict:
        from app.intelligence import creative_readiness

        brand = self.require_brand(brand_id)
        profile = self.require_profile(brand_id)
        validation = profile.pipeline_metadata.get("evidence_validation", {})
        stages = dict(profile.pipeline_metadata.get("stages", {}))
        # Older SUCCEEDED marketing outcomes measured JSON parsing, not retained evidence.
        if stages.get("marketing") == "SUCCEEDED" and not validation.get("marketing"):
            stages["marketing"] = "UNKNOWN"
        for stage in ("facts", "marketing"):
            stages.setdefault(stage, "UNKNOWN")
        return {
            "brand_id": brand.id,
            "revision": brand.revision,
            "status": profile.status,
            "schema_version": profile.schema_version,
            "profile": self.repo.bip(profile).model_dump(mode="json"),
            "provenance": profile.provenance,
            "warnings": profile.warnings,
            "provider": profile.provider,
            "model": profile.model,
            "extracted_at": profile.extracted_at,
            "updated_at": profile.updated_at,
            "confirmed_at": profile.confirmed_at,
            "source_coverage": profile.pipeline_metadata.get("source_coverage", {}),
            "creative_readiness": creative_readiness(
                self.repo.bip(profile),
                profile.pipeline_metadata.get("creative_readiness", {}).get(
                    "requested_offering_key"
                )
                or profile.pipeline_metadata.get("creative_readiness", {}).get(
                    "selected_offering_key"
                ),
                profile.provenance,
            ),
            "extraction_metrics": profile.pipeline_metadata.get("extraction_metrics", {}),
            "stages": stages,
            "evidence_validation": validation,
            "stage_errors": profile.pipeline_metadata.get("stage_errors", {}),
        }

    def update(self, brand_id: uuid.UUID, request: ProfilePatch) -> dict:
        brand = self.require_brand(brand_id, lock=True)
        profile = self.require_profile(brand_id)
        self._revision(brand, request.expected_revision)
        old = self.repo.bip(profile)
        try:
            bip = BIP.model_validate(merge_profile(old.model_dump(mode="json"), request.changes))
        except ValidationError as exc:
            paths = [".".join(map(str, item["loc"])) for item in exc.errors()]
            raise DomainError(
                "INVALID_PROFILE", "Invalid profile fields: " + ", ".join(paths), 422
            ) from exc
        if bip.brand_identity.website_url != brand.website_url:
            raise DomainError("IMMUTABLE_WEBSITE", "Registered website_url cannot be changed", 422)
        if old == bip:
            self.session.commit()
            return self.read(brand_id)
        profile.provenance = corrected_provenance(old, bip, profile.provenance)
        self.repo.write_bip(brand_id, profile, bip)
        brand.name = bip.brand_identity.name
        brand.revision += 1
        profile.status = "DRAFT"
        profile.confirmed_at = None
        profile.updated_at = now()
        self.session.commit()
        return self.read(brand_id)

    def confirm(self, brand_id: uuid.UUID, expected_revision: int) -> dict:
        brand = self.require_brand(brand_id, lock=True)
        profile = self.require_profile(brand_id)
        self._revision(brand, expected_revision)
        if not brand.name:
            raise DomainError(
                "NAME_REQUIRED", "A company name is required to confirm the profile", 422
            )
        if profile.status != "CONFIRMED":
            brand.revision += 1
            profile.status = "CONFIRMED"
            profile.confirmed_at = profile.updated_at = now()
            profile.provenance = [
                {**entry, "confirmed_by_user": True, "confirmation_revision": brand.revision}
                for entry in profile.provenance
            ]
        self.session.commit()
        return self.read(brand_id)

    @staticmethod
    def _revision(brand: Brand, expected: int):
        if brand.revision != expected:
            raise DomainError(
                "REVISION_CONFLICT", "Profile changed; fetch the latest revision", 409
            )

    def list_brands(self, limit: int, offset: int) -> list[dict[str, Any]]:
        brands = list(
            self.session.scalars(
                select(Brand).order_by(Brand.created_at, Brand.id).limit(limit).offset(offset)
            )
        )
        if not brands:
            return []
        # Batch-load relationships to keep list queries bounded as the brand count grows.
        from app.models import Offering

        profiles = {
            p.brand_id: p
            for p in self.session.scalars(
                select(Profile).where(Profile.brand_id.in_([b.id for b in brands]))
            )
        }
        offerings: dict[uuid.UUID, list] = {}
        for row in self.session.scalars(
            select(Offering)
            .where(Offering.brand_id.in_([b.id for b in brands]))
            .order_by(Offering.position)
        ):
            offerings.setdefault(row.brand_id, []).append(
                {"id": row.id, "name": row.name, **row.details}
            )
        return [
            {
                "id": b.id,
                "website_url": b.website_url,
                "name": b.name,
                "revision": b.revision,
                "created_at": b.created_at,
                "profile_status": profiles[b.id].status if b.id in profiles else None,
                "offerings": offerings.get(b.id, []),
            }
            for b in brands
        ]
