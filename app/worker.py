"""Durable worker: claim -> crawl -> persist sources -> analyze -> validate -> save DRAFT.

No network work runs inside an HTTP request or an open database transaction. A hard
job timeout is shorter than the lease. Tokens and revisions fence stale completions.
"""

import asyncio
import hashlib
import logging
import signal
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select

from app.config import Settings, get_settings
from app.crawling.website import WebsiteSource
from app.db import session_factory
from app.enrichment import Document, EnrichmentSource, SourceBundle
from app.errors import DomainError
from app.intelligence import metrics, run_pipeline
from app.llm.base import LLMProvider, build_provider
from app.models import ExtractionJob, Profile, SourceDocument, now
from app.repository import Repository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Claim:
    job_id: uuid.UUID
    brand_id: uuid.UUID
    token: uuid.UUID
    website_url: str
    user_name: str | None
    selected_offering_key: str | None = None
    chatgpt_account_id: uuid.UUID | None = None
    factual_model: str | None = None
    marketing_model: str | None = None
    kind: str = "EXTRACTION"
    source_job_id: uuid.UUID | None = None
    replace_reviewed: bool = False


class ExtractionWorker:
    def __init__(
        self, factory, settings: Settings, source: EnrichmentSource, provider: LLMProvider
    ):
        self.factory = factory
        self.settings = settings
        self.source = source
        self.provider = provider

    def claim(self) -> Claim | None:
        with self.factory.begin() as session:
            repo = Repository(session)
            job = repo.claim_job(self.settings.job_lease_seconds, self.settings.job_max_attempts)
            if not job:
                return None
            brand = repo.brand(job.brand_id)
            profile = repo.profile(brand.id)
            # Preserve an explicitly user-entered name, but never relabel an extracted name USER.
            name_is_user = profile is None or any(
                item["path"] == "/brand_identity/name" and item["origin"] == "USER"
                for item in profile.provenance
            )
            return Claim(
                job.id,
                brand.id,
                job.lease_token,
                brand.website_url,
                brand.name if name_is_user else None,
                job.selected_offering_key,
                job.chatgpt_account_id,
                job.factual_model,
                job.marketing_model,
                job.kind,
                job.source_job_id,
                job.replace_reviewed,
            )

    @staticmethod
    def owned_job(session, claim: Claim) -> ExtractionJob | None:
        return session.scalar(
            select(ExtractionJob)
            .where(
                ExtractionJob.id == claim.job_id,
                ExtractionJob.status == "RUNNING",
                ExtractionJob.lease_token == claim.token,
                ExtractionJob.lease_until > now(),
            )
            .with_for_update()
        )

    def save_sources(self, claim: Claim, bundle: SourceBundle):
        with self.factory.begin() as session:
            job = self.owned_job(session, claim)
            if not job:
                raise DomainError("LEASE_LOST", "Extraction lease expired or was reassigned")
            for document in bundle.documents:
                session.add(
                    SourceDocument(
                        brand_id=claim.brand_id,
                        job_id=claim.job_id,
                        url=document.url,
                        source_type=document.source_type,
                        title=document.title,
                        content=document.text,
                        content_hash=hashlib.sha256(document.text.encode()).hexdigest(),
                        fetched_at=document.fetched_at,
                        source_metadata=document.metadata,
                    )
                )
            from app.discovery import link_offerings, persist_discovery

            coverage = persist_discovery(
                session,
                claim.brand_id,
                claim.job_id,
                bundle,
                claim.website_url,
                self.settings.catalog_max_products,
            )
            session.flush()
            link_offerings(session, claim.brand_id)
            job.diagnostics = {**job.diagnostics, "discovery": coverage}

    def complete(
        self,
        claim: Claim,
        bip,
        provenance: list[dict],
        warnings: list[str],
        metadata=None,
        checkpoint=False,
    ):
        with self.factory.begin() as session:
            repo = Repository(session)
            brand = repo.brand(claim.brand_id, lock=True)
            job = self.owned_job(session, claim)
            if not job:
                raise DomainError("LEASE_LOST", "Extraction lease expired or was reassigned")
            if brand.revision != job.base_revision:
                raise DomainError(
                    "PROFILE_CHANGED", "Profile changed during extraction; result discarded"
                )
            profile = repo.profile(brand.id)
            if profile is None:
                profile = Profile(brand_id=brand.id)
                session.add(profile)
                # Populate required sections before write_bip performs a flush.
                profile.sections = bip.model_dump(mode="json", exclude={"offerings"})
            # Retain historical strict audits/proof when a replacement switches modes.
            # Do not nest previous history or reinterpret old origins as draft content.
            history = list((profile.pipeline_metadata or {}).get("evidence_validation_history", []))
            previous_audit = (profile.pipeline_metadata or {}).get("evidence_validation", {})
            if any(report.get("available") for report in previous_audit.values()):
                history.append(
                    {
                        "revision": brand.revision,
                        "extracted_at": profile.extracted_at.isoformat()
                        if profile.extracted_at
                        else None,
                        "extraction_mode": profile.pipeline_metadata.get(
                            "extraction_mode", "legacy"
                        ),
                        "evidence_validation": previous_audit,
                        "provenance": profile.provenance,
                    }
                )
            metadata = {**(metadata or {}), "evidence_validation_history": history}
            repo.write_bip(brand.id, profile, bip)
            profile.status = "DRAFT"
            profile.confirmed_at = None
            profile.provenance = provenance
            profile.warnings = warnings
            profile.provider = self.provider.name
            profile.model = self.provider.model
            profile.schema_version = 2
            profile.pipeline_metadata = metadata or {}
            profile.source_job_id = claim.job_id
            from app.discovery import link_offerings

            session.flush()
            link_offerings(session, claim.brand_id)
            job.metrics = (metadata or {}).get("extraction_metrics", {})
            profile.extracted_at = profile.updated_at = now()
            brand.name = bip.brand_identity.name
            brand.revision += 1
            if checkpoint:
                job.base_revision = brand.revision
                return
            job.status = "SUCCEEDED"
            job.finished_at = now()
            job.lease_token = job.lease_until = None
            job.error = None

    def fail(self, claim: Claim, error: DomainError):
        with self.factory.begin() as session:
            job = self.owned_job(session, claim)
            if job is None:
                return
            retry = (
                claim.kind != "MARKETING"
                and error.retryable
                and job.attempts < self.settings.job_max_attempts
            )
            job.status = "PENDING" if retry else "FAILED"
            if claim.kind == "MARKETING":
                job.diagnostics = {**job.diagnostics, "status": "FAILED", "error_code": error.code}
            job.error = {
                "code": error.code,
                "message": error.message,
                **({"details": error.details} if error.details else {}),
            }
            job.metrics = metrics(
                getattr(self.provider, "usage_records", []),
                self.settings,
                (now() - job.started_at).total_seconds() if job.started_at else 0,
            )
            if claim.chatgpt_account_id and not job.metrics.get("calls"):
                job.metrics = {
                    **job.metrics,
                    "billing_source": "chatgpt_plan",
                    "cost_basis": "ChatGPT account bound; no inference request recorded. "
                    "No API-key fallback.",
                }
            job.available_at = now() + timedelta(seconds=min(2**job.attempts, 60))
            job.finished_at = None if retry else now()
            job.lease_token = job.lease_until = None

    async def process_marketing(self, claim):
        import copy

        from app.draft import claim_review, completeness
        from app.intelligence import creative_readiness
        from app.marketing import enrich

        with self.factory.begin() as session:
            repo = Repository(session)
            profile = repo.profile(claim.brand_id)
            brand = repo.brand(claim.brand_id)
            job = session.get(ExtractionJob, claim.job_id)
            if not profile or brand.revision != job.base_revision:
                raise DomainError(
                    "PROFILE_CHANGED", "Profile changed before enrichment; no inference made", 409
                )
            rows = list(
                session.scalars(
                    select(SourceDocument).where(
                        SourceDocument.job_id == claim.source_job_id,
                        SourceDocument.brand_id == claim.brand_id,
                    )
                )
            )
            if not rows:
                raise DomainError(
                    "SOURCE_SNAPSHOT_MISSING",
                    "Saved extraction source snapshot is unavailable",
                    409,
                )
            bip, original = repo.bip(profile), list(profile.provenance)
            reviewed = profile.pipeline_metadata.get("reviewed_paths", [])
            bundle = SourceBundle(
                [
                    Document(
                        r.url, r.content, r.fetched_at, r.title, r.source_type, r.source_metadata
                    )
                    for r in rows
                ]
            )
            from app.discovery import link_offerings, persist_discovery

            coverage = persist_discovery(
                session,
                claim.brand_id,
                claim.source_job_id,
                bundle,
                claim.website_url,
                self.settings.catalog_max_products,
            )
            session.flush()
            link_offerings(session, claim.brand_id)
            job.diagnostics = {
                "discovery": {**coverage, "source_snapshot_id": str(claim.source_job_id)}
            }
        # Only transport/retry mode changes; the authorized job's selected model/effort
        # and configured deadline remain intact. One invocation, no automatic paid retry.
        provider = copy.copy(self.provider)
        if hasattr(provider, "settings"):
            provider.settings = provider.settings.model_copy(
                update={"llm_max_attempts": 1, "extraction_mode": "draft_first"}
            )
        provider.usage_records = []
        self.provider = provider
        updated, provenance, warnings, diagnostics = await enrich(
            provider,
            self.settings,
            bip,
            original,
            bundle,
            claim.selected_offering_key,
            claim.replace_reviewed,
            reviewed,
        )
        with self.factory.begin() as session:
            repo = Repository(session)
            brand = repo.brand(claim.brand_id, lock=True)
            job = self.owned_job(session, claim)
            if not job:
                raise DomainError("LEASE_LOST", "Enrichment lease expired or was reassigned")
            if brand.revision != job.base_revision:
                raise DomainError(
                    "PROFILE_CHANGED", "Profile changed during enrichment; result discarded", 409
                )
            job.status = diagnostics["status"]
            job.diagnostics = {**job.diagnostics, **diagnostics}
            job.metrics = metrics(
                provider.usage_records,
                self.settings,
                sum(r.get("latency_seconds", 0) for r in provider.usage_records),
            )
            job.finished_at = now()
            job.lease_token = job.lease_until = None
            if job.status == "INSUFFICIENT_DATA":
                return  # Valid empty/protected-only response never alters the profile.
            profile = repo.profile(claim.brand_id)
            repo.write_bip(claim.brand_id, profile, updated)
            profile.provenance = provenance
            profile.warnings = [*profile.warnings, *warnings]
            profile.status, profile.confirmed_at = "DRAFT", None
            profile.updated_at = now()
            brand.revision += 1
            metadata = dict(profile.pipeline_metadata)
            if metadata.get("evidence_validation", {}).get("marketing", {}).get("available"):
                metadata["evidence_validation_history"] = [
                    *metadata.get("evidence_validation_history", []),
                    {
                        "revision": brand.revision - 1,
                        "extracted_at": profile.extracted_at.isoformat()
                        if profile.extracted_at
                        else None,
                        "extraction_mode": metadata.get("extraction_mode", "legacy"),
                        "evidence_validation": metadata["evidence_validation"],
                        "provenance": original,
                    },
                ]
            metadata["stages"] = {**metadata.get("stages", {}), "marketing": "SUCCEEDED"}
            metadata["stage_errors"] = {
                k: v for k, v in metadata.get("stage_errors", {}).items() if k != "marketing"
            }
            metadata["evidence_validation"] = {
                **metadata.get("evidence_validation", {}),
                "marketing": {"status": "NOT_APPLICABLE", "available": False},
            }
            metadata["completeness"] = completeness(updated)
            metadata["claim_review"] = claim_review(updated, provenance)
            metadata["creative_readiness"] = creative_readiness(
                updated, claim.selected_offering_key, provenance, mode="draft_first"
            )
            metadata["last_marketing_enrichment"] = {
                "job_id": str(job.id),
                "model": claim.marketing_model,
                "metrics": job.metrics,
                "diagnostics": diagnostics,
            }
            profile.pipeline_metadata = metadata

    async def process_once(self) -> bool:
        claim = self.claim()
        if not claim:
            return False
        logger.info("extraction_started job_id=%s brand_id=%s", claim.job_id, claim.brand_id)
        self.provider.usage_records = []
        try:
            async with asyncio.timeout(self.settings.job_timeout_seconds):
                bind = getattr(self.provider, "for_job", None)
                if bind:
                    self.provider = bind(
                        claim.chatgpt_account_id,
                        claim.marketing_model if claim.kind == "MARKETING" else claim.factual_model,
                        claim.marketing_model,
                    )
                if claim.kind == "MARKETING":
                    await self.process_marketing(claim)
                    return True
                bundle = await self.source.collect(claim.website_url)
                self.save_sources(claim, bundle)
                result = await run_pipeline(
                    self.provider,
                    self.settings,
                    claim.website_url,
                    bundle,
                    claim.user_name,
                    claim.selected_offering_key,
                    factual_checkpoint=lambda result: self.complete(
                        claim,
                        result.profile,
                        result.provenance,
                        result.warnings,
                        result.metadata,
                        checkpoint=True,
                    ),
                )
                if claim.chatgpt_account_id:
                    result.metadata["chatgpt_account_id"] = str(claim.chatgpt_account_id)
                    result.metadata["billing_source"] = "chatgpt_plan"
                self.complete(
                    claim,
                    result.profile,
                    result.provenance,
                    result.warnings,
                    result.metadata,
                )
            logger.info("extraction_succeeded job_id=%s", claim.job_id)
        except TimeoutError:
            error = DomainError(
                "EXTRACTION_TIMEOUT", "Extraction exceeded the job time limit", retryable=True
            )
            self.fail(claim, error)
            logger.warning("extraction_failed job_id=%s code=%s", claim.job_id, error.code)
        except DomainError as error:
            self.fail(claim, error)
            logger.warning("extraction_failed job_id=%s code=%s", claim.job_id, error.code)
        except Exception as error:
            # Log the class, never raw provider errors, credentials or website payloads.
            logger.error(
                "extraction_unexpected job_id=%s type=%s", claim.job_id, type(error).__name__
            )
            self.fail(claim, DomainError("EXTRACTION_INTERNAL", "Unexpected extraction failure"))
        return True


async def serve():
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    factory = session_factory(settings.database_url)
    worker = ExtractionWorker(factory, settings, WebsiteSource(settings), build_provider(settings))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    logger.info("worker_ready provider=%s model=%s", worker.provider.name, worker.provider.model)
    try:
        while not stop.is_set():
            try:
                worked = await worker.process_once()
            except Exception as error:
                logger.error("worker_poll_failed type=%s", type(error).__name__)
                worked = False
            if not worked:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
    finally:
        factory.kw["bind"].dispose()


def main():
    asyncio.run(serve())


if __name__ == "__main__":
    main()
