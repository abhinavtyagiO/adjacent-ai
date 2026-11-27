"""Run real FastAPI and disposable PostgreSQL for the Slice 1 browser tests."""

import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.engine import make_url

from alembic import command
from app.config import get_settings
from app.creative.models import Campaign, CreativeConcept, CreativeJob, ResearchSnapshot
from app.creative.schemas import CampaignBrief
from app.db import session_factory
from app.models import Brand, ExtractionJob, Profile
from app.schemas import BIP
from app.video.models import RenderJob, VideoArtifact, VideoSpec

FIXTURE_BRAND = "https://orbit-stationery.example/"
SECOND_BRAND = "https://green-trail.example/"


def seed(database: str, media: Path):
    """Insert only Playwright fixture rows. No onboarding, creative, or render worker runs."""
    preview = media / "fixture-preview.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=0x6251b9:s=180x320",
            "-frames:v",
            "1",
            "-threads",
            "1",
            "-update",
            "1",
            str(preview),
        ],
        check=True,
        timeout=15,
    )
    with session_factory(database).begin() as session:
        if session.scalar(select(Brand.id).where(Brand.website_url == FIXTURE_BRAND)):
            return
        brand = Brand(name="Orbit Stationery", website_url=FIXTURE_BRAND, revision=1)
        session.add(brand)
        session.flush()
        second = Brand(name="Green Trail", website_url=SECOND_BRAND, revision=0)
        source = ExtractionJob(brand_id=brand.id, base_revision=0, status="SUCCEEDED")
        brief = CampaignBrief(
            objective="Awareness", audience="Desk planners", target_platform="Instagram Reels"
        )
        campaign = Campaign(brand_id=brand.id, brief=brief.model_dump(mode="json"), revision=1)
        session.add_all([second, source, campaign])
        session.flush()
        profile = BIP.model_validate(
            {"brand_identity": {"name": brand.name, "website_url": FIXTURE_BRAND}}
        )
        session.add(
            Profile(
                brand_id=brand.id,
                status="DRAFT",
                source_job_id=source.id,
                sections=profile.model_dump(mode="json"),
            )
        )
        research = ResearchSnapshot(
            campaign_id=campaign.id,
            brand_revision=1,
            campaign_revision=1,
            source_job_id=source.id,
            payload={"brief": brief.model_dump(mode="json")},
        )
        session.add(research)
        session.flush()
        generation = CreativeJob(
            campaign_id=campaign.id,
            research_id=research.id,
            status="SUCCEEDED",
            model="fixture",
        )
        session.add(generation)
        session.flush()
        content = {"title": "Make space for a fresh page"}
        concept = CreativeConcept(
            campaign_id=campaign.id,
            job_id=generation.id,
            candidate_key="fresh-page",
            content=content,
            original_content=content,
            validation={"eligible": True},
        )
        session.add(concept)
        session.flush()
        spec = VideoSpec(campaign_id=campaign.id, concept_id=concept.id, payload={})
        session.add(spec)
        session.flush()
        render = RenderJob(
            spec_id=spec.id,
            spec_revision=1,
            status="SUCCEEDED",
            inputs={"duration_frames": 450, "fps": 30},
        )
        session.add(render)
        session.flush()
        session.add(
            VideoArtifact(
                id=render.id,
                output_key="fixture-only.mp4",
                thumbnail_key=preview.name,
                metadata_json={},
            )
        )


def main():
    database = os.environ.get("TEST_DATABASE_URL")
    if not database:
        raise SystemExit(
            "Set TEST_DATABASE_URL to the dedicated PostgreSQL _frontend_test database"
        )
    parsed = make_url(database)
    if parsed.drivername != "postgresql+psycopg" or not parsed.database.endswith("_frontend_test"):
        raise SystemExit(
            "Playwright requires a dedicated PostgreSQL database ending _frontend_test"
        )
    with TemporaryDirectory(prefix="adjacent-frontend-fixture-") as media:
        os.environ.update(
            {
                "DATABASE_URL": database,
                "MEDIA_STORAGE_PATH": media,
                "LLM_PROVIDER": "openai_chatgpt",
            }
        )
        get_settings.cache_clear()
        command.upgrade(Config("alembic.ini"), "head")
        seed(database, Path(media))
        uvicorn.run("app.main:app", host="127.0.0.1", port=8016, access_log=False)


if __name__ == "__main__":
    main()
