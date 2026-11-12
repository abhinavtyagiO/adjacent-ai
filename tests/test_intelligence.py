"""Deterministic, explicitly constructed regression cases; no paid network calls."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.crawling.website import page_category, parse_page, sitemap_urls
from app.enrichment import Document, SourceBundle
from app.errors import DomainError
from app.intelligence import (
    add_catalog_classifications,
    creative_readiness,
    factual_consistency,
    metrics,
    run_pipeline,
)
from app.llm.base import build_provider
from app.models import now
from app.provenance import leaves, validated_profile
from app.schemas import BIP, ExtractionResult, LLMEvidence, MarketingResult
from tests.conftest import FakeProvider
from tests.test_llm import mock_client

EXAMPLES = json.loads(
    (Path(__file__).parent / "fixtures/phase1b/supported_examples.json").read_text()
)


def case(name):
    row = EXAMPLES[name]
    return row["url"], row["text"], SourceBundle([Document(row["url"], row["text"], now())])


def proof(path, url, quote, origin="WEBSITE"):
    return LLMEvidence(
        path=path,
        origin=origin,
        reasoning="Context supports this tentative interpretation."
        if origin != "WEBSITE"
        else None,
        citations=[{"source_url": url, "quote": quote}],
    )


def verify(data, entries, bundle, url):
    warnings = []
    result = factual_consistency(
        ExtractionResult(profile=BIP.model_validate(data), evidence=entries), bundle, warnings
    )
    profile, provenance = validated_profile(result, bundle, url, None, warnings=warnings)
    add_catalog_classifications(profile, provenance, bundle, warnings)
    return profile, provenance, warnings


def test_fastlane_tiers_and_platforms_are_not_products_or_marketing_defaults():
    url, text, bundle = case("fastlane")
    data = {
        "brand_identity": {"website_url": url, "name": "Fastlane"},
        "offerings": [{"name": "Fastlane"}, {"name": "Starter"}, {"name": "Growth"}],
        "marketing_defaults": {"target_channels": ["Instagram", "TikTok"]},
    }
    entries = [proof("/brand_identity/name", url, text)]
    entries += [proof(f"/offerings/{i}/name", url, text) for i in range(3)]
    entries += [proof(f"/marketing_defaults/target_channels/{i}", url, text) for i in range(2)]
    profile, _, _ = verify(data, entries, bundle, url)
    assert [o.name for o in profile.offerings] == ["Fastlane"]
    assert [t.name for t in profile.offerings[0].pricing_tiers] == ["Starter", "Growth"]
    assert profile.marketing_defaults.target_channels == []


def test_whole_truth_catalog_exceeds_four_and_kind_is_explainable_classification():
    url, text, bundle = case("whole_truth")
    names = [
        "Protein Bars",
        "Protein Powder",
        "Peanut Butter",
        "Muesli",
        "Dark Chocolate",
        "Energy Bars",
    ]
    data = {
        "brand_identity": {"website_url": url},
        "offerings": [{"name": n, "kind": "PRODUCT"} for n in names],
    }
    entries = [
        proof(f"/offerings/{i}/{field}", url, text, "CLASSIFIED" if field == "kind" else "WEBSITE")
        for i in range(6)
        for field in ["name", "kind"]
    ]
    profile, provenance, _ = verify(data, entries, bundle, url)
    assert len(profile.offerings) == 6
    assert all(o.kind == "PRODUCT" for o in profile.offerings)
    assert all(p["origin"] == "CLASSIFIED" for p in provenance if p["path"].endswith("/kind"))


def test_newton_course_classification_remains_service_even_when_it_teaches_software():
    url, text, bundle = case("newton")
    data = {
        "brand_identity": {"website_url": url},
        "offerings": [{"name": "Full Stack Development Course"}],
    }
    profile, _, _ = verify(data, [proof("/offerings/0/name", url, text)], bundle, url)
    assert profile.offerings[0].kind == "SERVICE"


def test_facility_and_newsletter_claims_keep_scope_and_numeric_inflation_is_removed():
    url, text, bundle = case("whole_truth")
    data = {
        "brand_identity": {"website_url": url},
        "trust_and_proof": {
            "certifications": ["ISO certified"],
            "metrics": ["1,000,000 readers", "100,000 readers"],
        },
    }
    entries = [
        proof(
            "/trust_and_proof/certifications/0", url, "Our manufacturing facility is ISO certified."
        ),
        proof("/trust_and_proof/metrics/0", url, "Our newsletter has 100,000 readers."),
        proof("/trust_and_proof/metrics/1", url, "Our newsletter has 100,000 readers."),
    ]
    profile, _, _ = verify(data, entries, bundle, url)
    assert profile.trust_and_proof.certifications == []
    assert profile.trust_and_proof.metrics == []
    assert {c.scope for c in profile.trust_and_proof.scoped_claims} == {"FACILITY", "NEWSLETTER"}
    assert all("1,000,000" not in c.text for c in profile.trust_and_proof.scoped_claims)


def test_contradictory_scope_drops_whole_claim_without_blocking_draft():
    url, text, bundle = case("whole_truth")
    data = {
        "brand_identity": {"website_url": url},
        "trust_and_proof": {"scoped_claims": [{"text": "ISO certified", "scope": "BRAND"}]},
    }
    entries = [
        proof(
            "/trust_and_proof/scoped_claims/0/" + field,
            url,
            text,
            "CLASSIFIED" if field == "scope" else "WEBSITE",
        )
        for field in ["text", "scope"]
    ]
    profile, _, _ = verify(data, entries, bundle, url)
    assert profile.trust_and_proof.scoped_claims == []


async def test_two_stages_preserve_facts_and_ground_hypotheses(settings, bundle, extraction_result):
    url = bundle.documents[0].url
    provider = FakeProvider(extraction_result)
    provider.infer_marketing = AsyncMock(
        return_value=MarketingResult(
            customer_personas_and_audience_segments={"personas": [{"name": "Team coordinators"}]},
            creative_context={
                "creative_angles": ["Show a handoff between collaborating teammates"]
            },
            offering_insights=[
                {
                    "offering_key": "flow",
                    "audiences": {"segments": [{"name": "Collaboration teams"}]},
                }
            ],
            evidence=[
                proof(
                    "/customer_personas_and_audience_segments/personas/0/name",
                    url,
                    "Acme makes collaboration software.",
                    "INFERRED",
                ),
                proof(
                    "/creative_context/creative_angles/0",
                    url,
                    "Acme makes collaboration software.",
                    "INFERRED",
                ),
                proof(
                    "/offering_insights/0/audiences/segments/0/name",
                    url,
                    "Flow is our product.",
                    "INFERRED",
                ),
            ],
        )
    )
    result = await run_pipeline(provider, settings, url, bundle)
    assert result.profile.brand_identity.name == "Acme"
    assert result.profile.offerings[0].audiences.segments[0].name == "Collaboration teams"
    assert result.profile.creative_context.creative_angles
    assert result.metadata["creative_readiness"]["scope"] == "BRAND"
    assert any(p["origin"] == "INFERRED" for p in result.provenance)
    assert set(leaves(result.profile.model_dump())) == {p["path"] for p in result.provenance}


@pytest.mark.parametrize("failure", [DomainError("LLM_UNAVAILABLE", "Unavailable"), TimeoutError()])
async def test_optional_marketing_failure_retains_verified_draft(
    settings, bundle, extraction_result, failure
):
    provider = FakeProvider(extraction_result)
    provider.infer_marketing = AsyncMock(side_effect=failure)
    result = await run_pipeline(provider, settings, bundle.documents[0].url, bundle)
    assert result.profile.brand_identity.name == "Acme"
    assert result.profile.offerings
    assert result.metadata["stages"]["marketing"] == "FAILED"
    assert any("draft retained" in w for w in result.warnings)


async def test_malformed_optional_pointer_cannot_crash_pipeline(
    settings, bundle, extraction_result
):
    extraction_result.evidence.append(proof("", bundle.documents[0].url, "Flow is our product."))
    result = await run_pipeline(
        FakeProvider(extraction_result), settings, bundle.documents[0].url, bundle
    )
    assert result.profile.brand_identity.name == "Acme"


def test_readiness_is_nonblocking_and_highlights_offering_targeting_gap():
    result = creative_readiness(BIP(brand_identity={"website_url": "https://acme.example/"}))
    assert result["status"] == "NEEDS_REVIEW"
    assert result["requires_human_review"]
    assert any(m["field"] == "selected_offering_key" for m in result["missing_information"])
    assert all(m["suggested_question"] for m in result["missing_information"])


@pytest.mark.parametrize(
    "url,category",
    [
        ("/for/ecommerce", "use_case"),
        ("/tools/hook-generator", "use_case"),
        ("/pages/our-story", "brand_story"),
        ("/pages/terms-of-service", "other"),
        ("/blog/what-about-eggs", "other"),
        ("/review/other-company", "comparison"),
        ("/categories/muesli", "category"),
        ("/products/muesli", "product"),
        ("/agentic-generalist-program", "product"),
        ("/about-us", "brand_story"),
    ],
)
def test_page_categories_do_not_inflate_coverage(url, category):
    assert page_category("https://acme.example" + url) == category


def test_page_keeps_headings_tables_faq_and_jsonld():
    text, _, _, metadata = parse_page(
        """<title>Catalog</title><main><h1>Our products</h1>
    <p>Real context</p><ul><li>Clear ingredients</li></ul>
    <table><tr><td>Starter</td><td>$29</td></tr></table></main>
    <script type="application/ld+json">{"@graph":[{"@type":"Product","name":"Bar"},
    {"@type":"Question","name":"What is inside?",
    "acceptedAnswer":{"text":"Only nuts."}}]}</script>""",
        "https://acme.example/products",
        3000,
    )
    assert "# Our products" in text and "Starter | $29" in text and "- Clear ingredients" in text
    assert "What is inside?" in text and metadata["products"][0]["name"] == "Bar"


def test_soft_404_and_xml_entities_are_rejected():
    with pytest.raises(DomainError, match="soft-404"):
        parse_page("<h1>404 page not found</h1>", "https://acme.example/pricing", 3000)
    with pytest.raises(ValueError):
        sitemap_urls('<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///etc/passwd">]><urlset/>', 10)
    assert sitemap_urls(
        "<urlset><url><loc>https://acme.example/products</loc></url></urlset>", 10
    ) == (["https://acme.example/products"], False)


async def test_stage_models_efforts_and_output_budgets_are_independent(
    settings, bundle, monkeypatch
):
    from app.schemas import FactualResult

    settings.deepseek_api_key = SecretStr("test-only")
    settings.factual_model = "factual-test-model"
    settings.marketing_model = "marketing-test-model"
    settings.factual_reasoning_effort = "none"
    settings.marketing_reasoning_effort = "low"
    client = mock_client(
        monkeypatch,
        [
            SimpleNamespace(
                output_text=FactualResult(
                    profile={"brand_identity": {"website_url": bundle.documents[0].url}}
                ).model_dump_json()
            ),
            SimpleNamespace(output_text=MarketingResult().model_dump_json()),
        ],
    )
    provider = build_provider(settings)
    await provider.extract_facts(bundle.documents[0].url, bundle)
    await provider.infer_marketing(
        BIP(brand_identity={"website_url": bundle.documents[0].url}), bundle
    )
    first, second = [call.kwargs for call in client.responses.create.call_args_list]
    assert first["model"] == "factual-test-model" and first["reasoning"] == {"effort": "none"}
    assert second["model"] == "marketing-test-model" and second["reasoning"] == {"effort": "low"}
    assert first["max_output_tokens"] == settings.factual_max_output_tokens
    assert second["max_output_tokens"] == settings.marketing_max_output_tokens


def test_missing_attempt_usage_does_not_report_zero_cost(settings):
    settings.llm_input_usd_per_million = 0.3
    settings.llm_output_usd_per_million = 1.2
    result = metrics([{"input_tokens": None, "output_tokens": None}], settings, 1)
    assert not result["usage_complete"] and result["estimated_cost_usd"] is None
    known = metrics([{"input_tokens": 1000, "output_tokens": 100}], settings, 1)
    assert known["estimated_cost_usd"] == pytest.approx(0.00042)


def test_single_json_fence_optional_objects_and_pointer_spellings():
    from app.llm.responses import parse_output
    from app.schemas import FactualResult

    parsed = parse_output(
        '```json\n{"profile":{"brand_identity":{"website_url":"https://acme.example/"},"offerings":[{"name":"Flow","pricing":null}]},"evidence":[{"path":"profile.offerings[0].name","origin":"WEBSITE","citations":[]}]}\n```',
        FactualResult,
    )
    assert parsed.evidence[0].path == "/offerings/0/name"
    assert parsed.profile.offerings[0].pricing.amount is None
    with pytest.raises(ValueError):
        parse_output('Commentary before {"profile":{}}', FactualResult)


def test_grouped_catalog_evidence_recovers_only_literal_leaves():
    from app.intelligence import literal_catalog_evidence

    url, text, bundle = case("newton")
    result = ExtractionResult(
        profile=BIP(
            brand_identity={"website_url": url},
            offerings=[
                {
                    "name": "Full Stack Development Course",
                    "description": "Guaranteed millionaire outcomes",
                    "kind": "SERVICE",
                }
            ],
        ),
        evidence=[proof("/offerings/0", url, text)],
    )
    result = literal_catalog_evidence(result, bundle)
    paths = {e.path for e in result.evidence}
    assert "/offerings/0/name" in paths
    assert "/offerings/0/description" not in paths
    assert "/offerings/0/kind" not in paths
    profile, _ = validated_profile(result, bundle, url, None)
    assert profile.offerings[0].name == "Full Stack Development Course"
    assert profile.offerings[0].description is None


async def test_malformed_paid_response_usage_is_recorded_before_validation(
    settings, bundle, monkeypatch
):
    settings.deepseek_api_key = SecretStr("test-only")
    settings.llm_max_attempts = 1
    mock_client(
        monkeypatch,
        [
            SimpleNamespace(
                output_text="{bad json",
                status="completed",
                usage=SimpleNamespace(
                    input_tokens=100, output_tokens=10, input_tokens_details=None
                ),
            )
        ],
    )
    provider = build_provider(settings)
    with pytest.raises(DomainError):
        await provider.extract_facts(bundle.documents[0].url, bundle)
    assert provider.usage_records[0]["input_tokens"] == 100
    assert provider.usage_records[0]["output_tokens"] == 10
    assert provider.usage_records[0]["status"] == "failed"


@pytest.mark.parametrize("brand,minimum", [("fastlane", 2), ("whole_truth", 9), ("newton", 7)])
async def test_recorded_catalog_regressions_without_network_or_model(settings, brand, minimum):
    from app.schemas import ExtractionResult
    from evaluations.phase1b import ROOT, load_bundle

    source = load_bundle(ROOT / "evaluations/snapshots" / (brand + ".json"))
    facts = ExtractionResult.model_validate_json(
        (ROOT / "evaluations/results" / (brand + "-none-facts.json")).read_text()
    )
    result = await run_pipeline(FakeProvider(facts), settings, source.documents[0].url, source)
    assert len(result.profile.offerings) >= minimum
    assert set(leaves(result.profile.model_dump())) == {p["path"] for p in result.provenance}
    if brand == "fastlane":
        assert not {"starter", "growth", "pro", "enterprise"} & {
            o.name.lower() for o in result.profile.offerings
        }
        assert sum(len(o.pricing_tiers) for o in result.profile.offerings) >= 3
        assert result.profile.marketing_defaults.target_channels == []
    elif brand == "whole_truth":
        assert all(o.kind for o in result.profile.offerings)
        assert not {"Truth Be Told", "ChemX"} & {o.name for o in result.profile.offerings}
    else:
        assert sum(o.kind == "SERVICE" for o in result.profile.offerings) >= 6
