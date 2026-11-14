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
from app.enrichment import EnrichmentSource, SourceBundle
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
            if not self.owned_job(session, claim):
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

    def complete(
        self, claim: Claim, bip, provenance: list[dict], warnings: list[str], metadata=None
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
            repo.write_bip(brand.id, profile, bip)
            profile.status = "DRAFT"
            profile.confirmed_at = None
            profile.provenance = provenance
            profile.warnings = warnings
            profile.provider = self.provider.name
            profile.model = self.provider.model
            profile.schema_version = 2
            profile.pipeline_metadata = metadata or {}
            job.metrics = (metadata or {}).get("extraction_metrics", {})
            profile.extracted_at = profile.updated_at = now()
            brand.name = bip.brand_identity.name
            brand.revision += 1
            job.status = "SUCCEEDED"
            job.finished_at = now()
            job.lease_token = job.lease_until = None
            job.error = None

    def fail(self, claim: Claim, error: DomainError):
        with self.factory.begin() as session:
            job = self.owned_job(session, claim)
            if job is None:
                return
            retry = error.retryable and job.attempts < self.settings.job_max_attempts
            job.status = "PENDING" if retry else "FAILED"
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
            job.available_at = now() + timedelta(seconds=min(2**job.attempts, 60))
            job.finished_at = None if retry else now()
            job.lease_token = job.lease_until = None

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
                        claim.chatgpt_account_id, claim.factual_model, claim.marketing_model
                    )
                bundle = await self.source.collect(claim.website_url)
                self.save_sources(claim, bundle)
                result = await run_pipeline(
                    self.provider,
                    self.settings,
                    claim.website_url,
                    bundle,
                    claim.user_name,
                    claim.selected_offering_key,
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
