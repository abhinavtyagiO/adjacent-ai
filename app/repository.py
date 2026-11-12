"""Persistence operations. Callers own transaction boundaries and business decisions."""

import uuid
from datetime import timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import Brand, ExtractionJob, Offering, Profile, SourceDocument, now
from app.schemas import BIP


class Repository:
    def __init__(self, session: Session):
        self.session = session

    def brand(self, brand_id: uuid.UUID, lock: bool = False) -> Brand | None:
        query = select(Brand).where(Brand.id == brand_id)
        if lock:
            query = query.with_for_update()
        return self.session.scalar(query)

    def profile(self, brand_id: uuid.UUID) -> Profile | None:
        return self.session.get(Profile, brand_id)

    def offerings(self, brand_id: uuid.UUID) -> list[Offering]:
        return list(
            self.session.scalars(
                select(Offering).where(Offering.brand_id == brand_id).order_by(Offering.position)
            )
        )

    def bip(self, profile: Profile) -> BIP:
        return BIP.model_validate(
            {
                **profile.sections,
                "offerings": [
                    {"name": offering.name, **offering.details}
                    for offering in self.offerings(profile.brand_id)
                ],
            }
        )

    def write_bip(self, brand_id: uuid.UUID, profile: Profile, bip: BIP):
        profile.sections = bip.model_dump(mode="json", exclude={"offerings"})
        existing = self.offerings(brand_id)
        # Reuse relational IDs for unchanged offering names. New/renamed offerings get new IDs.
        by_name: dict[str, list[Offering]] = {}
        for row in existing:
            by_name.setdefault(row.name, []).append(row)
            row.position = -row.position - 1
        self.session.flush()  # Free the unique nonnegative positions before reorder.
        retained = set()
        for position, offering in enumerate(bip.offerings):
            candidates = by_name.get(offering.name, [])
            row = candidates.pop(0) if candidates else Offering(brand_id=brand_id)
            row.name = offering.name
            row.position = position
            row.details = offering.model_dump(mode="json", exclude={"name"})
            self.session.add(row)
            if row.id:
                retained.add(row.id)
        for row in existing:
            if row.id not in retained:
                self.session.delete(row)

    def active_job(self, brand_id: uuid.UUID) -> ExtractionJob | None:
        return self.session.scalar(
            select(ExtractionJob).where(
                ExtractionJob.brand_id == brand_id, ExtractionJob.status.in_(["PENDING", "RUNNING"])
            )
        )

    def claim_job(self, lease_seconds: int, max_attempts: int) -> ExtractionJob | None:
        """Atomic lease acquisition. SKIP LOCKED permits multiple worker processes.

        Expired leases recover after crashes. Tokens fence late workers from writes.
        Exhausted abandoned jobs are made terminal so they cannot block a brand forever.
        """
        current = now()
        stale = and_(ExtractionJob.status == "RUNNING", ExtractionJob.lease_until < current)
        exhausted = (
            select(ExtractionJob)
            .where(stale, ExtractionJob.attempts >= max_attempts)
            .with_for_update(skip_locked=True)
        )
        for job in self.session.scalars(exhausted):
            job.status = "FAILED"
            job.finished_at = current
            job.lease_token = None
            job.lease_until = None
            job.error = {
                "code": "WORKER_LOST",
                "message": "Worker lease expired; attempts exhausted",
            }
        job = self.session.scalar(
            select(ExtractionJob)
            .where(
                or_(
                    and_(ExtractionJob.status == "PENDING", ExtractionJob.available_at <= current),
                    stale,
                ),
                ExtractionJob.attempts < max_attempts,
            )
            .order_by(ExtractionJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job:
            job.status = "RUNNING"
            job.attempts += 1
            job.started_at = current
            job.lease_token = uuid.uuid4()
            job.lease_until = current + timedelta(seconds=lease_seconds)
            job.error = None
        return job

    def source_documents(self, brand_id: uuid.UUID) -> list[SourceDocument]:
        return list(
            self.session.scalars(
                select(SourceDocument)
                .where(SourceDocument.brand_id == brand_id)
                .order_by(SourceDocument.fetched_at.desc())
            )
        )
