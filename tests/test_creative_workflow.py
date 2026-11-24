"""PostgreSQL/API/worker integration; all creative inference is explicitly mocked."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from app.creative.models import CreativeJob
from app.creative.worker import CreativeWorker
from app.errors import DomainError
from app.models import Brand, ExtractionJob, Profile, SourceDocument, now
from app.repository import Repository
from app.schemas import BIP
from tests.creative_helpers import MockCreativeTransport, fixture

pytestmark = pytest.mark.integration


def seed(factory, name="opal"):
    data = fixture(name)
    stamp = now()
    bip = BIP.model_validate(data["profile"])
    with factory.begin() as s:
        brand = Brand(
            website_url=bip.brand_identity.website_url, name=bip.brand_identity.name, revision=5
        )
        s.add(brand)
        s.flush()
        source = ExtractionJob(
            brand_id=brand.id, status="SUCCEEDED", base_revision=4, finished_at=stamp, attempts=1
        )
        s.add(source)
        s.flush()
        provenance = [
            {**p, "recorded_at": stamp.isoformat(), "reasoning": None, "confirmed_by_user": False}
            for p in data["provenance"]
        ]
        p = Profile(
            brand_id=brand.id,
            status="DRAFT",
            schema_version=2,
            sections={},
            provenance=provenance,
            warnings=[],
            pipeline_metadata={},
            source_job_id=source.id,
            extracted_at=stamp,
        )
        s.add(p)
        Repository(s).write_bip(brand.id, p, bip)
        for d in data["documents"]:
            s.add(
                SourceDocument(
                    brand_id=brand.id,
                    job_id=source.id,
                    url=d["url"],
                    title=d["title"],
                    content=d["text"],
                    source_type=d["source_type"],
                    source_metadata=d.get("metadata", {}),
                    content_hash="offline-snapshot-hash",
                    fetched_at=stamp,
                )
            )
        return brand.id, source.id


def checksum(factory, brand_id):
    with factory() as s:
        return s.execute(
            text(
                "SELECT md5(row_to_json(p)::text),b.revision FROM profiles p "
                "JOIN brands b ON b.id=p.brand_id WHERE b.id=:id"
            ),
            {"id": brand_id},
        ).one()


def campaign(client, brand_id, name="opal"):
    r = client.post(f"/v1/brands/{brand_id}/campaigns", json=fixture(name)["brief"])
    assert r.status_code == 201, r.text
    return r.json()


def enqueue(client, campaign_id, expected=1):
    r = client.post(
        f"/v1/campaigns/{campaign_id}/generations", json={"expected_revision": expected}
    )
    assert r.status_code == 202, r.text
    return r.json()


def job(client, job_id):
    r = client.get(f"/v1/creative-generations/{job_id}")
    assert r.status_code == 200
    return r.json()


def concepts(client, campaign_id, job_id=None):
    return client.get(
        f"/v1/campaigns/{campaign_id}/concepts", params={"job_id": job_id} if job_id else {}
    ).json()["items"]


@pytest.mark.parametrize("name", ["opal", "minimalist"])
async def test_full_flow_preserves_bip_and_supports_edit_select_approve_handoff(
    client, factory, settings, name
):
    brand, source = seed(factory, name)
    before = checksum(factory, brand)
    transport = MockCreativeTransport(settings, name)
    c = campaign(client, brand, name)
    assert c["brief"]["region"] is None and c["revision"] == 1
    queued = enqueue(client, c["id"])
    assert not transport.calls
    assert enqueue(client, c["id"])["id"] == queued["id"]
    worker = CreativeWorker(factory, settings, transport)
    assert await worker.process_once()
    done = job(client, queued["id"])
    assert done["status"] == "SUCCEEDED" and done["attempts"] == 1
    assert done["metrics"]["input_tokens"] == 200 and len(done["metrics"]["calls"]) == 2
    shortlist = client.get(f"/v1/campaigns/{c['id']}/shortlist").json()
    assert len(shortlist["items"]) == 5 and not shortlist["stale"]
    assert len(concepts(client, c["id"])) == 6
    research = client.get(f"/v1/campaigns/{c['id']}/research/{queued['research_id']}").json()
    assert research["brand_revision"] == 5 and research["source_job_id"] == str(source)
    concept = shortlist["items"][0]
    edit = client.patch(
        f"/v1/concepts/{concept['id']}",
        json={
            "expected_revision": 1,
            "changes": {
                "primary_hook": "A human-written hook for this audience",
                "core_insight": "Human-edited insight wording for this audience.",
                "story": {"opening": "An edited everyday situation"},
            },
        },
    )
    assert edit.status_code == 200, edit.text
    edited = edit.json()
    assert edited["revision"] == 2 and edited["edited_by_user"]
    assert edited["original_content"] == concept["content"]
    assert edited["evaluation"] is None and not edited["shortlisted"]
    assert (
        client.patch(
            f"/v1/concepts/{concept['id']}",
            json={"expected_revision": 1, "changes": {"cta": "Later"}},
        ).status_code
        == 409
    )
    selected = client.post(f"/v1/concepts/{concept['id']}/select", json={"expected_revision": 2})
    assert selected.status_code == 200
    assert (
        client.post(
            f"/v1/concepts/{concept['id']}/approve", json={"expected_revision": 3}
        ).status_code
        == 422
    )
    approved = client.post(
        f"/v1/concepts/{concept['id']}/approve",
        json={
            "expected_revision": 3,
            "human_review_completed": True,
            "acknowledge_claim_risks": True,
            "acknowledge_asset_requirements": True,
        },
    )
    assert approved.status_code == 200, approved.text
    handoff = client.get(f"/v1/concepts/{concept['id']}/handoff").json()
    assert handoff["state"] == "APPROVED" and not handoff["advertising_claims_approved"]
    assert not handoff["video_rendered"] and handoff["bip_revision"] == 5
    assert handoff["concept"]["primary_hook"] == "A human-written hook for this audience"
    assert checksum(factory, brand) == before
    assert not await worker.process_once()
    assert len(transport.calls) == 2


async def test_evaluator_failure_preserves_six_candidates_and_no_paid_retry(
    client, factory, settings
):
    brand, _ = seed(factory)
    c = campaign(client, brand)
    q = enqueue(client, c["id"])
    transport = MockCreativeTransport(
        settings, evaluator_error=DomainError("CHATGPT_TIMEOUT", "Safe mock timeout")
    )
    worker = CreativeWorker(factory, settings, transport)
    await worker.process_once()
    d = job(client, q["id"])
    assert d["status"] == "PARTIAL" and d["error"]["code"] == "CHATGPT_TIMEOUT"
    assert d["diagnostics"]["failure_stage"] == "evaluator"
    assert len(concepts(client, c["id"])) == 6
    assert not client.get(f"/v1/campaigns/{c['id']}/shortlist").json()["items"]
    assert len(d["metrics"]["calls"]) == 2 and d["metrics"]["calls"][1]["status"] == "failed"
    assert not await worker.process_once() and len(transport.calls) == 2


async def test_generator_failure_does_not_destroy_earlier_work(client, factory, settings):
    brand, _ = seed(factory)
    c = campaign(client, brand)
    first = enqueue(client, c["id"])
    worker = CreativeWorker(factory, settings, MockCreativeTransport(settings))
    await worker.process_once()
    saved = concepts(client, c["id"])
    second = enqueue(client, c["id"])
    transport = MockCreativeTransport(settings)

    async def fail(*args, **kwargs):
        raise DomainError("LLM_INVALID_OUTPUT", "Invalid mock JSON")

    transport.request_structured = fail
    worker.provider = transport
    await worker.process_once()
    assert job(client, first["id"])["status"] == "SUCCEEDED"
    assert job(client, second["id"])["status"] == "FAILED"
    assert concepts(client, c["id"]) == saved


async def test_all_rejected_candidates_skip_evaluation(client, factory, settings):
    brand, _ = seed(factory)
    c = campaign(client, brand)
    q = enqueue(client, c["id"])

    def reject(result):
        for row in result.candidates:
            row.product_capability = "Unsupported invented capability"

    transport = MockCreativeTransport(settings, on_generate=reject)
    await CreativeWorker(factory, settings, transport).process_once()
    d = job(client, q["id"])
    assert d["status"] == "INSUFFICIENT_DATA" and d["diagnostics"]["accepted"] == 0
    assert d["diagnostics"]["evaluator_status"] == "SKIPPED" and len(transport.calls) == 1
    assert not concepts(client, c["id"])


async def test_human_edit_during_evaluation_is_not_overwritten(client, factory, settings):
    brand, _ = seed(factory)
    c = campaign(client, brand)
    q = enqueue(client, c["id"])
    edited_id = []

    def edit_while_evaluating(payload):
        row = next(r for r in concepts(client, c["id"]) if r["candidate_key"] == "idea-0")
        edited_id.append(row["id"])
        result = client.patch(
            f"/v1/concepts/{row['id']}",
            json={"expected_revision": 1, "changes": {"title": "Human review wins"}},
        )
        assert result.status_code == 200

    transport = MockCreativeTransport(settings, on_evaluate=edit_while_evaluating)
    await CreativeWorker(factory, settings, transport).process_once()
    row = client.get(f"/v1/concepts/{edited_id[0]}").json()
    assert row["content"]["title"] == "Human review wins" and row["evaluation"] is None
    assert not row["shortlisted"]
    assert job(client, q["id"])["diagnostics"]["shortlisted"] == 4


async def test_regeneration_appends_without_touching_human_edits_or_selection(
    client, factory, settings
):
    brand, _ = seed(factory)
    c = campaign(client, brand)
    enqueue(client, c["id"])
    worker = CreativeWorker(factory, settings, MockCreativeTransport(settings))
    await worker.process_once()
    row = concepts(client, c["id"])[0]
    client.patch(
        f"/v1/concepts/{row['id']}",
        json={"expected_revision": 1, "changes": {"title": "Keep this human idea"}},
    )
    client.post(f"/v1/concepts/{row['id']}/select", json={"expected_revision": 2})
    saved = client.get(f"/v1/concepts/{row['id']}").json()
    next_job = enqueue(client, c["id"])
    await worker.process_once()
    assert len(concepts(client, c["id"])) == 12
    assert client.get(f"/v1/concepts/{row['id']}").json() == saved
    assert len(concepts(client, c["id"], next_job["id"])) == 6


@pytest.mark.parametrize("change", ["brief", "brand"])
async def test_stale_research_before_inference_makes_zero_calls(client, factory, settings, change):
    brand, _ = seed(factory)
    c = campaign(client, brand)
    q = enqueue(client, c["id"])
    if change == "brief":
        assert (
            client.patch(
                f"/v1/campaigns/{c['id']}/brief",
                json={"expected_revision": 1, "changes": {"objective": "Consideration"}},
            ).status_code
            == 200
        )
    else:
        with factory.begin() as s:
            s.get(Brand, brand).revision += 1
    transport = MockCreativeTransport(settings)
    await CreativeWorker(factory, settings, transport).process_once()
    d = job(client, q["id"])
    assert d["status"] == "FAILED" and d["error"]["code"] == "RESEARCH_STALE"
    assert not transport.calls


async def test_bip_edit_during_evaluator_yields_partial_and_cannot_select(
    client, factory, settings
):
    brand, _ = seed(factory)
    c = campaign(client, brand)
    q = enqueue(client, c["id"])

    def change(payload):
        with factory.begin() as s:
            s.get(Brand, brand).revision += 1

    await CreativeWorker(
        factory, settings, MockCreativeTransport(settings, on_evaluate=change)
    ).process_once()
    d = job(client, q["id"])
    assert d["status"] == "PARTIAL" and d["error"]["code"] == "RESEARCH_STALE"
    rows = concepts(client, c["id"])
    assert len(rows) == 6
    assert (
        client.post(
            f"/v1/concepts/{rows[0]['id']}/select", json={"expected_revision": 1}
        ).status_code
        == 409
    )


def test_concurrent_claims_only_acquire_a_single_lease(client, factory, settings):
    brand, _ = seed(factory)
    c = campaign(client, brand)
    enqueue(client, c["id"])

    def claim(_):
        return CreativeWorker(factory, settings, MockCreativeTransport(settings)).claim()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, range(2)))
    assert sum(x is not None for x in claims) == 1
    with factory() as s:
        assert s.scalar(select(CreativeJob)).attempts == 1


async def test_expired_lease_never_retries_inference(client, factory, settings):
    brand, _ = seed(factory)
    c = campaign(client, brand)
    q = enqueue(client, c["id"])
    transport = MockCreativeTransport(settings)
    worker = CreativeWorker(factory, settings, transport)
    claim = worker.claim()
    assert claim
    with factory.begin() as s:
        s.get(CreativeJob, claim.job_id).lease_until = now() - timedelta(seconds=1)
    assert worker.claim() is None
    assert job(client, q["id"])["status"] == "FAILED"
    assert not await worker.process_once() and not transport.calls
    worker.finish_error(claim, DomainError("LATE", "Late completion"), "generator", None)
    assert job(client, q["id"])["error"]["code"] == "WORKER_LOST"


def test_brief_validation_revision_auth_and_scope(client, factory, monkeypatch):
    brand, _ = seed(factory)
    assert (
        client.post(f"/v1/brands/{brand}/campaigns", json={"objective": "Awareness"}).status_code
        == 422
    )
    assert (
        client.post(
            f"/v1/brands/{brand}/campaigns",
            json={
                "objective": "Awareness",
                "audience": "Students",
                "promotion_scope": "FAMILY",
                "offering_key": "opal",
            },
        ).status_code
        == 422
    )
    c = campaign(client, brand)
    assert (
        client.patch(
            f"/v1/campaigns/{c['id']}/brief",
            json={"expected_revision": 1, "changes": {"duration_seconds": 1000}},
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/v1/campaigns/{c['id']}/brief",
            json={"expected_revision": 1, "changes": {"objective": "Consideration"}},
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/v1/campaigns/{c['id']}/brief",
            json={"expected_revision": 1, "changes": {"objective": "Sale"}},
        ).status_code
        == 409
    )
    monkeypatch.setenv("API_ACCESS_TOKEN", "private-test-token")
    from app.config import get_settings

    get_settings.cache_clear()
    assert client.get(f"/v1/campaigns/{c['id']}").status_code == 401
    assert (
        client.get(
            f"/v1/campaigns/{c['id']}", headers={"Authorization": "Bearer private-test-token"}
        ).status_code
        == 200
    )
    get_settings.cache_clear()


def test_source_binding_never_uses_another_brands_documents(client, factory):
    brand, source = seed(factory)
    with factory.begin() as s:
        for row in s.scalars(select(SourceDocument).where(SourceDocument.job_id == source)):
            s.delete(row)
    c = campaign(client, brand)
    r = client.post(f"/v1/campaigns/{c['id']}/generations", json={"expected_revision": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "SOURCE_SNAPSHOT_MISSING"
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(CreativeJob)) == 0


async def test_evaluator_bad_candidate_keys_preserves_partial_candidates(client, factory, settings):

    brand, _ = seed(factory)
    c = campaign(client, brand)
    q = enqueue(client, c["id"])
    transport = MockCreativeTransport(settings)
    original = transport.request_structured

    async def invalid(*args, **kwargs):
        result = await original(*args, **kwargs)
        if hasattr(result, "evaluations"):
            result.evaluations[0].candidate_key = "unknown"
        return result

    transport.request_structured = invalid
    await CreativeWorker(factory, settings, transport).process_once()
    d = job(client, q["id"])
    assert d["status"] == "PARTIAL" and d["diagnostics"]["evaluator_status"] == "FAILED"
    assert (
        d["error"]["code"] == "EVALUATOR_REFERENCE_INVALID" and len(concepts(client, c["id"])) == 6
    )


async def test_evaluator_selecting_nothing_reports_insufficiency(client, factory, settings):
    brand, _ = seed(factory)
    c = campaign(client, brand)
    q = enqueue(client, c["id"])
    transport = MockCreativeTransport(settings)
    original = transport.request_structured

    async def none(*args, **kwargs):
        result = await original(*args, **kwargs)
        if hasattr(result, "evaluations"):
            for e in result.evaluations:
                e.selected = False
        return result

    transport.request_structured = none
    await CreativeWorker(factory, settings, transport).process_once()
    d = job(client, q["id"])
    assert d["status"] == "INSUFFICIENT_DATA" and d["diagnostics"]["insufficient_shortlist"]
    assert len(concepts(client, c["id"])) == 6


async def test_selected_handoff_is_fenced_after_brief_change(client, factory, settings):
    brand, _ = seed(factory)
    c = campaign(client, brand)
    enqueue(client, c["id"])
    await CreativeWorker(factory, settings, MockCreativeTransport(settings)).process_once()
    row = concepts(client, c["id"])[0]
    assert (
        client.post(f"/v1/concepts/{row['id']}/select", json={"expected_revision": 1}).status_code
        == 200
    )
    assert client.get(f"/v1/concepts/{row['id']}/handoff").status_code == 200
    client.patch(
        f"/v1/campaigns/{c['id']}/brief",
        json={"expected_revision": 1, "changes": {"audience": "Parents"}},
    )
    assert client.get(f"/v1/concepts/{row['id']}/handoff").status_code == 409
    assert client.get(f"/v1/campaigns/{c['id']}/shortlist").json()["stale"]


def test_active_stale_generation_is_not_returned_as_new_brief_work(client, factory):
    brand, _ = seed(factory)
    c = campaign(client, brand)
    enqueue(client, c["id"])
    client.patch(
        f"/v1/campaigns/{c['id']}/brief",
        json={"expected_revision": 1, "changes": {"audience": "Parents"}},
    )
    r = client.post(f"/v1/campaigns/{c['id']}/generations", json={"expected_revision": 2})
    assert r.status_code == 409 and r.json()["error"]["code"] == "GENERATION_ACTIVE_STALE"


async def test_expired_evaluator_lease_keeps_checkpoint_but_never_publishes(
    client, factory, settings
):
    brand, _ = seed(factory)
    c = campaign(client, brand)
    q = enqueue(client, c["id"])
    transport = MockCreativeTransport(settings)
    worker = CreativeWorker(factory, settings, transport)

    def lose_lease(payload):
        with factory.begin() as s:
            s.get(CreativeJob, __import__("uuid").UUID(q["id"])).lease_until = now() - timedelta(
                seconds=1
            )
        assert worker.claim() is None

    transport.on_evaluate = lose_lease
    await worker.process_once()
    d = job(client, q["id"])
    assert d["status"] == "PARTIAL" and d["error"]["code"] == "WORKER_LOST"
    assert all(r["evaluation"] is None for r in concepts(client, c["id"]))
    assert not await worker.process_once() and len(transport.calls) == 2


async def test_account_binding_error_is_preflight_failure_and_not_running_generator(
    client, factory, settings
):
    brand, _ = seed(factory)
    c = campaign(client, brand)
    q = enqueue(client, c["id"])
    transport = MockCreativeTransport(settings)

    def revoked(*args):
        raise DomainError("CHATGPT_SIGN_IN_REQUIRED", "Reconnect the account")

    transport.for_job = revoked
    await CreativeWorker(factory, settings, transport).process_once()
    d = job(client, q["id"])
    assert d["status"] == "FAILED" and d["diagnostics"]["failure_stage"] == "preflight"
    assert d["diagnostics"]["generator_status"] == "SKIPPED" and not transport.calls


@pytest.fixture(autouse=True)
def forbid_recrawl_and_brand_inference(monkeypatch):
    from unittest.mock import AsyncMock

    fail = AsyncMock(
        side_effect=AssertionError("A creative campaign must not recrawl or extract the brand")
    )
    monkeypatch.setattr("app.crawling.website.WebsiteSource.collect", fail)
    monkeypatch.setattr("app.llm.responses.StructuredResponsesProvider.extract_facts", fail)
    monkeypatch.setattr("app.llm.responses.StructuredResponsesProvider.infer_marketing", fail)


async def test_generation_history_keeps_failed_jobs_visible(client, factory, settings):
    brand, _ = seed(factory)
    c = campaign(client, brand)
    q = enqueue(client, c["id"])
    transport = MockCreativeTransport(
        settings, evaluator_error=DomainError("EVALUATOR_FAILED", "Mock failure")
    )
    await CreativeWorker(factory, settings, transport).process_once()
    history = client.get(f"/v1/campaigns/{c['id']}/generations").json()
    assert len(history["items"]) == 1 and history["items"][0]["id"] == q["id"]
    assert history["items"][0]["status"] == "PARTIAL"


async def test_quality_review_persists_payoff_flags_overlap_and_benchmark_export(
    client,
    factory,
    settings,
):
    from uuid import UUID

    from evaluations.phase2a import export

    brand, _ = seed(factory)
    before = checksum(factory, brand)
    c = campaign(client, brand)
    queued = enqueue(client, c["id"])

    def revise_candidates(result):
        result.candidates[0].story.payoff = "Your grades will rise after your first session."
        result.candidates[5].primary_hook = result.candidates[4].primary_hook

    class MockOverlapTransport(MockCreativeTransport):
        async def request_structured(self, *args, **kwargs):
            result = await super().request_structured(*args, **kwargs)
            if not hasattr(result, "evaluations"):
                return result
            data = result.model_dump()
            data["evaluations"][0]["grounding"].update(
                payoff="UNSUPPORTED_PROMISE",
                explanation="App blocking does not support a grade gain.",
            )
            data["evaluations"][2]["grounding"].update(
                closing_line="PRODUCT_SCOPE_MISMATCH",
                explanation="The closing names another product.",
            )
            data["overlap_groups"] = [
                {
                    "candidate_keys": ["idea-0", "idea-1"],
                    "representative_key": "idea-0",
                    "rationale": "The two angles promise the same uninterrupted study time.",
                }
            ]
            return type(result).model_validate(data)

    transport = MockOverlapTransport(settings, on_generate=revise_candidates)
    await CreativeWorker(factory, settings, transport).process_once()
    report = export(client, UUID(c["id"]), UUID(queued["id"]))
    assert len(transport.calls) == 2
    assert report["generator_research"] == transport.calls[0]["payload"]["research"]
    assert report["shortlist_insufficient"] and len(report["shortlist"]) == 3
    assert all(item["evaluation"]["distinction"] for item in report["shortlist"])
    assert len(report["generated_candidates"]) == 6
    assert len(report["stage_usage"]) == 2
    records = {row["candidate"]["candidate_key"]: row for row in report["generated_candidates"]}
    payoff_flags = records["idea-0"]["validation"]["flags"]
    assert any(f["code"] == "UNSUPPORTED_PROMISE" and f["field"] == "payoff" for f in payoff_flags)
    assert records["idea-1"]["evaluation"]["overlap_representative_key"] == "idea-0"
    assert not records["idea-2"]["validation"]["eligible"]
    assert records["idea-5"]["rejected"]
    assert records["idea-0"]["viewer_copy"]["on_screen_copy"]["treatment"] == "SINGLE_CAPTION"
    blocked = next(row for row in concepts(client, c["id"]) if row["candidate_key"] == "idea-2")
    edited = client.patch(
        f"/v1/concepts/{blocked['id']}",
        json={
            "expected_revision": blocked["revision"],
            "changes": {"title": "New title"},
        },
    ).json()
    assert not edited["validation"]["eligible"]
    assert edited["original_content"] == blocked["original_content"]
    assert checksum(factory, brand) == before
