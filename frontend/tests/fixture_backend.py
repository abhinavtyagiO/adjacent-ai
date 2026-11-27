"""Run the real API, PostgreSQL and render worker with offline inference and sources."""

import asyncio
import os
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urljoin

import uvicorn
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.engine import make_url

from alembic import command
from app.config import get_settings
from app.creative.models import Campaign, CreativeConcept, CreativeJob, ResearchSnapshot
from app.creative.schemas import CampaignBrief
from app.creative.worker import CreativeWorker
from app.db import session_factory
from app.enrichment import Document, SourceBundle
from app.models import Brand, ExtractionJob, Profile, now
from app.schemas import BIP, DraftFactualResult, DraftMarketingResult
from app.video.models import RenderJob, VideoArtifact, VideoSpec
from app.video.pixabay import PixabayMediaProvider, parse_candidates
from app.video.worker import RenderWorker
from app.worker import ExtractionWorker
from tests.creative_helpers import MockCreativeTransport

FIXTURE_BRAND = "https://orbit-stationery.example/"
SECOND_BRAND = "https://green-trail.example/"


def seed(database: str, media: Path):
    """Insert a saved workspace for dashboard and navigation tests."""
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


class FixtureWebsite:
    async def collect(self, website_url):
        product_url = urljoin(website_url, "products/focus-timer")
        return SourceBundle(
            [
                Document(
                    product_url,
                    "Focus Map offers Focus Timer for planning study sessions. "
                    "Focus Timer lets students set a study interval and temporarily block apps.",
                    now(),
                    "Focus Timer",
                    metadata={"products": [{"name": "Focus Timer", "url": product_url}]},
                )
            ],
            coverage={"requested_pages": 1, "saved_pages": 1},
        )


class FixtureOnboarding:
    name = "mock"
    model = "fixture-only"

    async def extract_facts(self, website_url, sources):
        return DraftFactualResult(
            profile={
                "brand_identity": {
                    "name": "Focus Map",
                    "website_url": website_url,
                    "description": "Planning tools for study sessions.",
                },
                "offerings": [
                    {
                        "name": "Focus Timer",
                        "kind": "PRODUCT",
                        "url": sources.documents[0].url,
                        "description": "A timer for planning study sessions",
                        "features": ["Set a study interval", "Temporarily block apps"],
                        "benefits": ["Set aside time to study"],
                    }
                ],
            },
        )

    async def infer_marketing(self, profile, sources, selected_offering_key=None):
        return DraftMarketingResult(
            customer_personas_and_audience_segments={
                "segments": [{"name": "Students", "description": "People planning study time"}],
            },
            customer_psychology={
                "motivations": ["Protect a planned reading session"],
                "objections": ["Need access to essential apps"],
            },
            creative_context={
                "creative_angles": ["Show a study interval with fewer interruptions"]
            },
        )


def fixture_stock(provider, _query, media_type):
    """Use a normalized Pixabay-shaped result without making outbound requests."""
    return parse_candidates(
        {
            "hits": [
                {
                    "id": 190326,
                    "pageURL": "https://pixabay.com/videos/study-desk-190326/",
                    "user": "Fixture contributor",
                    "tags": "study desk, fixture",
                    "duration": 6,
                    "videos": {
                        "medium": {
                            "url": "https://cdn.pixabay.com/video/fixture.mp4",
                            "size": 100000,
                            "width": 360,
                            "height": 640,
                        },
                    },
                },
            ],
        },
        media_type,
        provider.max_bytes,
    ), None


def prepare_stock(clip):
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=0x334454:s=360x640:r=30:d=6",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "2",
            str(clip),
        ],
        check=True,
        timeout=15,
    )


async def drain(process_once, stop):
    while not stop.is_set():
        await process_once()
        await asyncio.sleep(0.15)


def offline_app(database, stock):
    from app.main import create_app

    settings = get_settings()
    factory = session_factory(database)
    extraction = ExtractionWorker(factory, settings, FixtureWebsite(), FixtureOnboarding())
    creative = CreativeWorker(factory, settings, MockCreativeTransport(settings))
    render = RenderWorker(factory, settings)

    async def render_once():
        await asyncio.to_thread(render.process_once)

    def download_stock(_provider, _candidate, destination):
        shutil.copyfile(stock, destination)

    @asynccontextmanager
    async def lifespan(_app):
        stop = asyncio.Event()
        with (
            patch("app.api.routes.extraction_binding", return_value={}),
            patch("app.creative.routes.extraction_binding", return_value={}),
            patch.object(PixabayMediaProvider, "_request_search", fixture_stock),
            patch.object(PixabayMediaProvider, "download", download_stock),
        ):
            tasks = [
                asyncio.create_task(drain(run, stop))
                for run in (extraction.process_once, creative.process_once, render_once)
            ]
            try:
                yield
            finally:
                stop.set()
                await asyncio.gather(*tasks)

    app = create_app()
    app.router.lifespan_context = lifespan
    return app


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
                "EXTRACTION_MODE": "draft_first",
                "LLM_MAX_ATTEMPTS": "1",
                "JOB_MAX_ATTEMPTS": "1",
                "PIXABAY_API_KEY": "browser-fixture-key",
            }
        )
        get_settings.cache_clear()
        command.upgrade(Config("alembic.ini"), "head")
        seed(database, Path(media))
        stock = Path(media) / "fixture-stock.mp4"
        prepare_stock(stock)
        uvicorn.run(offline_app(database, stock), host="127.0.0.1", port=8016, access_log=False)


if __name__ == "__main__":
    main()
