"""Independent marketing uses saved data and exactly one mocked inference request."""

import json
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.errors import DomainError
from app.marketing import merge_marketing
from app.models import CatalogProduct, ExtractionJob, Profile, SourceDocument, now
from app.schemas import BIP, DraftMarketingResult
from app.worker import ExtractionWorker
from tests.conftest import FakeProvider, FakeSource
from tests.test_chatgpt import chatgpt as chatgpt_fixture
from tests.test_chatgpt import configure_api
from tests.test_chatgpt import signing_key as signing_key_fixture
from tests.test_draft_first import draft_facts, hypotheses

chatgpt = chatgpt_fixture
signing_key = signing_key_fixture


async def seed(client, factory, settings, bundle=None, facts=None):
    settings.extraction_mode = "draft_first"
    if bundle is None:
        bundle, facts = draft_facts("minimalist")
    provider = FakeProvider(facts)
    provider.infer_marketing = AsyncMock(return_value=DraftMarketingResult())
    brand = client.post(
        "/v1/brands", json={"website_url": facts.profile.brand_identity.website_url}
    ).json()["id"]
    job = client.post(f"/v1/brands/{brand}/extractions").json()
    assert await ExtractionWorker(factory, settings, FakeSource(bundle), provider).process_once()
    final = client.get(job["poll_url"]).json()
    assert final["status"] == "SUCCEEDED", final
    p = client.get(f"/v1/brands/{brand}/profile").json()
    return brand, p, bundle, facts, job


def provider_for(facts, result):
    provider = FakeProvider(facts)
    provider.extract_facts = AsyncMock(side_effect=AssertionError("Factual model must not run"))
    provider.infer_marketing = AsyncMock(
        side_effect=result if callable(result) else None,
        return_value=None if callable(result) else result,
    )
    return provider


@pytest.mark.integration
async def test_independent_enrichment_no_crawl_or_factual_call_and_links(client, factory, settings):
    brand, old, bundle, facts, extraction = await seed(client, factory, settings)
    key = old["profile"]["offerings"][0]["key"]
    source = FakeSource(bundle)
    source.collect = AsyncMock(side_effect=AssertionError("Must not crawl"))
    provider = provider_for(facts, hypotheses)
    queued = client.post(
        f"/v1/brands/{brand}/marketing-enrichments",
        json={"expected_revision": old["revision"], "offering_key": key},
    ).json()
    assert queued["source_job_id"] == extraction["id"] and queued["kind"] == "MARKETING"
    assert queued["status"] == "PENDING"
    assert (
        client.post(f"/v1/brands/{brand}/extractions", json={"replace_existing": True}).status_code
        == 409
    )
    assert await ExtractionWorker(factory, settings, source, provider).process_once()
    final = client.get(queued["poll_url"]).json()
    assert final["status"] == "SUCCEEDED" and final["attempts"] == 1
    source.collect.assert_not_awaited()
    provider.extract_facts.assert_not_awaited()
    provider.infer_marketing.assert_awaited_once()
    new = client.get(f"/v1/brands/{brand}/profile").json()
    assert (
        new["source_job_id"] == old["source_job_id"] and new["extracted_at"] == old["extracted_at"]
    )
    assert new["revision"] == old["revision"] + 1
    before_proof = {e["path"]: e for e in old["provenance"]}
    for e in new["provenance"]:
        if e["path"] in before_proof:
            assert e == before_proof[e["path"]]
    assert any(e["origin"] == "INFERRED" for e in new["provenance"])
    for old_o, new_o in zip(old["profile"]["offerings"], new["profile"]["offerings"], strict=True):
        assert {
            k: v for k, v in old_o.items() if k not in ("audiences", "customer_psychology")
        } == {k: v for k, v in new_o.items() if k not in ("audiences", "customer_psychology")}
    assert new["customer_concerns"] and new["catalog"]["available"]
    response = client.get(f"/v1/brands/{brand}/catalog?limit=2").json()
    assert len(response["items"]) == 2 and response["total"] >= 3
    assert response["linked_to_offerings"] >= 1
    selected = client.get(f"/v1/brands/{brand}/catalog?linked_only=true").json()
    assert all(p["linked_offering_id"] for p in selected["items"])
    assert client.get(f"/v1/brands/{brand}/catalog?limit=0").status_code == 422
    assert client.get(f"/v1/brands/{brand}/customer-concerns").json()["items"]
    assert client.get(f"/v1/brands/{brand}").json()["customer_concerns"]


@pytest.mark.integration
@pytest.mark.parametrize("outcome", ["empty", "timeout", "malformed"])
async def test_unsuccessful_marketing_does_not_change_profile(client, factory, settings, outcome):
    brand, old, bundle, facts, _ = await seed(client, factory, settings)
    if outcome == "empty":
        provider = provider_for(facts, DraftMarketingResult())
    elif outcome == "timeout":

        async def fail(*args):
            raise DomainError("CHATGPT_TIMEOUT", "Safe timeout", retryable=True)

        provider = provider_for(facts, fail)
    else:

        async def invalid(*args):
            raise DomainError("LLM_INVALID_OUTPUT", "Malformed structured output")

        provider = provider_for(facts, invalid)
    settings.job_max_attempts = 3
    q = client.post(f"/v1/brands/{brand}/marketing-enrichments").json()
    assert await ExtractionWorker(factory, settings, FakeSource(bundle), provider).process_once()
    final = client.get(q["poll_url"]).json()
    assert final["status"] == ("INSUFFICIENT_DATA" if outcome == "empty" else "FAILED")
    new = client.get(f"/v1/brands/{brand}/profile").json()
    # Latest-job/discovery read views may evolve; persisted profile fields never change.
    for k in (
        "profile",
        "provenance",
        "warnings",
        "revision",
        "status",
        "updated_at",
        "confirmed_at",
        "stages",
    ):
        assert new[k] == old[k]
    provider.infer_marketing.assert_awaited_once()
    assert (
        await ExtractionWorker(factory, settings, FakeSource(bundle), provider).process_once()
        is False
    )


@pytest.mark.integration
@pytest.mark.parametrize("when", ["before", "during"])
async def test_revision_fence_preserves_customer_changes(client, factory, settings, when):
    brand, old, bundle, facts, _ = await seed(client, factory, settings)
    q = client.post(
        f"/v1/brands/{brand}/marketing-enrichments", json={"expected_revision": old["revision"]}
    ).json()

    def edit():
        return client.patch(
            f"/v1/brands/{brand}/profile",
            json={
                "expected_revision": old["revision"],
                "changes": {"brand_identity": {"tagline": "Customer edit"}},
            },
        )

    if when == "before":
        assert edit().status_code == 200
        provider = provider_for(facts, hypotheses)
    else:

        async def answer(p, s, key):
            assert edit().status_code == 200
            return hypotheses(p, s, key)

        provider = provider_for(facts, answer)
    await ExtractionWorker(factory, settings, FakeSource(bundle), provider).process_once()
    final = client.get(q["poll_url"]).json()
    assert final["status"] == "FAILED" and final["error"]["code"] == "PROFILE_CHANGED"
    assert (
        client.get(f"/v1/brands/{brand}/profile").json()["profile"]["brand_identity"]["tagline"]
        == "Customer edit"
    )
    assert provider.infer_marketing.await_count == (0 if when == "before" else 1)
    assert (
        client.post(
            f"/v1/brands/{brand}/marketing-enrichments", json={"expected_revision": old["revision"]}
        ).status_code
        == 409
    )


@pytest.mark.integration
async def test_user_edits_confirmed_values_and_explicit_replacement(client, factory, settings):
    brand, old, bundle, facts, _ = await seed(client, factory, settings)
    edit = client.patch(
        f"/v1/brands/{brand}/profile",
        json={
            "expected_revision": old["revision"],
            "changes": {
                "creative_context": {"creative_angles": ["User angle"]},
                "customer_psychology": {"motivations": []},
            },
        },
    ).json()
    confirmed = client.post(
        f"/v1/brands/{brand}/profile/confirm", json={"expected_revision": edit["revision"]}
    ).json()
    provider = provider_for(facts, hypotheses)
    q = client.post(f"/v1/brands/{brand}/marketing-enrichments").json()
    await ExtractionWorker(factory, settings, FakeSource(bundle), provider).process_once()
    p = client.get(f"/v1/brands/{brand}/profile").json()
    assert p["profile"]["creative_context"]["creative_angles"] == ["User angle"]
    assert p["profile"]["customer_psychology"]["motivations"] == []
    assert (
        p["profile"]["offerings"][0]["ingredients"]
        == confirmed["profile"]["offerings"][0]["ingredients"]
    )
    q = client.post(
        f"/v1/brands/{brand}/marketing-enrichments", json={"replace_reviewed": True}
    ).json()
    await ExtractionWorker(factory, settings, FakeSource(bundle), provider).process_once()
    assert client.get(q["poll_url"]).json()["status"] == "SUCCEEDED"
    p = client.get(f"/v1/brands/{brand}/profile").json()
    assert p["profile"]["creative_context"]["creative_angles"] != ["User angle"]


@pytest.mark.integration
async def test_legacy_source_resolution_does_not_pick_failed_or_newer_snapshot(
    client, factory, settings
):
    brand, old, bundle, facts, extract = await seed(client, factory, settings)
    with factory.begin() as session:
        profile = session.get(Profile, uuid.UUID(brand))
        profile.source_job_id = None
        bad = ExtractionJob(
            brand_id=uuid.UUID(brand),
            kind="EXTRACTION",
            base_revision=old["revision"],
            status="FAILED",
            started_at=now(),
            finished_at=now(),
        )
        session.add(bad)
        session.flush()
        session.add(
            SourceDocument(
                brand_id=uuid.UUID(brand),
                job_id=bad.id,
                url="https://wrong.example/",
                content="Wrong snapshot",
                content_hash="x",
                fetched_at=now(),
            )
        )
    q = client.post(f"/v1/brands/{brand}/marketing-enrichments").json()
    assert q["source_job_id"] == extract["id"]
    provider = provider_for(facts, hypotheses)
    await ExtractionWorker(factory, settings, FakeSource(bundle), provider).process_once()
    context = provider.infer_marketing.call_args.args[1]
    assert all(d.url != "https://wrong.example/" for d in context.documents)


@pytest.mark.integration
async def test_expired_paid_marketing_lease_is_not_retried(client, factory, settings):
    brand, old, bundle, facts, _ = await seed(client, factory, settings)
    q = client.post(f"/v1/brands/{brand}/marketing-enrichments").json()
    settings.job_max_attempts = 5
    worker = ExtractionWorker(
        factory, settings, FakeSource(bundle), provider_for(facts, hypotheses)
    )
    assert worker.claim().job_id == uuid.UUID(q["id"])
    with factory.begin() as session:
        session.get(ExtractionJob, uuid.UUID(q["id"])).lease_until = now() - timedelta(seconds=1)
    assert worker.claim() is None
    final = client.get(q["poll_url"]).json()
    assert final["status"] == "FAILED" and final["error"]["code"] == "WORKER_LOST"
    assert final["attempts"] == 1


@pytest.mark.integration
async def test_facts_and_concerns_are_available_while_initial_marketing_runs(
    client, factory, settings
):
    settings.extraction_mode = "draft_first"
    bundle, facts = draft_facts("minimalist")
    brand = client.post(
        "/v1/brands", json={"website_url": facts.profile.brand_identity.website_url}
    ).json()["id"]
    client.post(f"/v1/brands/{brand}/extractions")

    async def check(p, s, key):
        partial = client.get(f"/v1/brands/{brand}/profile").json()
        assert partial["stages"] == {"facts": "SUCCEEDED", "marketing": "RUNNING"}
        assert partial["profile"]["offerings"] and partial["customer_concerns"]
        raise DomainError("CHATGPT_TIMEOUT", "Timeout")

    provider = FakeProvider(facts)
    provider.infer_marketing = AsyncMock(side_effect=check)
    await ExtractionWorker(factory, settings, FakeSource(bundle), provider).process_once()
    p = client.get(f"/v1/brands/{brand}/profile").json()
    assert p["stages"]["marketing"] == "FAILED" and p["profile"]["offerings"]


@pytest.mark.integration
async def test_mock_chatgpt_marketing_request_binds_account_and_stream_diagnostics(
    client, factory, settings, chatgpt, monkeypatch
):
    brand, old, bundle, facts, _ = await seed(client, factory, settings)
    configure_api(client, monkeypatch, chatgpt)
    account, _ = await chatgpt.connect()
    chatgpt.marketing = DraftMarketingResult(
        creative_context={"creative_angles": ["A new product routine"]}
    )
    q = client.post(
        f"/v1/brands/{brand}/marketing-enrichments", json={"marketing_model": "account-model"}
    ).json()
    assert q["chatgpt_account_id"] == account["id"] and q["marketing_model"] == "account-model"
    assert not any(r.url.path == "/v1/responses" for r in chatgpt.calls)
    # Changing the selected account must not silently change this queued job's owner.
    chatgpt.issued = "oaiapp_second"
    chatgpt.token_value = "test-access-b"
    await chatgpt.connect()
    from app.llm.chatgpt_provider import ChatGPTProvider

    source = FakeSource(bundle)
    source.collect = AsyncMock(side_effect=AssertionError("No crawl"))
    await ExtractionWorker(
        factory, settings, source, ChatGPTProvider(chatgpt.settings, chatgpt.service)
    ).process_once()
    final = client.get(q["poll_url"]).json()
    assert final["status"] == "SUCCEEDED", final
    requests = [r for r in chatgpt.calls if r.url.path == "/v1/responses"]
    assert len(requests) == 1 and requests[0].headers["authorization"] == "Bearer test-access-a"
    body = json.loads(requests[0].content)
    assert body["text"]["format"]["name"] == "DraftMarketingResult"
    assert json.loads(body["input"][0]["content"])["website_concerns"]
    call = final["metrics"]["calls"][0]
    assert call["stage"] == "marketing" and call["stream_outcome"] == "COMPLETED"
    assert call["time_to_first_event_seconds"] is not None
    assert call["time_to_first_output_text_seconds"] is not None
    assert call["total_stage_seconds"] >= call["time_to_first_output_text_seconds"]
    assert "PRIVATE_REASONING" not in json.dumps(final)
    source.collect.assert_not_awaited()


def test_merge_never_changes_pricing_claims_or_fact_voice_and_protects_user_arrays():
    p = BIP(
        brand_identity={"website_url": "https://acme.example/"},
        brand_voice={"tone": ["Observed"]},
        offerings=[
            {
                "name": "Product",
                "key": "p",
                "description": "Useful",
                "pricing": {"amount": "10"},
                "claims": [{"text": "Clinical outcome", "scope": "OFFERING"}],
            }
        ],
    )
    proof = [
        {
            "path": "/brand_voice/tone/0",
            "origin": "AI_EXTRACTED",
            "citations": [],
            "reasoning": None,
            "recorded_at": now().isoformat(),
        }
    ]
    response = DraftMarketingResult(
        brand_voice={"tone": ["Invented"]},
        offering_insights=[
            {
                "offering_key": "p",
                "audiences": {"segments": [{"name": "Buyer", "needs": ["Useful product"]}]},
            }
        ],
    )
    new, provenance, _, diagnostics = merge_marketing(p, proof, response, replace_reviewed=True)
    assert new.brand_voice == p.brand_voice
    assert (
        new.offerings[0].pricing == p.offerings[0].pricing
        and new.offerings[0].claims == p.offerings[0].claims
    )
    assert diagnostics["merged_fields"] > 0
    assert provenance[0] == proof[0]


@pytest.mark.parametrize(
    "end,expected", [("", "DISCONNECTED"), ("response.incomplete", "INCOMPLETE")]
)
async def test_stream_outcome_and_safe_timing_on_interruption(
    chatgpt, end, expected, bundle, caplog
):
    chatgpt.end = end
    provider, _ = await chatgpt.provider()
    with pytest.raises(DomainError):
        await provider.extract_facts(bundle.documents[0].url, bundle)
    record = provider.usage_records[0]
    assert record["stream_outcome"] == expected
    assert record["time_to_first_event_seconds"] is not None
    assert record["time_to_first_output_text_seconds"] is not None
    assert record["total_stage_seconds"] >= record["time_to_first_event_seconds"]
    assert "PRIVATE_REASONING" not in caplog.text + json.dumps(record)


@pytest.mark.integration
async def test_missing_snapshot_and_unusable_offering_do_not_make_inference(
    client, factory, settings
):
    from sqlalchemy import delete

    brand, old, bundle, facts, extract = await seed(client, factory, settings)
    assert (
        client.post(
            f"/v1/brands/{brand}/marketing-enrichments", json={"offering_key": "missing"}
        ).status_code
        == 422
    )
    with factory.begin() as session:
        session.execute(
            delete(SourceDocument).where(SourceDocument.job_id == uuid.UUID(extract["id"]))
        )
    q = client.post(f"/v1/brands/{brand}/marketing-enrichments").json()
    provider = provider_for(facts, hypotheses)
    await ExtractionWorker(factory, settings, FakeSource(bundle), provider).process_once()
    assert client.get(q["poll_url"]).json()["error"]["code"] == "SOURCE_SNAPSHOT_MISSING"
    provider.infer_marketing.assert_not_awaited()


@pytest.mark.integration
async def test_discovery_ids_and_newer_observations_survive_partial_backfill(
    client, factory, settings
):
    brand, old, bundle, facts, extract = await seed(client, factory, settings)
    before = client.get(f"/v1/brands/{brand}/catalog").json()
    ids = {p["url"]: p["id"] for p in before["items"]}
    chosen = before["items"][0]
    assert client.get(f"/v1/brands/{brand}/catalog/{chosen['id']}").json()["id"] == chosen["id"]
    assert client.get(f"/v1/brands/{brand}/catalog/{uuid.uuid4()}").status_code == 404
    with factory.begin() as session:
        product = session.get(CatalogProduct, uuid.UUID(chosen["id"]))
        product.details = {**product.details, "price": "999", "observed_at": now().isoformat()}
    provider = provider_for(facts, DraftMarketingResult())
    q = client.post(f"/v1/brands/{brand}/marketing-enrichments").json()
    duplicate = client.post(f"/v1/brands/{brand}/marketing-enrichments").json()
    assert duplicate["id"] == q["id"]
    await ExtractionWorker(factory, settings, FakeSource(bundle), provider).process_once()
    after = client.get(f"/v1/brands/{brand}/catalog").json()
    assert {p["url"]: p["id"] for p in after["items"]} == ids
    provider.infer_marketing.assert_awaited_once()
    assert next(p for p in after["items"] if p["id"] == chosen["id"])["price"] == "999"


@pytest.mark.integration
async def test_prior_marketing_audit_is_preserved_when_independently_enriched(
    client, factory, settings
):
    brand, old, bundle, facts, _ = await seed(client, factory, settings)
    audit = {
        "facts": {"available": True, "accepted": 5},
        "marketing": {"available": True, "accepted": 2},
    }
    with factory.begin() as session:
        p = session.get(Profile, uuid.UUID(brand))
        p.pipeline_metadata = {
            **p.pipeline_metadata,
            "evidence_validation": audit,
            "extraction_mode": "strict",
        }
        original = list(p.provenance)
    q = client.post(f"/v1/brands/{brand}/marketing-enrichments").json()
    await ExtractionWorker(
        factory, settings, FakeSource(bundle), provider_for(facts, hypotheses)
    ).process_once()
    assert client.get(q["poll_url"]).json()["status"] == "SUCCEEDED"
    p = client.get(f"/v1/brands/{brand}/profile").json()
    assert p["evidence_validation_history"][-1]["evidence_validation"] == audit
    assert p["evidence_validation_history"][-1]["provenance"] == original
    assert p["evidence_validation"]["facts"] == audit["facts"]


@pytest.mark.integration
async def test_saved_seven_product_minimalist_profile_enriches_with_one_call(
    client, factory, settings
):
    from app.discovery import discover
    from app.schemas import ExtractionResult
    from evaluations.phase1b import ROOT, load_bundle

    bundle = load_bundle(ROOT / "evaluations/snapshots/minimalist.json")
    products, _, _ = discover(bundle, "https://beminimalist.co/")
    facts = ExtractionResult(
        profile=BIP(
            brand_identity={"website_url": "https://beminimalist.co/", "name": "Minimalist"},
            offerings=[
                {
                    "name": p["name"],
                    "url": p["url"],
                    "kind": "PRODUCT",
                    "description": next(d.title for d in bundle.documents if d.url == p["url"]),
                }
                for p in products.values()
            ],
        )
    )
    brand, old, _, _, _ = await seed(client, factory, settings, bundle, facts)
    assert len(old["profile"]["offerings"]) == 7
    provider = provider_for(facts, hypotheses)
    source = FakeSource(bundle)
    source.collect = AsyncMock(side_effect=AssertionError("No fresh crawl"))
    q = client.post(f"/v1/brands/{brand}/marketing-enrichments").json()
    await ExtractionWorker(factory, settings, source, provider).process_once()
    assert client.get(q["poll_url"]).json()["status"] == "SUCCEEDED"
    assert len(client.get(f"/v1/brands/{brand}/profile").json()["profile"]["offerings"]) == 7
    provider.infer_marketing.assert_awaited_once()
    provider.extract_facts.assert_not_awaited()
    source.collect.assert_not_awaited()


@pytest.mark.integration
async def test_stable_external_identity_survives_product_url_change(client, factory, settings):
    from app.discovery import persist_discovery
    from app.enrichment import Document, SourceBundle

    brand, _, _, _, job = await seed(client, factory, settings)
    with factory.begin() as session:
        product = session.scalar(
            select(CatalogProduct).where(CatalogProduct.brand_id == uuid.UUID(brand))
        )
        original_id = product.id
        product.details = {**product.details, "external_id": "stable-public-id"}
        session.flush()
        bundle = SourceBundle(
            [
                Document(
                    "https://beminimalist.co/catalog",
                    "Constructed catalog observation fixture",
                    now(),
                    metadata={
                        "products": [
                            {
                                "@type": "Product",
                                "productID": "stable-public-id",
                                "name": product.details["name"],
                                "url": "https://beminimalist.co/products/new-handle",
                            }
                        ]
                    },
                )
            ]
        )
        persist_discovery(
            session, uuid.UUID(brand), uuid.UUID(job["id"]), bundle, "https://beminimalist.co/"
        )
    with factory() as session:
        matches = list(
            session.scalars(
                select(CatalogProduct).where(CatalogProduct.brand_id == uuid.UUID(brand))
            )
        )
        assert len(matches) == 3
        assert (
            session.get(CatalogProduct, original_id).details["url"]
            == "https://beminimalist.co/products/new-handle"
        )


@pytest.mark.integration
async def test_profile_edit_refreshes_catalog_and_concern_relationships(client, factory, settings):
    brand, old, _, _, _ = await seed(client, factory, settings)
    original_key = old["profile"]["offerings"][0]["key"]
    products = client.get(f"/v1/brands/{brand}/catalog").json()["items"]
    product = next(p for p in products if p["url"] == old["profile"]["offerings"][0]["url"])
    offerings = old["profile"]["offerings"]
    offerings[0] = {
        **offerings[0],
        "name": "Customer-corrected product",
        "key": "customer-corrected",
    }
    edited = client.patch(
        f"/v1/brands/{brand}/profile",
        json={"expected_revision": old["revision"], "changes": {"offerings": offerings}},
    )
    assert edited.status_code == 200
    product_after = client.get(f"/v1/brands/{brand}/catalog/{product['id']}").json()
    assert product_after["linked_offering_key"] == "customer-corrected"
    assert product_after["linked_offering_id"] != product["linked_offering_id"]
    assert all(original_key not in c["offering_keys"] for c in edited.json()["customer_concerns"])
