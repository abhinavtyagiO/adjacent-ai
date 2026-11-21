"""Missing citations are a quality outcome, never permission to spend on stage two."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.errors import DomainError
from app.intelligence import marketing_sources, run_pipeline
from app.llm.responses import parse_output
from app.provenance import leaves
from app.schemas import BIP, CitedFactualResult, ExtractionResult, FactualResult
from evaluations.phase1b import ROOT, load_bundle
from tests.conftest import FakeProvider


def missing_facts(count=179):
    # Exactly 179 generated factual leaves, plus the authoritative URL anchor.
    # Names/feature strings are deliberately unsupported: no citation may be invented.
    return FactualResult(
        profile={
            "brand_identity": {"website_url": "https://beminimalist.co/"},
            "offerings": [
                {"name": "Unverified product", "features": [f"Fact {i}" for i in range(count - 1)]}
            ],
        },
        evidence=[],
        warnings=["Evidence entries have been omitted to keep the response concise."],
    )


@pytest.mark.parametrize("count", [178, 179])
async def test_uncited_facts_keep_empty_draft_and_never_call_marketing(settings, count):
    sources = load_bundle(ROOT / "evaluations/snapshots/whole_truth.json")
    parsed = parse_output(missing_facts(count).model_dump_json(), FactualResult)
    assert (
        len(leaves(parsed.profile.model_dump())) == count + 1
    )  # Also covers exactly 179 raw leaves.
    provider = FakeProvider(
        ExtractionResult(
            profile=BIP.model_validate(parsed.profile.model_dump()),
            warnings=parsed.warnings,
        )
    )
    provider.infer_marketing = AsyncMock(side_effect=AssertionError("Marketing must not run"))
    result = await run_pipeline(provider, settings, "https://beminimalist.co/", sources)
    provider.infer_marketing.assert_not_awaited()
    assert not result.profile.offerings
    assert leaves(result.profile.model_dump()) == {
        "/brand_identity/website_url": "https://beminimalist.co/"
    }
    assert result.metadata["stages"] == {"facts": "INSUFFICIENT_EVIDENCE", "marketing": "SKIPPED"}
    audit = result.metadata["evidence_validation"]["facts"]
    assert (audit["generated"], audit["accepted"], audit["rejected"]) == (count, 0, count)
    assert audit["rejection_reasons"] == {"MISSING_EVIDENCE": count}
    assert audit["parsed_output"] == {
        "populated_fields": count + 1,
        "evidence_entries": 0,
        "evidence_paths": 0,
    }
    assert not result.metadata["evidence_validation"]["marketing"]["available"]


async def test_chatgpt_empty_citations_are_not_retried_or_sent_to_marketing(chatgpt, bundle):
    chatgpt.facts = CitedFactualResult(profile=missing_facts().profile, citations=[])
    chatgpt.settings.llm_max_attempts = 5  # Still no repeated inference for evidence omissions.
    provider, _ = await chatgpt.provider()
    result = await run_pipeline(provider, chatgpt.settings, "https://beminimalist.co/", bundle)
    assert result.metadata["stages"]["facts"] == "INSUFFICIENT_EVIDENCE"
    assert len([r for r in chatgpt.calls if r.url.path == "/v1/responses"]) == 1
    assert provider.factual_parse_counts["evidence_entries"] == 0
    assert result.metadata["evidence_validation"]["facts"]["rejected"] == 179


async def test_identity_only_is_insufficient(settings, bundle, extraction_result):
    extraction_result.profile.offerings = []
    provider = FakeProvider(extraction_result)
    provider.infer_marketing = AsyncMock()
    result = await run_pipeline(provider, settings, bundle.documents[0].url, bundle)
    assert result.profile.brand_identity.name == "Acme"
    assert result.metadata["stages"]["facts"] == "INSUFFICIENT_EVIDENCE"
    provider.infer_marketing.assert_not_awaited()


def test_marketing_context_is_verified_bounded_and_product_prioritized(bundle):
    profile = BIP(
        brand_identity={"website_url": bundle.documents[0].url},
        offerings=[{"name": "Flow", "key": "flow"}],
    )
    entries = [
        {
            "path": "/brand_identity/description",
            "origin": "WEBSITE",
            "citations": [
                {
                    "source_url": bundle.documents[0].url,
                    "quote": "Acme makes collaboration software.",
                }
            ],
        },
        {
            "path": "/offerings/0/name",
            "origin": "WEBSITE",
            "citations": [{"source_url": bundle.documents[0].url, "quote": "Flow is our product."}],
        },
    ]
    context = marketing_sources(bundle, entries, "flow", profile, 30)
    assert context.documents[0].text == "Flow is our product."
    assert "Advisory" not in context.documents[0].text
    assert context.documents[0].fetched_at == bundle.documents[0].fetched_at
    assert marketing_sources(bundle, [], None, profile, 1000).documents == []


@pytest.mark.parametrize(
    "failure,expected",
    [("deadline", "CHATGPT_TIMEOUT"), ("disconnect", "CHATGPT_STREAM_DISCONNECTED")],
)
async def test_stage_specific_timeout_and_connection_errors(
    chatgpt, bundle, failure, expected, monkeypatch
):
    import asyncio

    provider, _ = await chatgpt.provider()
    provider.settings.factual_timeout_seconds = 0.01
    provider.settings.marketing_timeout_seconds = 0.5
    assert provider.settings.stage_timeout("marketing") == 0.5

    async def fail(*args):
        if failure == "deadline":
            await asyncio.sleep(1)
        raise httpx.ReadError("PRIVATE_DISCONNECTION_DETAIL")

    monkeypatch.setattr(provider, "_stream", fail)
    with pytest.raises(DomainError) as error:
        await provider.extract_facts(bundle.documents[0].url, bundle)
    assert error.value.code == expected and not error.value.retryable
    assert "PRIVATE" not in error.value.message
    assert provider.usage_records[0]["error_code"] == expected


async def test_compact_citation_expands_paths_without_weakening_checks(chatgpt, bundle):
    chatgpt.facts = CitedFactualResult(
        profile={
            "brand_identity": {
                "website_url": bundle.documents[0].url,
                "name": "Acme",
                "description": "Acme makes collaboration software.",
            }
        },
        citations=[
            {
                "source_id": "s0",
                "quote": "Acme makes collaboration software.",
                "paths": ["/brand_identity/name", "/brand_identity/description"],
            }
        ],
    )
    provider, _ = await chatgpt.provider()
    result = await run_pipeline(provider, chatgpt.settings, bundle.documents[0].url, bundle)
    audit = result.metadata["evidence_validation"]["facts"]
    assert audit["accepted"] == 2 and audit["parsed_output"]["evidence_entries"] == 1
    assert audit["parsed_output"]["evidence_paths"] == 2
    request = next(r for r in chatgpt.calls if r.url.path == "/v1/responses")
    payload = json.loads(json.loads(request.content)["input"][0]["content"])
    assert payload["documents"][0]["source_id"] == "s0"


@pytest.mark.parametrize(
    "source_id,quote,reason",
    [
        ("invented", "Acme", "UNKNOWN_SOURCE"),
        ("s0", "Invented passage", "QUOTE_NOT_FOUND"),
        ("s0", "Acme makes collaboration software.", "EXACT_VALUE_MISMATCH"),
    ],
)
async def test_compact_invalid_support_is_rejected(chatgpt, bundle, source_id, quote, reason):
    chatgpt.facts = CitedFactualResult(
        profile={
            "brand_identity": {"website_url": bundle.documents[0].url},
            "offerings": [{"name": "Unstated product"}],
        },
        citations=[{"source_id": source_id, "quote": quote, "paths": ["/offerings/0/name"]}],
    )
    provider, _ = await chatgpt.provider()
    result = await run_pipeline(provider, chatgpt.settings, bundle.documents[0].url, bundle)
    assert result.metadata["stages"]["marketing"] == "SKIPPED"
    assert result.metadata["evidence_validation"]["facts"]["rejection_reasons"] == {reason: 1}


# Pytest discovers fixtures imported from other test modules.
from tests.test_chatgpt import chatgpt as chatgpt_fixture  # noqa: E402
from tests.test_chatgpt import signing_key as signing_key_fixture  # noqa: E402

chatgpt = chatgpt_fixture
signing_key = signing_key_fixture
