import pytest

from app.provenance import leaves, validated_profile
from app.schemas import BIP, Citation, LLMEvidence


@pytest.mark.parametrize(
    "section,fields",
    [
        (
            "brand_identity",
            {"legal_name": "Unverified Ltd", "founded_year": 2001, "headquarters": "London"},
        ),
        (
            "business_positioning",
            {"industry": "Software", "positioning_statement": "Market leader"},
        ),
        (
            "customer_personas_and_audience_segments",
            {"personas": [{"name": "CEO", "demographics": "35-50"}]},
        ),
        ("customer_psychology", {"motivations": ["Growth"]}),
        ("value_propositions", {"primary": "Grow faster"}),
        (
            "trust_and_proof",
            {
                "testimonials": [{"quote": "Amazing", "attribution": "CEO"}],
                "awards": ["Best product"],
                "metrics": ["10x ROI"],
            },
        ),
        ("brand_voice", {"tone": ["Friendly"]}),
        ("visual_identity", {"colors": [{"hex": "#ffffff"}], "fonts": ["Inter"]}),
        ("creative_context", {"calls_to_action": ["Buy now"]}),
        ("marketing_guardrails", {"approval_requirements": "Legal approval"}),
        ("marketing_defaults", {"budget": "$500", "video_duration_seconds": 30}),
    ],
)
def test_every_enrichment_section_can_be_omitted_without_blocking(
    extraction_result, bundle, section, fields
):
    data = extraction_result.profile.model_dump()
    data[section].update(fields)
    extraction_result.profile = BIP.model_validate(data)
    warnings = []
    profile, provenance = validated_profile(
        extraction_result, bundle, bundle.documents[0].url, None, warnings=warnings
    )
    assert warnings and len(profile.model_dump()) == 12
    assert profile.brand_identity.name == "Acme" and len(profile.offerings) == 2
    assert {p["path"] for p in provenance} == set(leaves(profile.model_dump()))
    assert not (set(leaves({section: fields})) & set(leaves(profile.model_dump())))


def proof(path, bundle, quote="Acme makes collaboration software."):
    return LLMEvidence(
        path=path,
        origin="WEBSITE",
        citations=[Citation(source_url=bundle.documents[0].url, quote=quote)],
    )


def test_testimonial_needs_verified_quote_and_evidence_indexes_follow_retained_items(
    extraction_result, bundle
):
    data = extraction_result.profile.model_dump()
    data["trust_and_proof"]["testimonials"] = [
        {"quote": "Invented endorsement", "attribution": "Acme"},
        {"quote": "Acme makes collaboration software.", "attribution": "Unknown person"},
    ]
    extraction_result.profile = BIP.model_validate(data)
    extraction_result.evidence.extend(
        [
            proof("/trust_and_proof/testimonials/0/attribution", bundle),
            proof("/trust_and_proof/testimonials/1/quote", bundle),
        ]
    )
    profile, provenance = validated_profile(
        extraction_result, bundle, bundle.documents[0].url, None
    )
    assert len(profile.trust_and_proof.testimonials) == 1
    assert profile.trust_and_proof.testimonials[0].attribution is None
    retained = next(p for p in provenance if p["path"] == "/trust_and_proof/testimonials/0/quote")
    assert retained["citations"][0]["quote"] == profile.trust_and_proof.testimonials[0].quote
    assert not any(p["path"].endswith("/attribution") for p in provenance)


def test_cited_but_fabricated_testimonial_is_removed(extraction_result, bundle):
    from app.schemas import Testimonial

    extraction_result.profile.trust_and_proof.testimonials = [Testimonial(quote="Unstated praise")]
    extraction_result.evidence.append(proof("/trust_and_proof/testimonials/0/quote", bundle))
    profile, _ = validated_profile(extraction_result, bundle, bundle.documents[0].url, None)
    assert profile.trust_and_proof.testimonials == []


def test_offering_and_nested_list_provenance_are_reindexed_together(extraction_result, bundle):
    data = extraction_result.profile.model_dump()
    data["offerings"] = [
        {"name": "Invented product"},
        {"name": "Flow", "benefits": ["Unsupported benefit", "Collaboration software"]},
    ]
    extraction_result.profile = BIP.model_validate(data)
    extraction_result.evidence = [
        e for e in extraction_result.evidence if not e.path.startswith("/offerings/")
    ]
    extraction_result.evidence.extend(
        [
            proof("/offerings/1/name", bundle, "Flow is our product."),
            proof("/offerings/1/benefits/1", bundle),
        ]
    )
    profile, provenance = validated_profile(
        extraction_result, bundle, bundle.documents[0].url, None
    )
    assert len(profile.offerings) == 1 and profile.offerings[0].name == "Flow"
    assert profile.offerings[0].benefits == ["Collaboration software"]
    paths = {p["path"] for p in provenance}
    assert "/offerings/0/name" in paths and "/offerings/0/benefits/0" in paths
    assert paths == set(leaves(profile.model_dump()))


def test_partially_unsupported_pricing_is_cleared_as_a_unit(extraction_result, bundle):
    extraction_result.profile.offerings[0].pricing.amount = "10"
    extraction_result.profile.offerings[0].pricing.currency = "USD"
    # Even a supported amount must not be shown with an unsupported currency.
    from app.enrichment import Document

    document = Document("https://acme.example/pricing", "Price 10", bundle.documents[0].fetched_at)
    bundle.documents.append(document)
    extraction_result.evidence.append(
        LLMEvidence(
            path="/offerings/0/pricing/amount",
            origin="WEBSITE",
            citations=[Citation(source_url=document.url, quote=document.text)],
        )
    )
    profile, provenance = validated_profile(
        extraction_result, bundle, bundle.documents[0].url, None
    )
    assert (
        profile.offerings[0].pricing.amount is None
        and profile.offerings[0].pricing.currency is None
    )
    assert not any(p["path"].startswith("/offerings/0/pricing/") for p in provenance)


def test_registered_name_and_url_are_preserved_without_model_evidence(extraction_result, bundle):
    extraction_result.evidence = []
    profile, provenance = validated_profile(
        extraction_result, bundle, bundle.documents[0].url, "User Company"
    )
    assert profile.brand_identity.name == "User Company"
    assert {p["origin"] for p in provenance} == {"USER"}
