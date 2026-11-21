"""Quality outcomes, counts and readiness checks using fixtures/mocks only."""

import json
from unittest.mock import AsyncMock

import pytest

from app.diagnostics import EvidenceAudit
from app.intelligence import (
    creative_readiness,
    factual_consistency,
    literal_catalog_evidence,
    run_pipeline,
)
from app.provenance import leaves, validated_profile
from app.schemas import BIP, ExtractionResult, MarketingResult
from evaluations.phase1b import ROOT, load_bundle
from tests.conftest import FakeProvider
from tests.test_intelligence import case, proof


def marketing_report(result):
    return result.metadata["evidence_validation"]["marketing"]


def assert_balanced(report):
    assert report["generated"] == report["accepted"] + report["rejected"]
    assert sum(s["generated"] for s in report["by_section"].values()) == report["generated"]
    assert sum(report["rejection_reasons"].values()) == report["rejected"]
    for section in report["by_section"].values():
        assert section["generated"] == section["accepted"] + section["rejected"]
        assert sum(section["rejection_reasons"].values()) == section["rejected"]


@pytest.mark.parametrize(
    "accepted,total,status",
    [
        (0, 0, "INSUFFICIENT_EVIDENCE"),
        (0, 3, "INSUFFICIENT_EVIDENCE"),
        (1, 3, "INSUFFICIENT_EVIDENCE"),
        (1, 2, "SUCCEEDED"),
        (3, 3, "SUCCEEDED"),
    ],
)
async def test_stage_outcome_uses_retained_insights_not_json_success(
    settings, bundle, extraction_result, accepted, total, status
):
    provider = FakeProvider(extraction_result)
    values = [f"Tentative angle {i}" for i in range(total)]
    provider.infer_marketing = AsyncMock(
        return_value=MarketingResult(
            creative_context={"creative_angles": values},
            evidence=[
                proof(
                    f"/creative_context/creative_angles/{i}",
                    bundle.documents[0].url,
                    "Acme makes collaboration software.",
                    "INFERRED",
                )
                for i in range(accepted)
            ],
        )
    )
    result = await run_pipeline(provider, settings, bundle.documents[0].url, bundle)
    assert result.metadata["stages"] == {"facts": "SUCCEEDED", "marketing": status}
    report = marketing_report(result)
    assert (report["generated"], report["accepted"], report["rejected"]) == (
        total,
        accepted,
        total - accepted,
    )
    assert len(result.profile.creative_context.creative_angles) == accepted
    assert result.profile.brand_identity.name == "Acme"
    assert result.profile.offerings  # Quality gaps never discard validated facts.
    assert_balanced(report)


async def test_rejection_reasons_and_logs_contain_no_sensitive_values(
    settings, bundle, extraction_result, caplog
):
    secret = "PRIVATE_UNSUPPORTED_MODEL_TEXT"
    provider = FakeProvider(extraction_result)
    provider.infer_marketing = AsyncMock(
        return_value=MarketingResult(
            creative_context={"creative_angles": [secret] * 5},
            evidence=[
                proof(
                    "/creative_context/creative_angles/0",
                    bundle.documents[0].url,
                    "invented quote",
                    "INFERRED",
                ),
                proof(
                    "/creative_context/creative_angles/1",
                    "https://unknown.example/",
                    "secret quote",
                    "INFERRED",
                ),
                proof(
                    "/creative_context/creative_angles/2",
                    bundle.documents[0].url,
                    "Acme makes collaboration software.",
                    "CLASSIFIED",
                ),
                proof(
                    "/creative_context/creative_angles/3",
                    bundle.documents[0].url,
                    "Acme makes collaboration software.",
                    "INFERRED",
                ).model_copy(update={"reasoning": None}),
                proof(secret, bundle.documents[0].url, "secret quote", "INFERRED"),
            ],
        )
    )
    result = await run_pipeline(provider, settings, bundle.documents[0].url, bundle)
    report = marketing_report(result)
    assert report["rejection_reasons"] == {
        "QUOTE_NOT_FOUND": 1,
        "UNKNOWN_SOURCE": 1,
        "CLASSIFICATION_NOT_ALLOWED": 1,
        "MISSING_RATIONALE": 1,
        "MISSING_EVIDENCE": 1,
    }
    assert report["ignored_evidence_entries"] == 1
    assert secret not in json.dumps(report)
    assert secret not in caplog.text and "invented quote" not in caplog.text
    assert "secret quote" not in caplog.text
    assert_balanced(report)


@pytest.mark.parametrize(
    "key,reason",
    [("missing", "UNKNOWN_OFFERING_REFERENCE"), ("flow", "DUPLICATE_OFFERING_REFERENCE")],
)
async def test_unusable_offering_references_are_counted(
    settings, bundle, extraction_result, key, reason
):
    insights = [
        {"offering_key": key, "audiences": {"segments": [{"description": "Tentative segment"}]}}
    ]
    if key == "flow":
        insights.insert(0, {"offering_key": "flow"})
    provider = FakeProvider(extraction_result)
    provider.infer_marketing = AsyncMock(return_value=MarketingResult(offering_insights=insights))
    result = await run_pipeline(provider, settings, bundle.documents[0].url, bundle)
    report = marketing_report(result)
    assert report["generated"] == 1  # Selectors and factual name anchors do not count as insights.
    assert report["rejection_reasons"] == {reason: 1}
    assert report["by_section"]["offerings"]["rejected"] == 1
    assert result.metadata["stages"]["marketing"] == "INSUFFICIENT_EVIDENCE"
    assert_balanced(report)


def test_structural_pruning_counts_children_and_keeps_original_indexes(bundle):
    url = bundle.documents[0].url
    result = ExtractionResult(
        profile=BIP(
            brand_identity={"website_url": url, "name": "Acme"},
            offerings=[{"name": "Unstated", "features": ["Supported feature"]}, {"name": "Flow"}],
        ),
        evidence=[
            proof("/brand_identity/name", url, "Acme makes collaboration software."),
            proof("/offerings/0/name", url, "Flow is our product."),
            proof("/offerings/0/features/0", url, "Flow is our product."),
            proof("/offerings/1/name", url, "Flow is our product."),
        ],
    )
    audit = EvidenceAudit(
        p for p in leaves(result.profile.model_dump()) if not p.endswith("/website_url")
    )
    profile, provenance = validated_profile(result, bundle, url, None, audit=audit)
    report = audit.report()
    assert report["generated"] == 4 and report["accepted"] == 2
    assert report["rejection_reasons"] == {
        "EXACT_VALUE_MISMATCH": 1,
        "CONTAINER_NAME_UNVERIFIED": 1,
    }
    assert profile.offerings[0].name == "Flow"
    assert any(p["path"] == "/offerings/0/name" for p in provenance)
    assert_balanced(report)


def test_plan_relocation_counts_original_fields_once():
    url, text, bundle = case("fastlane")
    result = ExtractionResult(
        profile=BIP(
            brand_identity={"website_url": url},
            offerings=[{"name": "Fastlane"}, {"name": "Starter", "pricing": {"amount": "$29"}}],
        ),
        evidence=[
            proof("/offerings/0/name", url, text),
            proof("/offerings/1/name", url, text),
            proof("/offerings/1/pricing/amount", url, text),
        ],
    )
    audit = EvidenceAudit(
        p for p in leaves(result.profile.model_dump()) if not p.endswith("/website_url")
    )
    result = factual_consistency(literal_catalog_evidence(result, bundle), bundle, [], audit)
    profile, _ = validated_profile(result, bundle, url, None, audit=audit)
    assert profile.offerings[0].pricing_tiers[0].name == "Starter"
    assert (audit.report()["generated"], audit.report()["accepted"]) == (3, 3)
    assert_balanced(audit.report())


def test_numeric_semantic_failure_keeps_specific_reason():
    url, text, bundle = case("whole_truth")
    result = ExtractionResult(
        profile=BIP(
            brand_identity={"website_url": url}, trust_and_proof={"metrics": ["1,000,000 readers"]}
        ),
        evidence=[proof("/trust_and_proof/metrics/0", url, text)],
    )
    audit = EvidenceAudit(["/trust_and_proof/metrics/0"])
    result = factual_consistency(result, bundle, [], audit)
    validated_profile(result, bundle, url, None, audit=audit)
    assert audit.report()["rejection_reasons"] == {"NUMERIC_CLAIM_MISMATCH": 1}


def complete_profile():
    return BIP(
        brand_identity={
            "name": "Acme",
            "website_url": "https://acme.example/",
            "description": "Team software",
        },
        brand_voice={"tone": ["Clear"]},
        creative_context={"content_pillars": ["Team collaboration"]},
        offerings=[
            {
                "name": "Flow",
                "key": "flow",
                "benefits": ["Easier handoffs"],
                "audiences": {"segments": [{"description": "Teams coordinating projects"}]},
                "customer_psychology": {"motivations": ["Keep projects moving"]},
                "claims": [{"text": "Supports shared task boards", "scope": "OFFERING"}],
            }
        ],
    )


def claim_proof(origin="WEBSITE", confirmed=False):
    return [
        {
            "path": "/offerings/0/claims/0/" + field,
            "origin": origin,
            "citations": [{"source_url": "https://acme.example/", "quote": "Source claim"}],
            "reasoning": "Context supports the label",
            "confirmed_by_user": confirmed,
        }
        for field in ["text", "scope"]
    ]


def test_selected_offering_needs_all_four_checks_not_brand_level_substitutes():
    profile = complete_profile()
    profile.customer_personas_and_audience_segments = profile.offerings[0].audiences.model_copy(
        deep=True
    )
    profile.customer_psychology = profile.offerings[0].customer_psychology.model_copy(deep=True)
    selected = profile.offerings[0]
    selected.benefits = []
    selected.audiences.segments = []
    selected.customer_psychology.motivations = []
    selected.claims = []
    assessment = creative_readiness(profile, "flow", claim_proof())
    assert assessment["status"] == "NEEDS_REVIEW"
    assert assessment["offering_assessment"] == dict.fromkeys(
        ["benefits", "target_audience", "motivations", "supported_marketing_claims"], False
    )
    assert (
        len([m for m in assessment["missing_information"] if m["field"].startswith("offerings.")])
        == 4
    )


@pytest.mark.parametrize(
    "origin,confirmed,ready",
    [
        ("WEBSITE", False, True),
        ("USER", False, False),
        ("USER", True, True),
        ("INFERRED", True, False),
    ],
)
def test_claim_support_requires_source_or_explicit_user_confirmation(origin, confirmed, ready):
    result = creative_readiness(complete_profile(), "flow", claim_proof(origin, confirmed))
    assert (result["status"] == "READY_FOR_CREATIVE_BRIEF") == ready
    assert result["offering_assessment"]["supported_marketing_claims"] == ready


@pytest.mark.parametrize(
    "scope,subject",
    [
        ("FACILITY", None),
        ("NEWSLETTER", None),
        ("BRAND", None),
        ("CUSTOMER_ANECDOTE", None),
        ("OFFERING", "other-product"),
    ],
)
def test_unrelated_claim_scope_does_not_make_offering_ready(scope, subject):
    profile = complete_profile()
    profile.offerings[0].claims[0].scope = scope
    profile.offerings[0].claims[0].subject = subject
    assert not creative_readiness(profile, "flow", claim_proof())["offering_assessment"][
        "supported_marketing_claims"
    ]


def test_classified_scope_is_allowed_but_not_classified_claim_text():
    proof_entries = claim_proof()
    proof_entries[1]["origin"] = "CLASSIFIED"
    assert (
        creative_readiness(complete_profile(), "flow", proof_entries)["status"]
        == "READY_FOR_CREATIVE_BRIEF"
    )
    proof_entries[0]["origin"] = "CLASSIFIED"
    assert creative_readiness(complete_profile(), "flow", proof_entries)["status"] == "NEEDS_REVIEW"


def test_legacy_null_keys_never_count_as_explicit_selection():
    profile = complete_profile()
    profile.offerings[0].key = None
    result = creative_readiness(profile)
    assert result["scope"] == "BRAND" and result["selected_offering_key"] is None
    assert result["offering_assessment"] is None
    unknown = creative_readiness(profile, "not-found")
    assert unknown["requested_offering_key"] == "not-found"
    assert any("not found" in m["reason"] for m in unknown["missing_information"])


@pytest.mark.parametrize("brand", ["fastlane", "whole_truth", "newton"])
async def test_saved_brand_results_are_checked_without_paid_inference(settings, brand):
    bundle = load_bundle(ROOT / "evaluations/snapshots" / (brand + ".json"))
    facts = ExtractionResult.model_validate_json(
        (ROOT / "evaluations/results" / (brand + "-none-facts.json")).read_text()
    )
    saved = json.loads((ROOT / "evaluations/results" / (brand + "-none-profile.json")).read_text())
    roots = [
        "customer_personas_and_audience_segments",
        "customer_psychology",
        "brand_voice",
        "creative_context",
    ]
    entries = []
    for p in saved["provenance"]:
        if p["path"].split("/")[1] in roots:
            entries.append(
                {
                    **{k: p[k] for k in ["path", "origin", "reasoning"]},
                    "citations": [
                        {k: c[k] for k in ["source_url", "quote"]} for c in p["citations"]
                    ],
                }
            )
    provider = FakeProvider(facts)
    provider.infer_marketing = AsyncMock(
        return_value=MarketingResult(**{k: saved["profile"][k] for k in roots}, evidence=entries)
    )
    result = await run_pipeline(provider, settings, bundle.documents[0].url, bundle)
    report = marketing_report(result)
    assert_balanced(result.metadata["evidence_validation"]["facts"])
    assert_balanced(report)
    if brand == "newton":
        assert result.metadata["stages"]["marketing"] == "INSUFFICIENT_EVIDENCE"
        assert report["generated"] == report["accepted"] == 0
        assert len(result.profile.offerings) == 7
    else:
        assert result.metadata["stages"]["marketing"] == "SUCCEEDED"
        assert report["accepted"] > 0
    assert result.metadata["creative_readiness"]["scope"] == "BRAND"


async def test_optional_marketing_schema_failure_preserves_facts(
    settings, bundle, extraction_result
):
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as invalid:
        MarketingResult.model_validate({"brand_voice": "PRIVATE_INVALID_RESPONSE"})
    provider = FakeProvider(extraction_result)
    provider.infer_marketing = AsyncMock(side_effect=invalid.value)
    result = await run_pipeline(provider, settings, bundle.documents[0].url, bundle)
    assert result.metadata["stages"] == {"facts": "SUCCEEDED", "marketing": "FAILED"}
    assert not marketing_report(result)["available"]
    assert result.profile.brand_identity.name == "Acme" and result.profile.offerings
    assert "PRIVATE_INVALID_RESPONSE" not in json.dumps(result.metadata)
    assert "PRIVATE_INVALID_RESPONSE" not in json.dumps(result.warnings)


def test_persona_name_alone_is_not_sufficient_product_targeting():
    profile = complete_profile()
    data = profile.model_dump()
    data["offerings"][0]["audiences"] = {"personas": [{"name": "Everyone"}]}
    result = creative_readiness(BIP.model_validate(data), "flow", claim_proof())
    assert not result["offering_assessment"]["target_audience"]


def test_global_offering_claim_must_match_selected_subject_and_support_it():
    profile = complete_profile()
    claim = profile.offerings[0].claims.pop()
    claim.subject = "flow"
    profile.trust_and_proof.scoped_claims.append(claim)
    entries = [
        {**p, "path": p["path"].replace("/offerings/0/claims", "/trust_and_proof/scoped_claims")}
        for p in claim_proof()
    ]
    entries.append({**entries[0], "path": "/trust_and_proof/scoped_claims/0/subject"})
    assert creative_readiness(profile, "flow", entries)["offering_assessment"][
        "supported_marketing_claims"
    ]
    claim.subject = "another-offering"
    assert not creative_readiness(profile, "flow", entries)["offering_assessment"][
        "supported_marketing_claims"
    ]


async def test_duplicate_names_keep_targeting_and_diagnostics_on_the_selected_key(
    settings, bundle, extraction_result
):
    extraction_result.profile.offerings[1].name = "Flow"
    extraction_result.evidence[-1] = proof(
        "/offerings/1/name", bundle.documents[0].url, "Flow is our product."
    )
    provider = FakeProvider(extraction_result)
    provider.infer_marketing = AsyncMock(
        return_value=MarketingResult(
            offering_insights=[
                {
                    "offering_key": "flow-2",
                    "customer_psychology": {"motivations": ["Simplify handoffs"]},
                }
            ],
            evidence=[
                proof(
                    "/offering_insights/0/customer_psychology/motivations/0",
                    bundle.documents[0].url,
                    "Flow is our product.",
                    "INFERRED",
                )
            ],
        )
    )
    result = await run_pipeline(provider, settings, bundle.documents[0].url, bundle)
    assert result.profile.offerings[0].customer_psychology.motivations == []
    assert result.profile.offerings[1].customer_psychology.motivations == ["Simplify handoffs"]
    assert marketing_report(result)["accepted"] == 1
    assert any(
        p["path"] == "/offerings/1/customer_psychology/motivations/0" for p in result.provenance
    )
