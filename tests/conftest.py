import os
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from alembic import command
from app.config import Settings, get_settings
from app.db import get_session, session_factory
from app.enrichment import Document, SourceBundle
from app.main import create_app
from app.models import now
from app.schemas import BIP, Citation, ExtractionResult, LLMEvidence, MarketingResult

WEBSITE = "https://acme.example/"
CONTENT = "Acme makes collaboration software. Flow is our product. Advisory is our service."


@pytest.fixture
def settings():
    return Settings(_env_file=None)


@pytest.fixture
def bundle():
    return SourceBundle([Document(WEBSITE, CONTENT, now(), "Acme")])


@pytest.fixture
def extraction_result():
    profile = BIP.model_validate(
        {
            "brand_identity": {"name": "Acme", "website_url": WEBSITE},
            "offerings": [{"name": "Flow"}, {"name": "Advisory"}],
        }
    )
    evidence = [
        LLMEvidence(
            path=path, origin="WEBSITE", citations=[Citation(source_url=WEBSITE, quote=quote)]
        )
        for path, quote in [
            ("/brand_identity/name", "Acme makes collaboration software."),
            ("/offerings/0/name", "Flow is our product."),
            ("/offerings/1/name", "Advisory is our service."),
        ]
    ]
    return ExtractionResult(profile=profile, evidence=evidence)


class FakeSource:
    def __init__(self, bundle):
        self.bundle = bundle

    async def collect(self, website_url):
        return self.bundle


class FakeProvider:
    name = "test"
    model = "fixture-only"

    def __init__(self, result):
        self.result = result

    async def extract(self, website_url, sources):
        return self.result

    async def extract_facts(self, website_url, sources):
        return await self.extract(website_url, sources)

    async def infer_marketing(self, profile, sources, selected_offering_key=None):
        return MarketingResult()


@pytest.fixture(scope="session")
def database_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated PostgreSQL database ending in _test")
    parsed = make_url(url)
    if parsed.drivername != "postgresql+psycopg" or not parsed.database.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must be PostgreSQL and database name must end in _test")
    # Migrations, never metadata.create_all: tests exercise the shipped schema.
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with patch.dict(os.environ, {"DATABASE_URL": url}):
        get_settings.cache_clear()
        command.upgrade(config, "head")
    get_settings.cache_clear()
    return url


@pytest.fixture
def factory(database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("API_ACCESS_TOKEN", "")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY_BILLING_ENABLED", "true")
    get_settings.cache_clear()
    factory = session_factory(database_url)
    with factory.begin() as session:
        session.execute(
            text("TRUNCATE source_documents, offerings, profiles, extraction_jobs, brands")
        )
    yield factory
    factory.kw["bind"].dispose()
    get_settings.cache_clear()


@pytest.fixture
def client(factory):
    app = create_app()

    def database_session():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = database_session
    with TestClient(app) as client:
        yield client


def compact_facts(result, sources):
    """Encode historical fixtures in the new compact inference wire contract."""
    from app.schemas import CitedFactualResult, FactualOffering, FactualProfile

    data = {
        k: v for k, v in result.profile.model_dump().items() if k in FactualProfile.model_fields
    }
    data["offerings"] = [
        {k: v for k, v in item.items() if k in FactualOffering.model_fields}
        for item in data["offerings"]
    ]
    ids = {doc.url: f"s{i}" for i, doc in enumerate(sources.documents)}
    return CitedFactualResult(
        profile=data,
        citations=[
            {
                "source_id": ids[c.source_url],
                "quote": c.quote,
                "paths": [entry.path],
                "origin": entry.origin,
                "reasoning": entry.reasoning,
            }
            for entry in result.evidence
            for c in entry.citations
        ],
        warnings=result.warnings,
    )
