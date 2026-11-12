import uuid
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.errors import DomainError
from app.models import ExtractionJob, Offering, Profile, SourceDocument, now
from app.repository import Repository
from app.schemas import CreateBrand
from app.services import BrandService
from app.worker import ExtractionWorker
from tests.conftest import FakeProvider, FakeSource

pytestmark = pytest.mark.integration


def register(client, name=None):
    response = client.post("/v1/brands", json={"website_url": "https://acme.example", "name": name})
    assert response.status_code == 201
    return response.json()["id"]


def make_worker(factory, settings, bundle, extraction_result):
    return ExtractionWorker(factory, settings, FakeSource(bundle), FakeProvider(extraction_result))


async def extract(client, worker, brand_id):
    response = client.post(f"/v1/brands/{brand_id}/extractions")
    assert response.status_code == 202
    job_id = response.json()["id"]
    assert await worker.process_once()
    job = client.get(f"/v1/extractions/{job_id}").json()
    assert job["status"] == "SUCCEEDED", job
    return job_id


async def test_full_onboarding_review_edit_confirm_and_relational_offerings(
    client, factory, settings, bundle, extraction_result
):
    worker = make_worker(factory, settings, bundle, extraction_result)
    brand_id = register(client)
    assert client.get(f"/v1/brands/{brand_id}/profile").status_code == 404
    job_id = await extract(client, worker, brand_id)
    draft = client.get(f"/v1/brands/{brand_id}/profile").json()
    assert draft["status"] == "DRAFT" and draft["revision"] == 1
    assert len(draft["profile"]) == 12
    assert draft["profile"]["marketing_defaults"]["budget"] is None
    assert len(draft["profile"]["offerings"]) == 2
    assert draft["extracted_at"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Offering)) == 2
        source = session.scalar(select(SourceDocument))
        assert str(source.job_id) == job_id and len(source.content_hash) == 64
        assert "Acme" in source.content
        # PostgreSQL storage is genuinely JSONB, not a test-only substitute.
        assert session.scalar(text("SELECT pg_typeof(sections)::text FROM profiles")) == "jsonb"
    edited = client.patch(
        f"/v1/brands/{brand_id}/profile",
        json={
            "expected_revision": 1,
            "changes": {"brand_identity": {"name": "Acme Inc"}, "brand_voice": {"tone": ["Warm"]}},
        },
    ).json()
    assert edited["revision"] == 2
    assert edited["profile"]["brand_voice"]["tone"] == ["Warm"]
    assert (
        next(e for e in edited["provenance"] if e["path"] == "/brand_identity/name")["origin"]
        == "USER"
    )
    confirmed = client.post(
        f"/v1/brands/{brand_id}/profile/confirm", json={"expected_revision": 2}
    ).json()
    assert confirmed["status"] == "CONFIRMED" and confirmed["revision"] == 3
    assert confirmed["confirmed_at"]
    listing = client.get("/v1/brands").json()["items"]
    assert listing[0]["name"] == "Acme Inc"
    assert listing[0]["profile_status"] == "CONFIRMED"
    assert len(listing[0]["offerings"]) == 2
    assert client.get(f"/v1/brands/{brand_id}/offerings").json()["items"][0]["name"] == "Flow"
    assert client.get(f"/v1/brands/{brand_id}/sources").json()["items"][0]["fetched_at"]
    assert not await worker.process_once()


async def test_revisions_confirmation_and_edits_preserve_unchanged_offering_ids(
    client, factory, settings, bundle, extraction_result
):
    brand_id = register(client)
    await extract(client, make_worker(factory, settings, bundle, extraction_result), brand_id)
    before = client.get(f"/v1/brands/{brand_id}/offerings").json()["items"]
    assert (
        client.post(
            f"/v1/brands/{brand_id}/profile/confirm", json={"expected_revision": 0}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/v1/brands/{brand_id}/profile/confirm", json={"expected_revision": 1}
        ).status_code
        == 200
    )
    response = client.patch(
        f"/v1/brands/{brand_id}/profile",
        json={
            "expected_revision": 2,
            "changes": {
                "offerings": [
                    {"name": "Advisory", "description": "User corrected"},
                    {"name": "Flow"},
                ]
            },
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "DRAFT" and response.json()["confirmed_at"] is None
    after = client.get(f"/v1/brands/{brand_id}/offerings").json()["items"]
    assert after[0]["id"] == before[1]["id"] and after[1]["id"] == before[0]["id"]
    stale = client.patch(
        f"/v1/brands/{brand_id}/profile",
        json={
            "expected_revision": 2,
            "changes": {"brand_identity": {"name": "Lost update"}},
        },
    )
    assert stale.status_code == 409


async def test_no_company_name_can_still_be_reviewed_then_corrected(
    client, factory, settings, bundle, extraction_result
):
    extraction_result.profile.brand_identity.name = None
    extraction_result.evidence = [
        e for e in extraction_result.evidence if e.path != "/brand_identity/name"
    ]
    brand_id = register(client)
    await extract(client, make_worker(factory, settings, bundle, extraction_result), brand_id)
    response = client.post(f"/v1/brands/{brand_id}/profile/confirm", json={"expected_revision": 1})
    assert response.status_code == 422 and response.json()["error"]["code"] == "NAME_REQUIRED"
    assert (
        client.patch(
            f"/v1/brands/{brand_id}/profile",
            json={
                "expected_revision": 1,
                "changes": {"brand_identity": {"name": "User Company"}},
            },
        ).status_code
        == 200
    )
    assert (
        client.post(f"/v1/brands/{brand_id}/profile/confirm", json={"expected_revision": 2}).json()[
            "status"
        ]
        == "CONFIRMED"
    )


async def test_extraction_never_overwrites_an_intervening_user_edit(
    client, factory, settings, bundle, extraction_result
):
    worker = make_worker(factory, settings, bundle, extraction_result)
    brand_id = register(client)
    await extract(client, worker, brand_id)
    request = client.post(f"/v1/brands/{brand_id}/extractions", json={"replace_existing": True})
    assert request.status_code == 202
    claim = worker.claim()
    client.patch(
        f"/v1/brands/{brand_id}/profile",
        json={
            "expected_revision": 1,
            "changes": {"brand_identity": {"name": "Keep my correction"}},
        },
    )
    with pytest.raises(DomainError) as error:
        worker.complete(claim, extraction_result.profile, [], [])
    assert error.value.code == "PROFILE_CHANGED"
    worker.fail(claim, error.value)
    assert (
        client.get(f"/v1/brands/{brand_id}/profile").json()["profile"]["brand_identity"]["name"]
        == "Keep my correction"
    )
    assert client.get(request.json()["poll_url"]).json()["error"]["code"] == "PROFILE_CHANGED"


def test_trigger_is_immediate_durable_and_deduplicated(client, factory):
    brand_id = register(client)
    one = client.post(f"/v1/brands/{brand_id}/extractions")
    two = client.post(f"/v1/brands/{brand_id}/extractions")
    assert one.status_code == two.status_code == 202
    assert one.json()["id"] == two.json()["id"] and one.json()["status"] == "PENDING"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ExtractionJob)) == 1
        assert session.scalar(select(func.count()).select_from(SourceDocument)) == 0


async def test_crawl_failure_is_clear_and_persists_no_profile(
    client, factory, settings, bundle, extraction_result
):
    worker = make_worker(factory, settings, bundle, extraction_result)
    worker.source.collect = AsyncMock(
        side_effect=DomainError("ROBOTS_DENIED", "Crawling disallowed")
    )
    brand_id = register(client)
    job = client.post(f"/v1/brands/{brand_id}/extractions").json()
    await worker.process_once()
    result = client.get(job["poll_url"]).json()
    assert result["status"] == "FAILED" and result["error"]["code"] == "ROBOTS_DENIED"
    assert client.get(f"/v1/brands/{brand_id}/profile").status_code == 404


async def test_invalid_llm_facts_fail_but_leave_source_trace(
    client, factory, settings, bundle, extraction_result
):
    extraction_result.evidence = []
    brand_id = register(client)
    job = client.post(f"/v1/brands/{brand_id}/extractions").json()
    await make_worker(factory, settings, bundle, extraction_result).process_once()
    result = client.get(job["poll_url"]).json()
    assert result["status"] == "FAILED" and result["error"]["code"] == "LLM_EVIDENCE_INVALID"
    assert len(client.get(f"/v1/brands/{brand_id}/sources").json()["items"]) == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Profile)) == 0


async def test_transient_failure_retries_then_succeeds(
    client, factory, settings, bundle, extraction_result
):
    worker = make_worker(factory, settings, bundle, extraction_result)
    worker.provider.extract = AsyncMock(
        side_effect=[
            DomainError("LLM_UNAVAILABLE", "Rate limited", retryable=True),
            extraction_result,
        ]
    )
    brand_id = register(client)
    job = client.post(f"/v1/brands/{brand_id}/extractions").json()
    await worker.process_once()
    assert client.get(job["poll_url"]).json()["status"] == "PENDING"
    with factory.begin() as session:
        session.get(ExtractionJob, uuid.UUID(job["id"])).available_at = now() - timedelta(seconds=1)
    await worker.process_once()
    final = client.get(job["poll_url"]).json()
    assert final["status"] == "SUCCEEDED" and final["attempts"] == 2


def test_lease_recovery_fences_late_workers(client, factory, settings, bundle, extraction_result):
    worker = make_worker(factory, settings, bundle, extraction_result)
    brand_id = register(client)
    client.post(f"/v1/brands/{brand_id}/extractions")
    old = worker.claim()
    assert worker.claim() is None
    with factory.begin() as session:
        session.get(ExtractionJob, old.job_id).lease_until = now() - timedelta(seconds=1)
    new = worker.claim()
    assert new.job_id == old.job_id and new.token != old.token
    with pytest.raises(DomainError, match="lease"):
        worker.complete(old, extraction_result.profile, [], [])
    worker.complete(new, extraction_result.profile, [], [])
    assert client.get(f"/v1/brands/{brand_id}/profile").json()["status"] == "DRAFT"


def test_skip_locked_claims_another_job(factory, settings):
    with factory() as session:
        service = BrandService(session)
        one = service.create(CreateBrand(website_url="https://one.example/"))
        two = service.create(CreateBrand(website_url="https://two.example/"))
        first = service.enqueue(one.id, False)
        second = service.enqueue(two.id, False)
    with factory.begin() as locked:
        locked.scalar(select(ExtractionJob).where(ExtractionJob.id == first.id).with_for_update())
        with factory.begin() as concurrent:
            claimed = Repository(concurrent).claim_job(300, 3)
            assert claimed.id == second.id


def test_database_unique_active_job_constraint(factory):
    with factory() as session:
        service = BrandService(session)
        brand = service.create(CreateBrand(website_url="https://acme.example/"))
        service.enqueue(brand.id, False)
        session.add(ExtractionJob(brand_id=brand.id, base_revision=0))
        with pytest.raises(IntegrityError):
            session.commit()


def test_exhausted_crashed_job_becomes_terminal(
    client, factory, settings, bundle, extraction_result
):
    brand_id = register(client)
    job_id = uuid.UUID(client.post(f"/v1/brands/{brand_id}/extractions").json()["id"])
    with factory.begin() as session:
        job = session.get(ExtractionJob, job_id)
        job.status, job.attempts = "RUNNING", 3
        job.lease_until = now() - timedelta(seconds=1)
    assert make_worker(factory, settings, bundle, extraction_result).claim() is None
    result = client.get(f"/v1/extractions/{job_id}").json()
    assert result["status"] == "FAILED" and result["error"]["code"] == "WORKER_LOST"
    assert client.post(f"/v1/brands/{brand_id}/extractions").json()["id"] != str(job_id)


def test_validation_not_found_duplicate_and_access_token(client, monkeypatch):
    from app.config import get_settings

    assert client.post("/v1/brands", json={"website_url": "http://localhost"}).status_code == 422
    brand_id = register(client)
    assert (
        client.post("/v1/brands", json={"website_url": "https://acme.example"}).status_code == 409
    )
    assert client.get(f"/v1/brands/{uuid.uuid4()}").status_code == 404
    assert client.get("/v1/brands/not-a-uuid").status_code == 422
    assert client.get("/v1/brands?limit=101").status_code == 422
    assert client.get(f"/v1/brands/{brand_id}").status_code == 200
    monkeypatch.setenv("API_ACCESS_TOKEN", "test-token")
    get_settings.cache_clear()
    assert client.get("/v1/brands").status_code == 401
    assert (
        client.get("/v1/brands", headers={"Authorization": "Bearer test-token"}).status_code == 200
    )


async def test_invalid_edits_leave_confirmed_profile_intact(
    client, factory, settings, bundle, extraction_result
):
    brand_id = register(client)
    await extract(client, make_worker(factory, settings, bundle, extraction_result), brand_id)
    client.post(f"/v1/brands/{brand_id}/profile/confirm", json={"expected_revision": 1})
    for changes in [
        {"not_a_section": "wrong"},
        {"brand_voice": None},
        {"offerings": [{"description": "No offering name"}]},
        {"brand_identity": {"website_url": "https://other.example/"}},
    ]:
        response = client.patch(
            f"/v1/brands/{brand_id}/profile",
            json={
                "expected_revision": 2,
                "changes": changes,
            },
        )
        assert response.status_code == 422, response.text
    assert client.get(f"/v1/brands/{brand_id}/profile").json()["status"] == "CONFIRMED"
    assert client.post(f"/v1/brands/{brand_id}/extractions").status_code == 409
    assert client.get("/v1/profile-schema").json()["schema_version"] == 1


async def test_failed_reextraction_preserves_confirmed_profile(
    client, factory, settings, bundle, extraction_result
):
    worker = make_worker(factory, settings, bundle, extraction_result)
    brand_id = register(client)
    await extract(client, worker, brand_id)
    client.post(f"/v1/brands/{brand_id}/profile/confirm", json={"expected_revision": 1})
    worker.provider.extract = AsyncMock(side_effect=DomainError("LLM_REFUSED", "No profile"))
    job = client.post(f"/v1/brands/{brand_id}/extractions", json={"replace_existing": True}).json()
    await worker.process_once()
    assert client.get(job["poll_url"]).json()["status"] == "FAILED"
    assert client.get(f"/v1/brands/{brand_id}/profile").json()["status"] == "CONFIRMED"


async def test_hard_timeout_and_retry_exhaustion_are_persisted(
    client, factory, settings, bundle, extraction_result
):
    import asyncio

    settings.job_timeout_seconds = 0.01
    settings.job_max_attempts = 1
    worker = make_worker(factory, settings, bundle, extraction_result)

    async def hang(url):
        await asyncio.sleep(1)

    worker.source.collect = hang
    brand_id = register(client)
    job = client.post(f"/v1/brands/{brand_id}/extractions").json()
    await worker.process_once()
    result = client.get(job["poll_url"]).json()
    assert result["status"] == "FAILED" and result["error"]["code"] == "EXTRACTION_TIMEOUT"
