"""Check field-level evidence against the exact documents supplied to the model."""

import logging
from typing import Any

from app.enrichment import Document, SourceBundle
from app.errors import DomainError
from app.models import now
from app.schemas import BIP, ExtractionResult, LLMEvidence

logger = logging.getLogger(__name__)
LEGAL_NAME_PATH = "/brand_identity/legal_name"


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


def checked_evidence(
    path: str,
    value: Any,
    entry: LLMEvidence | None,
    documents: dict[str, Document],
    timestamp: str,
) -> dict:
    """Validate one populated fact without deciding whether its failure is blocking."""
    if not entry or not entry.citations:
        raise DomainError("LLM_EVIDENCE_INVALID", f"Missing source evidence for {path}")
    if entry.origin == "INFERRED" and (not inference_allowed(path) or not entry.reasoning):
        raise DomainError("LLM_EVIDENCE_INVALID", f"Unsupported inference for {path}")
    citations = []
    for citation in entry.citations:
        document = documents.get(citation.source_url)
        if not document or normalized(citation.quote) not in normalized(document.text):
            raise DomainError("LLM_EVIDENCE_INVALID", f"Unverifiable source citation for {path}")
        citations.append({**citation.model_dump(), "fetched_at": document.fetched_at.isoformat()})
    # Exact names and pricing amounts must actually occur in their supporting quotes.
    exact = path in {
        "/brand_identity/name",
        LEGAL_NAME_PATH,
        "/brand_identity/founded_year",
    } or (path.startswith("/offerings/") and path.endswith(("/name", "/amount")))
    if exact and not any(
        normalized(str(value)).casefold() in normalized(c["quote"]).casefold() for c in citations
    ):
        raise DomainError("LLM_EVIDENCE_INVALID", f"Source does not contain the value at {path}")
    return {**entry.model_dump(), "citations": citations, "recorded_at": timestamp}


def validated_profile(
    result: ExtractionResult,
    sources: SourceBundle,
    website_url: str,
    registered_name: str | None,
    *,
    warnings: list[str] | None = None,
) -> tuple[BIP, list[dict]]:
    """Reject uncited facts, fabricated quotes, unknown URLs and fact-like inferences.

    This checks traceability, not semantic truth: every generated draft still needs review.
    Server-controlled URL and a user-supplied name override model output.
    Unsupported optional legal names are cleared, with warnings collected by the caller.
    All other evidence failures still reject the result.
    """
    profile = result.profile.model_copy(deep=True)
    profile.brand_identity.website_url = website_url
    if registered_name:
        profile.brand_identity.name = registered_name
    values = leaves(profile.model_dump())
    evidence = {}
    duplicate_paths = set()
    for entry in result.evidence:
        if entry.path in evidence:
            duplicate_paths.add(entry.path)
        evidence[entry.path] = entry
    if duplicate_paths - {LEGAL_NAME_PATH}:
        raise DomainError("LLM_EVIDENCE_INVALID", "Model returned duplicate evidence paths")
    if profile.brand_identity.legal_name is None:
        # Evidence for an absent optional legal name cannot invalidate the actual facts.
        evidence.pop(LEGAL_NAME_PATH, None)
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
        try:
            if path == LEGAL_NAME_PATH and path in duplicate_paths:
                raise DomainError("LLM_EVIDENCE_INVALID", "Duplicate legal-name evidence")
            output.append(checked_evidence(path, value, evidence.get(path), documents, timestamp))
        except DomainError as error:
            if path != LEGAL_NAME_PATH:
                raise
            profile.brand_identity.legal_name = None
            if warnings is not None:
                warnings.append(
                    "Legal name was set to null because its source evidence could not be verified."
                )
            logger.warning("optional_field_omitted path=%s code=%s", path, error.code)
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
