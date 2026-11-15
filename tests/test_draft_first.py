"""Draft-first MVP regression tests. All inference and source collection is offline."""

import json
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.draft import basic_profile, claim_review, relevant_context
from app.errors import DomainError
from app.intelligence import creative_readiness, run_pipeline
from app.llm.responses import parse_output
from app.provenance import corrected_provenance, leaves
from app.schemas import (
    BIP,
    DraftFactualResult,
    DraftMarketingResult,
    ExtractionResult,
    MarketingResult,
)
from evaluations.phase1b import ROOT, load_bundle
from tests.conftest import FakeProvider, FakeSource
from tests.test_chatgpt import chatgpt as chatgpt_fixture
from tests.test_chatgpt import signing_key as signing_key_fixture

chatgpt = chatgpt_fixture
signing_key = signing_key_fixture


@pytest.fixture
def draft_settings(settings):
    settings.extraction_mode = "draft_first"
    return settings


def draft_facts(brand):
    """Constructed provider responses, not claims of actual model output."""
    if brand == "minimalist":
        sources = load_bundle(ROOT / "tests/fixtures/phase1b/minimalist-snapshot.json")
        offerings = [
            {
                "name": product["name"],
                "kind": "PRODUCT",
                "catalog_level": "PRODUCT",
                "url": doc.url,
                "description": product.get("description") or doc.title,
                "features": [doc.title],
                "ingredients": ["Vitamin C 10%"] if "Vitamin C" in product["name"] else [],
            }
            for doc in sources.documents
            for product in doc.metadata.get("products", [])[:1]
        ]
        identity = {
            "name": "Minimalist",
            "website_url": "https://beminimalist.co/",
            "description": "Skincare products with ingredient transparency.",
        }
    else:
        sources = load_bundle(ROOT / f"evaluations/snapshots/{brand}.json")
        if brand == "whole_truth":
            offerings = [
                {
                    "name": name,
                    "kind": "PRODUCT",
                    "catalog_level": "FAMILY",
                    "description": "Food with transparent ingredients.",
                    "variants": ["Small", "Large"],
                    "ingredients": ["Cocoa"],
                    "benefits": ["Protein supports health"],
                }
                for name in [
                    "Protein Bars",
                    "Protein Powder",
                    "Peanut Butter",
                    "Muesli",
                    "Dark Chocolate",
                    "Energy Bars",
                ]
            ]
            identity = {
                "name": "The Whole Truth",
                "website_url": sources.documents[0].url,
                "description": "Food with visible ingredient lists.",
            }
        elif brand == "fastlane":
            offerings = [
                {
                    "name": "Fastlane",
                    "kind": "PRODUCT",
                    "description": "Software for creating short videos.",
                    "supported_platforms": ["Instagram", "TikTok"],
                },
                {
                    "name": "Starter",
                    "pricing": {"amount": "29", "billing_period": "month", "currency": "USD"},
                },
                {"name": "Growth", "description": "Subscription plan with more credits."},
            ]
            identity = {"name": "Fastlane", "website_url": sources.documents[0].url}
        else:
            offerings = [
                {
                    "name": "Full Stack Development Course",
                    "description": "Hands-on software development course.",
                    "kind": "PRODUCT",
                },
                {"name": "Data Science Program", "description": "Practical data science training."},
            ]
            identity = {"name": "Newton School", "website_url": sources.documents[0].url}
    return sources, ExtractionResult(profile=BIP(brand_identity=identity, offerings=offerings))


def hypotheses(profile, sources, selected_offering_key=None):
    return DraftMarketingResult(
        creative_context={
            "content_pillars": ["Show a product solving a daily task"],
            "creative_angles": ["An everyday product-use story"],
        },
        offering_insights=[
            {
                "offering_key": profile.offerings[0].key,
                "audiences": {
                    "segments": [
                        {
                            "name": "Interested buyers",
                            "description": f"People exploring {profile.offerings[0].name}",
                            "needs": ["Understand product benefits"],
                        }
                    ]
                },
                "customer_psychology": {
                    "motivations": ["Find an option suited to a daily need"],
                    "objections": ["Unsure how the product fits their routine"],
                },
                "explanation": "A tentative product-to-buyer connection for review.",
            }
        ],
    )


@pytest.mark.parametrize(
    "brand,count", [("minimalist", 3), ("whole_truth", 6), ("fastlane", 1), ("newton", 2)]
)
async def test_four_brand_uncited_offerings_survive_and_marketing_runs(
    draft_settings, brand, count, monkeypatch
):
    sources, facts = draft_facts(brand)
    provider = FakeProvider(facts)
    provider.infer_marketing = AsyncMock(side_effect=hypotheses)
    # Strong guarantee that MVP mode never enters strict evidence verification.
    monkeypatch.setattr(
        "app.intelligence.validated_profile",
        Mock(side_effect=AssertionError("Strict path executed")),
    )
    result = await run_pipeline(
        provider, draft_settings, facts.profile.brand_identity.website_url, sources
    )
    assert len(result.profile.offerings) == count
    provider.infer_marketing.assert_awaited_once()
    assert result.metadata["stages"] == {"facts": "SUCCEEDED", "marketing": "SUCCEEDED"}
    assert result.metadata["extraction_mode"] == "draft_first"
    assert result.metadata["evidence_validation"]["facts"]["status"] == "NOT_APPLICABLE"
    assert result.metadata["evidence_validation"]["facts"]["acceptance_rate"] is None
    assert any(e["origin"] == "AI_EXTRACTED" for e in result.provenance)
    assert any(e["origin"] == "INFERRED" for e in result.provenance)
    assert all(not e["citations"] for e in result.provenance)
    assert set(leaves(result.profile.model_dump())) == {e["path"] for e in result.provenance}
    assert result.profile.offerings[0].audiences.segments
    if brand == "fastlane":
        assert [t.name for t in result.profile.offerings[0].pricing_tiers] == ["Starter", "Growth"]
        assert result.profile.marketing_defaults.target_channels == []
    if brand == "newton":
        assert all(o.kind == "SERVICE" for o in result.profile.offerings)
        assert not result.profile.trust_and_proof.guarantees
    if brand == "whole_truth":
        assert result.profile.offerings[0].variants == ["Small", "Large"]
        assert result.metadata["claim_review"]
        assert all(not c["approved_for_advertising"] for c in result.metadata["claim_review"])


@pytest.mark.parametrize(
    "offering",
    [
        None,
        {"name": "Unknown product"},
        {"name": "Starter", "kind": "OTHER", "description": "Plan without a parent"},
    ],
)
async def test_partial_draft_without_usable_offerings_skips_marketing(
    draft_settings, bundle, offering
):
    provider = FakeProvider(
        ExtractionResult(
            profile=BIP(
                brand_identity={
                    "website_url": bundle.documents[0].url,
                    "description": "A meaningful business description",
                },
                offerings=[offering] if offering else [],
            )
        )
    )
    provider.infer_marketing = AsyncMock(side_effect=AssertionError("Must not infer"))
    result = await run_pipeline(provider, draft_settings, bundle.documents[0].url, bundle)
    assert result.metadata["stages"] == {"facts": "INSUFFICIENT_DATA", "marketing": "SKIPPED"}
    assert result.profile.brand_identity.description
    assert result.metadata["completeness"]["missing_information"]
    provider.infer_marketing.assert_not_awaited()


async def test_empty_marketing_is_insufficient_data(draft_settings):
    sources, facts = draft_facts("minimalist")
    provider = FakeProvider(facts)
    provider.infer_marketing = AsyncMock(return_value=MarketingResult(evidence=[]))
    result = await run_pipeline(
        provider, draft_settings, facts.profile.brand_identity.website_url, sources
    )
    assert result.metadata["stages"]["marketing"] == "INSUFFICIENT_DATA"
    assert len(result.profile.offerings) == 3


@pytest.mark.parametrize("error", [TimeoutError(), DomainError("CHATGPT_TIMEOUT", "Safe timeout")])
async def test_optional_marketing_timeout_keeps_full_factual_draft(draft_settings, error):
    sources, facts = draft_facts("minimalist")
    provider = FakeProvider(facts)
    provider.infer_marketing = AsyncMock(side_effect=error)
    result = await run_pipeline(
        provider, draft_settings, facts.profile.brand_identity.website_url, sources
    )
    assert result.metadata["stages"] == {"facts": "SUCCEEDED", "marketing": "FAILED"}
    assert len(result.profile.offerings) == 3
    assert not any(e["origin"] == "INFERRED" for e in result.provenance)
    provider.infer_marketing.assert_awaited_once()


def test_light_checks_duplicates_relationships_urls_and_impossible_numbers():
    url = "https://acme.example/"
    p = BIP(
        brand_identity={"website_url": url, "founded_year": 9999},
        offerings=[
            {
                "name": "Family",
                "catalog_level": "FAMILY",
                "features": ["One", "One"],
                "parent_offering_key": "child",
            },
            {"name": "family", "description": "Description", "kind": "PRODUCT"},
            {
                "name": "Child",
                "parent_offering_key": "family",
                "url": "http://127.0.0.1/",
                "ingredients": ["Ingredient 200%"],
                "pricing": {"amount": "-10"},
            },
            {"name": "Family", "kind": "SERVICE"},
        ],
    )
    warnings = []
    clean = basic_profile(p, url, None, warnings)
    assert len(clean.offerings) == 2
    assert clean.offerings[0].features == ["One"]
    assert clean.offerings[0].description == "Description"
    assert clean.offerings[1].ingredients == [] and clean.offerings[1].url is None
    assert clean.offerings[1].pricing.amount is None
    assert clean.brand_identity.founded_year is None
    assert warnings and not clean.offerings[0].parent_offering_key


def test_contradictory_tiers_are_deduplicated_and_flagged():
    p = BIP(
        brand_identity={"website_url": "https://acme.example/"},
        offerings=[
            {
                "name": "App",
                "description": "Software",
                "pricing_tiers": [
                    {"name": "Pro", "pricing": {"amount": "10"}},
                    {"name": "Pro", "pricing": {"amount": "20"}},
                ],
            }
        ],
    )
    warnings = []
    clean = basic_profile(p, p.brand_identity.website_url, None, warnings)
    assert len(clean.offerings[0].pricing_tiers) == 1
    assert clean.offerings[0].pricing_tiers[0].pricing.amount == "10"
    assert any("conflict" in w for w in warnings)


@pytest.mark.parametrize(
    "value",
    [
        "broken JSON",
        '{"profile":{"brand_identity":{"website_url":"https://acme.example/"},"offerings":[{}]}}',
        '{"profile": {"brand_identity":{"website_url":"https://acme.example/"}},"citations":[]}',
    ],
)
def test_default_wire_rejects_malformed_or_legacy_evidence_envelopes(value):
    with pytest.raises(ValidationError):
        parse_output(value, DraftFactualResult)


async def test_default_wire_and_stream_use_no_evidence_contract(chatgpt, bundle):
    chatgpt.settings.extraction_mode = "draft_first"
    chatgpt.facts = DraftFactualResult(
        profile={
            "brand_identity": {"website_url": bundle.documents[0].url},
            "offerings": [
                {
                    "name": "Flow",
                    "description": "Collaboration software",
                    "benefits": ["Coordinate tasks"],
                }
            ],
        }
    )
    chatgpt.marketing = DraftMarketingResult(
        creative_context={"creative_angles": ["A team handoff"]}
    )
    provider, _ = await chatgpt.provider()
    result = await run_pipeline(provider, chatgpt.settings, bundle.documents[0].url, bundle)
    assert result.metadata["stages"]["marketing"] == "SUCCEEDED"
    calls = [json.loads(r.content) for r in chatgpt.calls if r.url.path == "/v1/responses"]
    assert len(calls) == 2
    for body in calls:
        schema = json.dumps(body["text"]["format"]["schema"])
        assert '"evidence"' not in schema and '"citations"' not in schema
        assert body["store"] is False and body["stream"] is True
        assert body["model"] == "account-model"
    assert "extracted_profile" in json.loads(calls[1]["input"][0]["content"])
    assert "verified_profile" not in json.loads(calls[1]["input"][0]["content"])


@pytest.mark.parametrize(
    "symptom,code",
    [("response.incomplete", "CHATGPT_RESPONSE_INCOMPLETE"), ("", "CHATGPT_STREAM_DISCONNECTED")],
)
async def test_draft_stream_still_requires_complete_response(chatgpt, bundle, symptom, code):
    chatgpt.settings.extraction_mode = "draft_first"
    chatgpt.end = symptom
    provider, _ = await chatgpt.provider()
    with pytest.raises(DomainError) as error:
        await provider.extract_facts(bundle.documents[0].url, bundle)
    assert error.value.code == code


def test_default_mode_is_draft_first_and_historical_provenance_is_unchanged(
    extraction_result, bundle
):
    from app.provenance import validated_profile

    assert Settings(_env_file=None).extraction_mode == "draft_first"
    old, proof = validated_profile(extraction_result, bundle, bundle.documents[0].url, None)
    assert corrected_provenance(old, old, proof) == proof
    assert all(p["origin"] in {"WEBSITE", "USER"} for p in proof)


@pytest.mark.integration
async def test_draft_postgres_edit_confirm_claim_review_and_history(
    client, factory, draft_settings
):
    from app.worker import ExtractionWorker

    sources, facts = draft_facts("minimalist")
    provider = FakeProvider(facts)
    provider.infer_marketing = AsyncMock(side_effect=hypotheses)
    brand = client.post(
        "/v1/brands", json={"website_url": facts.profile.brand_identity.website_url}
    ).json()["id"]
    queued = client.post(f"/v1/brands/{brand}/extractions").json()
    assert await ExtractionWorker(
        factory, draft_settings, FakeSource(sources), provider
    ).process_once()
    assert client.get(queued["poll_url"]).json()["status"] == "SUCCEEDED"
    p = client.get(f"/v1/brands/{brand}/profile").json()
    assert p["extraction_mode"] == "draft_first" and p["status"] == "DRAFT"
    assert len(p["profile"]["offerings"]) == 3
    assert p["claim_review"] and all(not c["approved_for_advertising"] for c in p["claim_review"])
    ids = [i["id"] for i in client.get(f"/v1/brands/{brand}/offerings").json()["items"]]
    edit = client.patch(
        f"/v1/brands/{brand}/profile",
        json={
            "expected_revision": p["revision"],
            "changes": {"brand_identity": {"tagline": "User tagline"}},
        },
    ).json()
    assert (
        next(e for e in edit["provenance"] if e["path"] == "/brand_identity/tagline")["origin"]
        == "USER"
    )
    assert (
        next(e for e in edit["provenance"] if e["path"] == "/offerings/0/name")["origin"]
        == "AI_EXTRACTED"
    )
    confirmed = client.post(
        f"/v1/brands/{brand}/profile/confirm", json={"expected_revision": edit["revision"]}
    ).json()
    assert confirmed["status"] == "CONFIRMED"
    assert (
        next(e for e in confirmed["provenance"] if e["path"] == "/offerings/0/name")["origin"]
        == "AI_EXTRACTED"
    )
    assert all(not c["approved_for_advertising"] for c in confirmed["claim_review"])
    assert ids == [i["id"] for i in client.get(f"/v1/brands/{brand}/offerings").json()["items"]]


@pytest.mark.parametrize(
    "field,value",
    [
        ("benefits", "Clinically reduces acne"),
        ("ingredients", "Niacinamide 10%"),
        ("benefits", "Supports heart health"),
        ("features", "ISO certified facility"),
        ("features", "Guaranteed results"),
        ("benefits", "3x faster results"),
    ],
)
def test_sensitive_claims_stay_provisional(field, value):
    p = BIP(
        brand_identity={"website_url": "https://acme.example/"},
        offerings=[{"name": "Product", field: [value]}],
    )
    risks = claim_review(p, [])
    assert risks and risks[0]["status"] == "REQUIRES_REVIEW"
    assert not risks[0]["approved_for_advertising"]
    assert not risks[0]["independently_verified"]


def test_valid_hierarchy_and_pricing_review_without_citations():
    p = BIP(
        brand_identity={"website_url": "https://acme.example/"},
        offerings=[
            {"name": "Serums", "key": "serums", "catalog_level": "FAMILY"},
            {
                "name": "Vitamin C",
                "parent_offering_key": "serums",
                "catalog_level": "PRODUCT",
                "pricing": {"amount": "285", "currency": "INR"},
            },
        ],
    )
    result = basic_profile(p, p.brand_identity.website_url, None, [])
    assert result.offerings[1].parent_offering_key == "serums"
    assert any("PRICING_OR_PROMOTION" in r["reasons"] for r in claim_review(result, []))


async def test_invalid_marketing_schema_preserves_factual_profile(draft_settings):
    sources, facts = draft_facts("minimalist")
    provider = FakeProvider(facts)
    provider.infer_marketing = AsyncMock(
        return_value=DraftMarketingResult.model_construct(
            offering_insights=[{"offering_key": "unknown", "audiences": 42}]
        )
    )
    with pytest.warns(UserWarning, match="serializer"):
        result = await run_pipeline(
            provider, draft_settings, facts.profile.brand_identity.website_url, sources
        )
    assert len(result.profile.offerings) == 3
    assert result.metadata["stage_errors"]["marketing"]["code"] == "MARKETING_INVALID_OUTPUT"


async def test_entirely_empty_profile_is_saved_without_marketing(draft_settings, bundle):
    provider = FakeProvider(
        ExtractionResult(profile=BIP(brand_identity={"website_url": bundle.documents[0].url}))
    )
    provider.infer_marketing = AsyncMock()
    result = await run_pipeline(provider, draft_settings, bundle.documents[0].url, bundle)
    assert result.metadata["stages"] == {"facts": "INSUFFICIENT_DATA", "marketing": "SKIPPED"}
    provider.infer_marketing.assert_not_awaited()


def test_relevant_passages_are_bounded_and_do_not_require_quotes():
    sources, facts = draft_facts("minimalist")
    p = basic_profile(facts.profile, facts.profile.brand_identity.website_url, None, [])
    context = relevant_context(sources, p, p.offerings[0].key, 2000)
    assert context.documents and sum(len(d.text) for d in context.documents) <= 2000


def test_readiness_requires_product_audience_and_creative_content():
    p = BIP(
        brand_identity={
            "name": "Acme",
            "website_url": "https://acme.example/",
            "description": "Useful software",
        },
        offerings=[
            {
                "name": "Flow",
                "key": "flow",
                "benefits": ["Coordinate tasks"],
                "audiences": {"segments": [{"name": "Teams", "needs": ["Share work"]}]},
                "customer_psychology": {"motivations": ["Reduce handoff friction"]},
            }
        ],
        brand_voice={"tone": ["Clear"]},
        creative_context={"creative_angles": ["A team handoff"]},
    )
    r = creative_readiness(p, "flow", [], mode="draft_first")
    assert r["status"] == "READY_FOR_CREATIVE_BRIEF" and r["ready_for_campaign"] is False
    p.offerings[0].audiences.segments = []
    assert creative_readiness(p, "flow", [], mode="draft_first")["status"] == "NEEDS_REVIEW"


@pytest.mark.integration
async def test_replacing_strict_profile_preserves_original_audit_and_evidence(
    client, factory, settings, bundle, extraction_result
):
    import uuid

    from app.models import Profile
    from app.worker import ExtractionWorker

    brand = client.post("/v1/brands", json={"website_url": bundle.documents[0].url}).json()["id"]
    client.post(f"/v1/brands/{brand}/extractions")
    await ExtractionWorker(
        factory, settings, FakeSource(bundle), FakeProvider(extraction_result)
    ).process_once()
    old = client.get(f"/v1/brands/{brand}/profile").json()
    assert old["extraction_mode"] == "strict"
    with factory() as session:
        stored_proof = session.get(Profile, uuid.UUID(brand)).provenance
    settings.extraction_mode = "draft_first"
    facts = extraction_result.model_copy(deep=True)
    facts.profile.offerings[0].description = "Collaboration software"
    client.post(f"/v1/brands/{brand}/extractions", json={"replace_existing": True})
    await ExtractionWorker(
        factory, settings, FakeSource(bundle), FakeProvider(facts)
    ).process_once()
    new = client.get(f"/v1/brands/{brand}/profile").json()
    assert new["extraction_mode"] == "draft_first"
    assert (
        new["evidence_validation_history"][0]["evidence_validation"] == old["evidence_validation"]
    )
    assert new["evidence_validation_history"][0]["provenance"] == stored_proof
    assert new["evidence_validation_history"][0]["revision"] == old["revision"]
