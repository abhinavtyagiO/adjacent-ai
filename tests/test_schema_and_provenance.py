import pytest
from pydantic import ValidationError

from app.config import Settings
from app.db import session_factory
from app.provenance import corrected_provenance, leaves, validated_profile
from app.schemas import BIP, Citation, CreateBrand, LLMEvidence
from app.services import merge_profile


def test_minimal_schema_keeps_twelve_sections_and_unknowns_null():
    profile = BIP(brand_identity={"website_url": "https://acme.example"})
    assert len(profile.model_dump()) == 12
    assert profile.brand_identity.name is None
    assert profile.marketing_defaults.budget is None
    assert profile.offerings == []
    assert profile.customer_psychology.pain_points == []


def test_blank_optional_fields_become_null_and_blank_list_items_are_rejected():
    profile = BIP(
        brand_identity={"website_url": "https://acme.example/", "name": "  ", "tagline": ""}
    )
    assert profile.brand_identity.name is None and profile.brand_identity.tagline is None
    with pytest.raises(ValidationError):
        BIP(brand_identity={"website_url": "https://acme.example/", "values": [" "]})


@pytest.mark.parametrize("url", ["sqlite:///test.db", "sqlite:///:memory:", "postgresql://x/y"])
def test_non_postgres_config_rejected(url):
    with pytest.raises(ValidationError):
        Settings(database_url=url, _env_file=None)
    with pytest.raises(ValueError):
        session_factory(url)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://acme.example",
        "http://127.0.0.1",
        "http://[::1]",
        "http://169.254.169.254",
        "http://localhost",
        "http://x.local",
        "https://user:pass@acme.example",
        "https://acme.example:8080",
        "https://acme.example:80",
        "https://acme.example/\nhi",
    ],
)
def test_invalid_or_private_website_rejected(url):
    with pytest.raises(ValidationError):
        CreateBrand(website_url=url)


def test_normalizes_host_and_removes_tracking():
    brand = CreateBrand(website_url="https://ACME.example?utm_source=x#hello")
    assert brand.website_url == "https://acme.example/"


def test_evidence_uses_real_source_and_server_timestamps(extraction_result, bundle):
    profile, evidence = validated_profile(extraction_result, bundle, bundle.documents[0].url, None)
    assert profile.brand_identity.name == "Acme"
    name = next(e for e in evidence if e["path"] == "/brand_identity/name")
    assert name["origin"] == "WEBSITE"
    assert name["citations"][0]["fetched_at"] == bundle.documents[0].fetched_at.isoformat()


@pytest.mark.parametrize(
    "problem",
    [
        "mismatched_name",
        "missing_evidence",
        "missing_citations",
        "unknown_source",
        "invented_quote",
        "inferred_name",
        "duplicate_evidence",
    ],
)
def test_unsupported_legal_name_is_cleared_without_rejecting_draft(
    extraction_result, bundle, problem
):
    extraction_result.profile.brand_identity.legal_name = "Acme Incorporated"
    entry = LLMEvidence(
        path="/brand_identity/legal_name",
        origin="WEBSITE",
        citations=[
            Citation(source_url=bundle.documents[0].url, quote="Acme makes collaboration software.")
        ],
    )
    if problem == "missing_citations":
        entry.citations = []
    elif problem == "unknown_source":
        entry.citations[0].source_url = "https://unread.example/"
    elif problem == "invented_quote":
        entry.citations[0].quote = "Registered company: Acme Incorporated"
    elif problem == "inferred_name":
        entry.origin, entry.reasoning = "INFERRED", "Guessed company suffix"
    if problem != "missing_evidence":
        extraction_result.evidence.append(entry)
    if problem == "duplicate_evidence":
        extraction_result.evidence.append(entry.model_copy(deep=True))
    warnings = []
    profile, provenance = validated_profile(
        extraction_result, bundle, bundle.documents[0].url, None, warnings=warnings
    )
    assert profile.brand_identity.legal_name is None
    assert profile.brand_identity.name == "Acme" and len(profile.offerings) == 2
    assert not any(p["path"] == "/brand_identity/legal_name" for p in provenance)
    assert len(warnings) == 1 and "Legal name" in warnings[0]
    assert extraction_result.profile.brand_identity.legal_name == "Acme Incorporated"


def test_supported_legal_name_is_preserved(extraction_result, bundle):
    from app.enrichment import Document

    document = Document(
        "https://acme.example/terms",
        "Legal entity: Acme Incorporated.",
        bundle.documents[0].fetched_at,
    )
    bundle.documents.append(document)
    extraction_result.profile.brand_identity.legal_name = "Acme Incorporated"
    extraction_result.evidence.append(
        LLMEvidence(
            path="/brand_identity/legal_name",
            origin="WEBSITE",
            citations=[Citation(source_url=document.url, quote=document.text)],
        )
    )
    warnings = []
    profile, provenance = validated_profile(
        extraction_result, bundle, bundle.documents[0].url, None, warnings=warnings
    )
    assert profile.brand_identity.legal_name == "Acme Incorporated" and warnings == []
    assert (
        next(p for p in provenance if p["path"] == "/brand_identity/legal_name")["origin"]
        == "WEBSITE"
    )


def test_absent_legal_name_does_not_require_evidence_or_warn(extraction_result, bundle):
    warnings = []
    profile, _ = validated_profile(
        extraction_result, bundle, bundle.documents[0].url, None, warnings=warnings
    )
    assert profile.brand_identity.legal_name is None and warnings == []


def test_unused_legal_name_evidence_is_ignored(extraction_result, bundle):
    extraction_result.evidence.append(
        LLMEvidence(
            path="/brand_identity/legal_name",
            origin="WEBSITE",
            citations=[],
        )
    )
    profile, provenance = validated_profile(
        extraction_result, bundle, bundle.documents[0].url, None
    )
    assert profile.brand_identity.legal_name is None
    assert not any(p["path"] == "/brand_identity/legal_name" for p in provenance)


@pytest.mark.parametrize(
    "mutation",
    [
        "no_evidence",
        "fake_quote",
        "fake_url",
        "fake_name",
        "duplicate",
        "unknown_path",
        "inferred_name",
    ],
)
def test_untraceable_or_invented_details_are_omitted(extraction_result, bundle, mutation):
    result = extraction_result.model_copy(deep=True)
    if mutation == "no_evidence":
        result.evidence = []
    elif mutation == "fake_quote":
        result.evidence[0].citations[0].quote = "Acme was founded in 1850."
    elif mutation == "fake_url":
        result.evidence[0].citations[0].source_url = "https://unread.example/"
    elif mutation == "fake_name":
        result.profile.brand_identity.name = "Invented Company"
    elif mutation == "duplicate":
        result.evidence.append(result.evidence[0])
    elif mutation == "unknown_path":
        result.evidence.append(LLMEvidence(path="/missing", origin="WEBSITE", citations=[]))
    else:
        result.evidence[0].origin = "INFERRED"
        result.evidence[0].reasoning = "Guessed from domain"
    warnings = []
    profile, provenance = validated_profile(
        result, bundle, bundle.documents[0].url, None, warnings=warnings
    )
    assert warnings
    assert {p["path"] for p in provenance} == set(leaves(profile.model_dump()))
    if mutation in {
        "no_evidence",
        "fake_quote",
        "fake_url",
        "fake_name",
        "duplicate",
        "inferred_name",
    }:
        assert profile.brand_identity.name is None
    if mutation == "no_evidence":
        assert profile.offerings == []


def test_grounded_inference_stays_separate(extraction_result, bundle):
    extraction_result.profile.brand_voice.tone = ["Direct"]
    extraction_result.evidence.append(
        LLMEvidence(
            path="/brand_voice/tone/0",
            origin="INFERRED",
            reasoning="Concise declarative copy",
            citations=[
                Citation(
                    source_url=bundle.documents[0].url, quote="Acme makes collaboration software."
                )
            ],
        )
    )
    _, evidence = validated_profile(extraction_result, bundle, bundle.documents[0].url, None)
    assert next(e for e in evidence if e["path"] == "/brand_voice/tone/0")["origin"] == "INFERRED"


def test_user_correction_replaces_evidence_and_removal_drops_it(extraction_result, bundle):
    old, evidence = validated_profile(extraction_result, bundle, bundle.documents[0].url, None)
    new = old.model_copy(deep=True)
    new.brand_identity.name = "Acme Inc"
    new.offerings = []
    result = corrected_provenance(old, new, evidence)
    assert next(e for e in result if e["path"] == "/brand_identity/name")["origin"] == "USER"
    assert not any(e["path"].startswith("/offerings/") for e in result)


def test_merge_preserves_omitted_fields_but_clears_null_and_arrays():
    assert merge_profile({"a": {"b": 1, "c": 2}, "d": [1]}, {"a": {"b": None}, "d": []}) == {
        "a": {"b": None, "c": 2},
        "d": [],
    }
    assert leaves({"empty": [], "unknown": None, "zero": 0}) == {"/zero": 0}
