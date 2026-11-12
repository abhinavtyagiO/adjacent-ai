"""Check field-level evidence against the exact documents supplied to the model."""

from typing import Any

from app.enrichment import SourceBundle
from app.errors import DomainError
from app.models import now
from app.schemas import BIP, ExtractionResult


def leaves(value: Any, path: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            escaped = key.replace("~", "~0").replace("/", "~1")
            result.update(leaves(child, f"{path}/{escaped}"))
        return result
    if isinstance(value, list):
        result = {}
        for index, child in enumerate(value):
            result.update(leaves(child, f"{path}/{index}"))
        return result
    return {} if value is None or value == "" else {path: value}


def normalized(text: str) -> str:
    return " ".join(text.split())


def inference_allowed(path: str) -> bool:
    # Identity, proof, pricing, marketing preferences and persona identities are facts only.
    if path.endswith(("/name", "/demographics")):
        return False
    roots = (
        "/brand_voice/",
        "/customer_psychology/",
        "/creative_context/",
        "/customer_personas_and_audience_segments/",
    )
    return path.startswith(roots) or (
        path.startswith("/offerings/")
        and ("/audiences/" in path or "/customer_psychology/" in path)
    )


def validated_profile(
    result: ExtractionResult, sources: SourceBundle, website_url: str, registered_name: str | None
) -> tuple[BIP, list[dict]]:
    """Reject uncited facts, fabricated quotes, unknown URLs and fact-like inferences.

    This checks traceability, not semantic truth: every generated draft still needs review.
    Server-controlled URL and a user-supplied name override model output.
    """
    profile = result.profile.model_copy(deep=True)
    profile.brand_identity.website_url = website_url
    if registered_name:
        profile.brand_identity.name = registered_name
    values = leaves(profile.model_dump())
    evidence = {entry.path: entry for entry in result.evidence}
    if len(evidence) != len(result.evidence):
        raise DomainError("LLM_EVIDENCE_INVALID", "Model returned duplicate evidence paths")
    documents = {doc.url: doc for doc in sources.documents}
    timestamp = now().isoformat()
    output = []
    for path, value in values.items():
        if path == "/brand_identity/website_url" or (
            path == "/brand_identity/name" and registered_name
        ):
            output.append(
                {
                    "path": path,
                    "origin": "USER",
                    "citations": [],
                    "reasoning": None,
                    "recorded_at": timestamp,
                }
            )
            continue
        entry = evidence.get(path)
        if not entry or not entry.citations:
            raise DomainError("LLM_EVIDENCE_INVALID", f"Missing source evidence for {path}")
        if entry.origin == "INFERRED" and (not inference_allowed(path) or not entry.reasoning):
            raise DomainError("LLM_EVIDENCE_INVALID", f"Unsupported inference for {path}")
        citations = []
        for citation in entry.citations:
            document = documents.get(citation.source_url)
            if not document or normalized(citation.quote) not in normalized(document.text):
                raise DomainError(
                    "LLM_EVIDENCE_INVALID", f"Unverifiable source citation for {path}"
                )
            citations.append(
                {**citation.model_dump(), "fetched_at": document.fetched_at.isoformat()}
            )
        # Exact names and pricing amounts must actually occur in their supporting quotes.
        exact = path in {
            "/brand_identity/name",
            "/brand_identity/legal_name",
            "/brand_identity/founded_year",
        } or (path.startswith("/offerings/") and path.endswith(("/name", "/amount")))
        if exact and not any(
            normalized(str(value)).casefold() in normalized(c["quote"]).casefold()
            for c in citations
        ):
            raise DomainError(
                "LLM_EVIDENCE_INVALID", f"Source does not contain the value at {path}"
            )
        output.append({**entry.model_dump(), "citations": citations, "recorded_at": timestamp})
    unknown = set(evidence) - set(values)
    if unknown:
        raise DomainError("LLM_EVIDENCE_INVALID", "Model cited fields absent from the profile")
    return profile, output


def corrected_provenance(old: BIP, new: BIP, existing: list[dict]) -> list[dict]:
    before, after = leaves(old.model_dump()), leaves(new.model_dump())
    evidence = {entry["path"]: entry for entry in existing}
    result = []
    for path, value in after.items():
        if path in before and before[path] == value and path in evidence:
            result.append(evidence[path])
        else:
            result.append(
                {
                    "path": path,
                    "origin": "USER",
                    "citations": [],
                    "reasoning": None,
                    "recorded_at": now().isoformat(),
                }
            )
    return result
