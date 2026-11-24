"""One lease, up to two calls, append-only candidates and an evaluation checkpoint.

Expired paid leases are terminal. Transactions only wrap local state changes; lease tokens
and campaign/BIP revisions fence every publishing write, including generator checkpoints.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from pydantic import ValidationError
from sqlalchemy import func, select

from app.creative.models import Campaign, CreativeConcept, CreativeJob, ResearchSnapshot
from app.creative.provider import CreativeProvider
from app.creative.schemas import EvaluatorResult, GeneratorResult
from app.creative.validation import add_evaluator_flags, validate_candidates, validate_evaluation
from app.errors import DomainError
from app.intelligence import metrics
from app.models import Brand, now

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreativeClaim:
    job_id: uuid.UUID
    token: uuid.UUID


class CreativeWorker:
    def __init__(self, factory, settings, provider):
        self.factory, self.settings, self.provider = factory, settings, provider

    def claim(self):
        with self.factory.begin() as session:
            for job in session.scalars(
                select(CreativeJob)
                .where(CreativeJob.status == "RUNNING", CreativeJob.lease_until < now())
                .with_for_update(skip_locked=True)
            ):
                has_candidates = session.scalar(
                    select(func.count())
                    .select_from(CreativeConcept)
                    .where(CreativeConcept.job_id == job.id)
                )
                job.status = "PARTIAL" if has_candidates else "FAILED"
                job.finished_at = now()
                job.lease_token = job.lease_until = None
                job.error = {
                    "code": "WORKER_LOST",
                    "message": "Creative lease expired; no paid retry was scheduled",
                }
                stage = job.diagnostics.get("stage", "unknown")
                job.metrics = {
                    **job.metrics,
                    "usage_complete": False,
                    "unrecorded_usage_possible": stage in {"generator", "evaluator"},
                    "usage_gap_reason": "Worker lost; only checkpointed usage is known",
                }
                job.diagnostics = {
                    **job.diagnostics,
                    "stage": "finished",
                    "failure_stage": stage,
                    **(
                        {f"{stage}_status": "FAILED"} if stage in {"generator", "evaluator"} else {}
                    ),
                }
            job = session.scalar(
                select(CreativeJob)
                .where(CreativeJob.status == "PENDING", CreativeJob.attempts == 0)
                .order_by(CreativeJob.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            job.status, job.attempts, job.started_at = "RUNNING", 1, now()
            job.lease_token = uuid.uuid4()
            job.lease_until = now() + timedelta(seconds=self.settings.job_lease_seconds)
            return CreativeClaim(job.id, job.lease_token)

    @staticmethod
    def owned(session, claim):
        job = session.scalar(
            select(CreativeJob)
            .where(
                CreativeJob.id == claim.job_id,
                CreativeJob.status == "RUNNING",
                CreativeJob.lease_token == claim.token,
                CreativeJob.lease_until > now(),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if not job:
            raise DomainError("LEASE_LOST", "Creative lease expired or was reassigned", 409)
        return job

    def current(self, session, claim):
        # Do not read network data or hold these locks over inference.
        original = session.get(CreativeJob, claim.job_id)
        parent = session.get(Campaign, original.campaign_id)
        brand = session.scalar(
            select(Brand)
            .where(Brand.id == parent.brand_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        campaign = session.scalar(
            select(Campaign)
            .where(Campaign.id == parent.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        job = self.owned(session, claim)
        research = session.get(ResearchSnapshot, job.research_id)
        if (
            campaign.revision != research.campaign_revision
            or brand.revision != research.brand_revision
        ):
            raise DomainError(
                "RESEARCH_STALE",
                "Brand or brief changed during generation; no result published",
                409,
            )
        return job, research

    def record_usage(self, job, provider):
        records = getattr(provider, "usage_records", []) if provider else []
        job.metrics = metrics(records, self.settings, (now() - job.started_at).total_seconds())
        if not records:
            job.metrics = {
                **job.metrics,
                "billing_source": "chatgpt_plan" if job.chatgpt_account_id else "none",
                "cost_basis": "No inference usage was recorded; no fallback billing",
            }

    def finish_error(self, claim, error, stage, provider):
        with self.factory.begin() as session:
            try:
                job = self.owned(session, claim)
            except DomainError:
                return
            count = session.scalar(
                select(func.count())
                .select_from(CreativeConcept)
                .where(CreativeConcept.job_id == job.id)
            )
            job.status = "PARTIAL" if count else "FAILED"
            job.error = {"code": error.code, "message": error.message}
            job.diagnostics = {
                **job.diagnostics,
                "stage": "finished",
                "failure_stage": stage,
                **({f"{stage}_status": "FAILED"} if stage in {"generator", "evaluator"} else {}),
            }
            self.record_usage(job, provider)
            job.finished_at = now()
            job.lease_token = job.lease_until = None

    async def process_once(self):
        claim = self.claim()
        if not claim:
            return False
        stage, adapter = "preflight", None
        logger.info("creative_started job_id=%s", claim.job_id)
        try:
            async with asyncio.timeout(self.settings.job_timeout_seconds):
                with self.factory.begin() as session:
                    job, research = self.current(session, claim)
                    payload, account_id, model = research.payload, job.chatgpt_account_id, job.model
                    job.diagnostics = {
                        "stage": "preflight",
                        "generator_status": "SKIPPED",
                        "evaluator_status": "SKIPPED",
                    }
                provider = self.provider
                bind = getattr(provider, "for_job", None)
                if bind:
                    provider = bind(account_id, model, model)
                adapter = CreativeProvider(provider, self.settings, model)
                stage = "generator"
                with self.factory.begin() as session:
                    job, _ = self.current(session, claim)
                    job.diagnostics = {
                        **job.diagnostics,
                        "stage": stage,
                        "generator_status": "RUNNING",
                    }
                generated = GeneratorResult.model_validate(await adapter.generate(payload))
                accepted, diagnostics = validate_candidates(generated, payload)
                with self.factory.begin() as session:
                    job, _ = self.current(session, claim)
                    for candidate, validation in accepted:
                        content = candidate.model_dump(mode="json")
                        session.add(
                            CreativeConcept(
                                campaign_id=job.campaign_id,
                                job_id=job.id,
                                candidate_key=candidate.candidate_key,
                                content=content,
                                original_content=content,
                                validation=validation,
                            )
                        )
                    job.diagnostics = {
                        **diagnostics,
                        "stage": "evaluator" if accepted else "finished",
                        "generator_status": "SUCCEEDED" if accepted else "INSUFFICIENT_DATA",
                        "evaluator_status": "RUNNING" if accepted else "SKIPPED",
                    }
                    self.record_usage(job, adapter)
                    if not accepted:
                        job.status, job.finished_at = "INSUFFICIENT_DATA", now()
                        job.lease_token = job.lease_until = None
                        return True
                # Candidates are committed before the optional evaluator can fail.
                stage = "evaluator"
                evaluated = EvaluatorResult.model_validate(
                    await adapter.evaluate(
                        payload,
                        [
                            {"candidate": c.model_dump(mode="json"), "validation": v}
                            for c, v in accepted
                        ],
                    )
                )
                evaluations = validate_evaluation(evaluated, [c for c, _ in accepted])
                with self.factory.begin() as session:
                    job, _ = self.current(session, claim)
                    concepts = list(
                        session.scalars(
                            select(CreativeConcept)
                            .where(CreativeConcept.job_id == job.id)
                            .with_for_update()
                            .execution_options(populate_existing=True)
                        )
                    )
                    selected = 0
                    for concept in concepts:
                        # Human edits win even if made between generator and evaluator writes.
                        if concept.edited_by_user or concept.revision != 1:
                            continue
                        evaluation = evaluations[concept.candidate_key]
                        concept.evaluation = {
                            **evaluation,
                            "scoring_semantics": (
                                "INTERNAL_CREATIVE_JUDGMENT_NOT_PERFORMANCE_PREDICTION"
                            ),
                        }
                        concept.validation = add_evaluator_flags(concept.validation, evaluation)
                        concept.shortlisted = (
                            evaluation["selected"] and concept.validation["eligible"]
                        )
                        selected += int(concept.shortlisted)
                    job.status = "SUCCEEDED" if selected else "INSUFFICIENT_DATA"
                    job.diagnostics = {
                        **job.diagnostics,
                        "stage": "finished",
                        "evaluator_status": "SUCCEEDED",
                        "shortlisted": selected,
                        "overlap_groups": [
                            group.model_dump(mode="json") for group in evaluated.overlap_groups
                        ],
                        "insufficient_shortlist": selected < 5,
                        "insufficiency_reason": evaluated.insufficiency_reason
                        or (
                            "Fewer than five eligible, distinct concepts were selected"
                            if selected < 5
                            else None
                        ),
                    }
                    job.finished_at = now()
                    job.lease_token = job.lease_until = None
                    self.record_usage(job, adapter)
        except TimeoutError:
            self.finish_error(
                claim,
                DomainError("CREATIVE_TIMEOUT", "Creative job deadline exceeded"),
                stage,
                adapter,
            )
        except ValidationError:
            self.finish_error(
                claim,
                DomainError(
                    "CREATIVE_INVALID_OUTPUT", "Creative response did not match the schema"
                ),
                stage,
                adapter,
            )
        except DomainError as error:
            self.finish_error(claim, error, stage, adapter)
        except Exception as error:
            logger.error(
                "creative_unexpected job_id=%s type=%s", claim.job_id, type(error).__name__
            )
            self.finish_error(
                claim,
                DomainError("CREATIVE_INTERNAL", "Unexpected creative generation failure"),
                stage,
                adapter,
            )
        logger.info("creative_finished job_id=%s", claim.job_id)
        return True
