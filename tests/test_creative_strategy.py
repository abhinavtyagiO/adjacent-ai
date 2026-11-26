"""Offline contracts and conservative validation; no claim of live creative quality."""

import json

import pytest
from pydantic import ValidationError

from app.creative.prompts import EVALUATOR, GENERATOR
from app.creative.provider import CreativeProvider
from app.creative.research import build_context
from app.creative.schemas import CampaignBrief, Candidate
from app.creative.validation import check_candidate, validate_candidates, validate_evaluation
from app.errors import DomainError
from app.schemas import BIP
from tests.creative_helpers import (
    MockCreativeTransport,
    docs,
    evaluated,
    fixture,
    generated,
    research,
)


@pytest.mark.parametrize("name", ["opal", "minimalist"])
def test_benchmarks_are_bound_diverse_and_not_canned_runtime_references(name):
    context = research(name)
    result = generated(context, name)
    accepted, diagnostics = validate_candidates(result, context)
    assert len(accepted) == 6 and diagnostics["rejected"] == 0
    assert len({c.strategic_mechanism for c, _ in accepted}) == 6
    assert len({c.psychological_driver for c, _ in accepted}) >= 3
    assert context["brief"]["region"] is None
    for manual in fixture(name)["manual_quality_references"]:
        assert manual["title"] not in GENERATOR + EVALUATOR
        assert manual["title"] not in {c.title for c, _ in accepted}
    mapping = validate_evaluation(evaluated(result.candidates), result.candidates)
    assert sum(e["selected"] for e in mapping.values()) == 5


def test_research_retains_opinions_hypotheses_guardrails_and_visuals_without_promoting_them():
    context = research("minimalist")
    assert len(json.dumps(context, ensure_ascii=False)) <= 30000
    assert any(i["kind"] == "AI_HYPOTHESIS" for i in context["items"])
    assert any(i["path"].startswith("/visual_identity/") for i in context["items"])
    negative = [i for i in context["items"] if i.get("sentiment") == "NEGATIVE_OR_MIXED"]
    assert negative and all(
        i["kind"] == "CUSTOMER_OPINION" and not i["capability"] for i in negative
    )
    assert any("burns" in i["text"] for i in negative)
    assert context["restrictions"]["brand_safety_rules"]


def test_verified_observation_requires_matching_saved_quote():
    data = fixture("opal")
    profile = BIP.model_validate(data["profile"])
    sources = docs(data)
    quote = sources[0].content[:60]
    profile.brand_identity.description = quote
    entries = [
        {
            "path": "/brand_identity/description",
            "origin": "WEBSITE",
            "citations": [{"source_url": sources[0].url, "quote": quote}],
        }
    ]
    first = build_context(
        profile, entries, sources, CampaignBrief.model_validate(data["brief"]), [], 30000
    )
    item = next(i for i in first["items"] if i["path"] == "/brand_identity/description")
    assert item["kind"] == "VERIFIED_SOURCE_OBSERVATION"
    entries[0]["citations"][0]["quote"] = "This quote does not exist in the source"
    second = build_context(
        profile, entries, sources, CampaignBrief.model_validate(data["brief"]), [], 30000
    )
    assert (
        next(i for i in second["items"] if i["path"] == item["path"])["kind"]
        == "EXTRACTED_PROVISIONAL"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("primary_hook", ""),
        ("psychological_driver", "ATTENTION"),
        ("alternative_hooks", ["One"]),
        ("production_format", "AVATAR"),
        ("core_insight", "x" * 701),
    ],
)
def test_candidate_schema_rejects_missing_oversize_or_unsupported_fields(field, value):
    c = generated(research("opal")).candidates[0].model_dump()
    c[field] = value
    with pytest.raises(ValidationError):
        Candidate.model_validate(c)


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("audience", "Everyone", "AUDIENCE_MISMATCH"),
        (
            "product_capability",
            "Clinically proven to cure everything",
            "UNSUPPORTED_PRODUCT_CONNECTION",
        ),
        ("primary_hook", "Guaranteed results", "UNSUPPORTED_HEALTH_OR_SAFETY_ASSURANCE"),
        ("research_reference_ids", ["made-up"], "UNKNOWN_RESEARCH_REFERENCE"),
    ],
)
def test_deterministic_blocking_checks(field, value, code):
    context = research("opal")
    c = generated(context).candidates[0].model_copy(update={field: value})
    validation = check_candidate(c, context)
    assert not validation["eligible"] and code in {f["code"] for f in validation["flags"]}


def test_literal_exclusions_and_avoided_terms_are_enforced():
    context = research("opal")
    c = generated(context).candidates[0]
    context["brief"]["creative_exclusions"] = ["interrupted paragraph"]
    assert not check_candidate(c, context)["eligible"]
    context["brief"]["creative_exclusions"] = []
    context["restrictions"]["prohibited_terms"] = ["interrupted paragraph"]
    assert not check_candidate(c, context)["eligible"]


def test_numeric_and_testimonial_claims_remain_reviewable_not_auto_approved():
    context = research("opal")
    c = (
        generated(context)
        .candidates[0]
        .model_copy(update={"primary_hook": "Save 20% of your time?"})
    )
    result = check_candidate(c, context)
    assert result["eligible"] and result["requires_human_review"]
    assert not result["advertising_claims_approved"]
    assert "SENSITIVE_OR_QUANTIFIED_CLAIM" in {f["code"] for f in result["flags"]}


def test_negative_review_cannot_be_used_as_product_capability_or_positive_testimonial():
    context = research("minimalist")
    c = generated(context, "minimalist").candidates[0]
    negative = next(i for i in context["items"] if i.get("sentiment") == "NEGATIVE_OR_MIXED")
    forged = c.model_copy(
        update={
            "product_capability": negative["text"],
            "capability_reference_ids": [negative["id"]],
        }
    )
    assert not check_candidate(forged, context)["eligible"]
    story = c.story.model_copy(update={"development": negative["text"]})
    copied = c.model_copy(update={"story": story})
    assert not check_candidate(copied, context)["eligible"]


def test_asset_availability_is_not_assumed_from_model_text():
    context = research("opal")
    data = generated(context).candidates[0].model_dump()
    data["asset_requirements"] = [
        {
            "kind": "APP_SCREENSHOT",
            "description": "An official app screenshot",
            "requires_creation": False,
            "research_reference_ids": [],
        }
    ]
    assert not check_candidate(Candidate.model_validate(data), context)["eligible"]
    data["asset_requirements"][0]["requires_creation"] = True
    assert check_candidate(Candidate.model_validate(data), context)["eligible"]


def test_hook_duplicates_and_repeated_insights_do_not_create_extra_concepts():
    context = research("opal")
    result = generated(context)
    result.candidates[1].primary_hook = "THE Interrupted Paragraph!!!"
    result.candidates[2].core_insight = result.candidates[0].core_insight
    result.candidates[2].problem_or_aspiration = result.candidates[0].problem_or_aspiration
    result.candidates[2].strategic_mechanism = result.candidates[0].strategic_mechanism
    accepted, d = validate_candidates(result, context)
    assert len(accepted) == 4 and d["rejected"] == 2 and d["below_candidate_target"]


@pytest.mark.parametrize("mode", ["unknown", "duplicate", "too_many", "missing"])
def test_evaluator_cannot_resurrect_rejected_candidates_or_force_more_than_five(mode):
    candidates = generated(research("opal")).candidates
    result = evaluated(candidates)
    if mode == "unknown":
        result.evaluations[0].candidate_key = "unknown"
    if mode == "duplicate":
        result.evaluations[1].candidate_key = result.evaluations[0].candidate_key
    if mode == "too_many":
        result.evaluations[-1].selected = True
    if mode == "missing":
        result.evaluations.pop()
    with pytest.raises(DomainError):
        validate_evaluation(result, candidates)


def test_scope_and_budget_do_not_silently_change_customer_preferences():
    context = research("minimalist")
    assert context["brief"]["objective"] == "Awareness" and context["brief"]["language"] is None
    data = fixture("minimalist")
    selected = next(o for o in data["profile"]["offerings"] if o["catalog_level"] == "PRODUCT")
    brief = CampaignBrief(
        objective="Awareness",
        audience="Students",
        promotion_scope="PRODUCT",
        offering_key=selected["key"],
    )
    focused = research("minimalist", brief=brief)
    paths = [i["path"] for i in focused["items"] if i["path"].startswith("/offerings/")]
    index = data["profile"]["offerings"].index(selected)
    assert paths and all(p.startswith(f"/offerings/{index}/") for p in paths)
    brief.creative_references = ["x" * 2000] * 5
    with pytest.raises(DomainError):
        research("minimalist", max_chars=4000, brief=brief)


async def test_provider_two_call_budget_and_no_retry_even_when_default_allows_more(settings):
    settings.llm_max_attempts = 5
    transport = MockCreativeTransport(settings)
    adapter = CreativeProvider(transport, settings, "mock-selected")
    context = research("opal")
    result = await adapter.generate(context)
    await adapter.evaluate(
        context, [{"candidate": c.model_dump(), "validation": {}} for c in result.candidates]
    )
    with pytest.raises(DomainError):
        await adapter.generate(context)
    assert len(transport.calls) == 2
    assert all(c["retry_limit"] == 1 and c["model"] == "mock-selected" for c in transport.calls)
    assert len(adapter.usage_records) == 2
    assert transport.settings.llm_max_attempts == 5


async def test_oversized_input_makes_zero_inference_requests(settings):
    transport = MockCreativeTransport(settings)
    adapter = CreativeProvider(transport, settings, "mock")
    with pytest.raises(DomainError):
        await adapter.generate({"text": "x" * 100000})
    assert not transport.calls


def test_same_insight_cannot_be_disguised_by_a_different_mechanism_label():
    context = research("opal")
    result = generated(context)
    result.candidates[1].core_insight = result.candidates[0].core_insight
    accepted, d = validate_candidates(result, context)
    assert len(accepted) == 5 and d["rejections"][0]["reasons"] == ["REPEATED_CONCEPT"]


def test_creative_deadlines_are_independent_of_factual_marketing_defaults(settings):
    assert settings.stage_timeout("creative_generator") == 120
    assert settings.stage_timeout("creative_evaluator") == 60
    settings.marketing_timeout_seconds = 7
    assert settings.stage_timeout("creative_evaluator") == 60


def test_impossible_production_is_blocked_before_evaluation():
    context = research("opal")
    c = generated(context).candidates[0]
    c.b_roll = ["A photorealistic avatar with a celebrity endorsement"]
    assert not check_candidate(c, context)["eligible"]


async def test_shutdown_does_not_claim_a_second_paid_job(monkeypatch, settings):
    import asyncio
    import signal
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from app.worker import serve

    handlers = {}
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "add_signal_handler", lambda sig, callback: handlers.setdefault(sig, callback)
    )

    async def finish_current():
        handlers[signal.SIGTERM]()
        return True

    brand = SimpleNamespace(
        process_once=AsyncMock(), provider=SimpleNamespace(name="mock", model="mock")
    )
    creative = SimpleNamespace(process_once=AsyncMock(side_effect=finish_current))
    monkeypatch.setattr("app.worker.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.worker.session_factory", lambda _: SimpleNamespace(kw={"bind": Mock()})
    )
    monkeypatch.setattr("app.worker.build_provider", lambda _: Mock())
    monkeypatch.setattr("app.worker.ExtractionWorker", lambda *args: brand)
    monkeypatch.setattr("app.creative.worker.CreativeWorker", lambda *args: creative)
    await serve()
    creative.process_once.assert_awaited_once()
    brand.process_once.assert_not_awaited()


def test_only_oversized_capability_fails_before_paying_for_generation():
    from app.creative.research import build_context

    data = fixture("opal")
    profile = BIP.model_validate(
        {
            "brand_identity": {
                "name": "Example",
                "website_url": "https://example.com/",
                "description": "x" * 701,
            }
        }
    )
    with pytest.raises(DomainError) as exc:
        build_context(
            profile, [], docs(data), CampaignBrief.model_validate(data["brief"]), [], 30000
        )
    assert exc.value.code == "RESEARCH_INSUFFICIENT"


def test_destination_preserves_tracking_parameters_and_fragment_without_fetching():
    url = "https://opalapp.com/pricing?utm_source=instagram&variant=students#plans"
    brief = CampaignBrief(objective="Awareness", audience="Students", destination_url=url)
    assert brief.destination_url == url
    with pytest.raises(ValidationError):
        CampaignBrief(
            objective="Awareness", audience="Students", destination_url="javascript:alert(1)"
        )


@pytest.mark.parametrize("brand", ["opal", "minimalist"])
def test_research_selects_real_passages_beyond_the_prefix_and_filters_metadata(brand):
    fixture_data = fixture(brand)
    sources = {doc.url: doc for doc in docs(fixture_data)}
    context = build_context(
        BIP.model_validate(fixture_data["profile"]),
        fixture_data["provenance"],
        list(sources.values()),
        CampaignBrief.model_validate(fixture_data["brief"]),
        [{"description": "Students want a routine that fits between classes."}],
        30000,
    )
    passages = [item for item in context["items"] if item["kind"] == "SAVED_SOURCE_PASSAGE"]
    coverage = context["coverage"]
    assert coverage["passages_selected"] == len(passages) > 0
    assert coverage["passages_omitted"] > 0 and coverage["concerns_selected"] == 1
    assert coverage["non_passage_lines_or_chunks_filtered"] > 0
    assert len({item["text"].casefold() for item in passages}) == len(passages)
    assert any(sources[item["source_url"]].content.find(item["text"]) > 900 for item in passages)
    for item in passages:
        doc = sources[item["source_url"]]
        assert item["text"] in doc.content and item["content_hash"] == doc.content_hash
        assert "https://" not in item["text"] and "Logo URL" not in item["text"]
    assert len(json.dumps(context, ensure_ascii=False)) <= 30000


def test_product_and_family_scope_are_explicit_in_capability_references():
    product_key = "vitamin-c-10-face-serum"
    product = research(
        "minimalist",
        brief=CampaignBrief(
            objective="Awareness",
            audience="Students",
            promotion_scope="PRODUCT",
            offering_key=product_key,
        ),
    )
    capabilities = [item for item in product["items"] if item["capability"]]
    assert capabilities and all(item["offering_key"] == product_key for item in capabilities)
    assert all(item["family_key"] == "vitamin-c" for item in capabilities)

    family = research(
        "minimalist",
        brief=CampaignBrief(
            objective="Awareness",
            audience="Students",
            promotion_scope="FAMILY",
            offering_key="vitamin-c",
        ),
    )
    assert {i["family_key"] for i in family["items"] if i["capability"]} == {"vitamin-c"}
    sibling = next(
        i
        for i in family["items"]
        if i["capability"] and i["offering_key"] == "vitamin-c-e-ferulic-16-face-serum"
    )
    candidate = (
        generated(product, "minimalist")
        .candidates[0]
        .model_copy(
            update={
                "product_capability": sibling["text"],
                "capability_reference_ids": [sibling["id"]],
            }
        )
    )
    product["items"].append(sibling)
    validation = check_candidate(candidate, product)
    assert not validation["eligible"]
    assert any(f["code"] == "PRODUCT_SCOPE_MISMATCH" for f in validation["flags"])


def test_caption_contract_supports_two_treatments_and_requires_order_for_new_ideas():
    from app.creative.schemas import GeneratorResult

    data = generated(research("opal")).model_dump()
    assert data["candidates"][0]["on_screen_copy"]["treatment"] == "SINGLE_CAPTION"
    beats = ["HOOK", "DEVELOPMENT", "PRODUCT_CONNECTION", "PAYOFF"]
    copy = {
        "treatment": "SEQUENTIAL_CAPTIONS",
        "captions": [
            {"beat": beat, "text": text}
            for beat, text in zip(
                beats,
                [
                    "Another study break?",
                    "Block the distracting apps.",
                    "Start a focus session with Opal.",
                    "Get back to your notes.",
                ],
                strict=True,
            )
        ],
    }
    data["candidates"][0]["on_screen_copy"] = copy
    assert GeneratorResult.model_validate(data).candidates[0].on_screen_copy.treatment == (
        "SEQUENTIAL_CAPTIONS"
    )
    copy["captions"].reverse()
    with pytest.raises(ValidationError):
        GeneratorResult.model_validate(data)
    data["candidates"][0].pop("on_screen_copy")
    assert Candidate.model_validate(data["candidates"][0]).on_screen_copy is None
    with pytest.raises(ValidationError):
        GeneratorResult.model_validate(data)


@pytest.mark.parametrize(
    "claim",
    [
        "Safe during pregnancy.",
        "Suitable for breastfeeding mothers.",
        "Harmless for babies and children.",
        "A risk-free choice for toddlers.",
    ],
)
def test_safety_assurances_in_actual_caption_are_blocked(claim):
    context = research("minimalist")
    data = generated(context, "minimalist").candidates[0].model_dump()
    data["on_screen_copy"] = {"treatment": "SINGLE_CAPTION", "text": claim}
    result = check_candidate(Candidate.model_validate(data), context)
    assert not result["eligible"]
    assert any(
        f["field"] == "on_screen_copy" and f["code"] == "UNSUPPORTED_SAFETY_ASSURANCE"
        for f in result["flags"]
    )


def test_numeric_review_ignores_production_timing_and_keeps_viewer_claims():
    context = research("opal")
    candidate = generated(context).candidates[0].model_dump()
    candidate["candidate_key"] = "idea-1"
    candidate["story"] = {
        "opening": "0–3s: A notebook closes.",
        "development": "[00:03–00:06] A phone lights up.",
        "product_connection": "6s - Start a focus session.",
        "payoff": "11–15 seconds: Make space to study.",
    }
    candidate["b_roll"] = ["Hold the overhead shot for 3 seconds."]
    result = check_candidate(Candidate.model_validate(candidate), context)
    assert not any(flag["code"] == "NUMERIC_STATEMENT_REVIEW" for flag in result["flags"])

    candidate["story"]["payoff"] = "11–15s: Save 30 minutes every day."
    candidate["on_screen_copy"]["text"] = "Get back 20 minutes today."
    result = check_candidate(Candidate.model_validate(candidate), context)
    assert {
        flag["field"] for flag in result["flags"] if flag["code"] == "NUMERIC_STATEMENT_REVIEW"
    } == {"payoff", "on_screen_copy"}


def test_evaluator_requires_grounding_distinction_and_overlap_groups():
    from app.creative.schemas import EvaluatorResult

    data = evaluated(generated(research("opal")).candidates).model_dump()
    for field in ("grounding", "distinction"):
        invalid = json.loads(json.dumps(data))
        invalid["evaluations"][0].pop(field)
        with pytest.raises(ValidationError):
            EvaluatorResult.model_validate(invalid)
    data.pop("overlap_groups")
    with pytest.raises(ValidationError):
        EvaluatorResult.model_validate(data)
