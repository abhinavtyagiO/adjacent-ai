"""Check field-level evidence against the exact documents supplied to the model."""

import logging
import re
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
    if path.endswith("/demographics"):
        return False
    if path.endswith("/name") and not any(
        marker in path for marker in ("/personas/", "/segments/")
    ):
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
    if entry.origin == "CLASSIFIED" and (
        not entry.reasoning
        or not (
            path.endswith(("/kind", "/catalog_level", "/scope"))
            or path.startswith(("/business_positioning/", "/brand_voice/"))
        )
    ):
        raise DomainError("LLM_EVIDENCE_INVALID", f"Unsupported classification for {path}")
    citations = []
    for citation in entry.citations:
        document = documents.get(citation.source_url)
        if not document or normalized(citation.quote) not in normalized(document.text):
            raise DomainError("LLM_EVIDENCE_INVALID", f"Unverifiable source citation for {path}")
        citations.append({**citation.model_dump(), "fetched_at": document.fetched_at.isoformat()})
    # Exact names and pricing amounts must actually occur in their supporting quotes.
    exact = (
        path
        in {
            "/brand_identity/name",
            LEGAL_NAME_PATH,
            "/brand_identity/founded_year",
        }
        or bool(re.fullmatch(r"/offerings/\d+/(?:name|pricing_tiers/\d+/name)", path))
        or (path.startswith("/offerings/") and path.endswith("/amount"))
        or (path.startswith("/trust_and_proof/testimonials/") and path.endswith("/quote"))
    )
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
    """Keep verified enrichment and omit unsupported content without blocking a draft.

    The registered URL and any user-entered name are authoritative. All extracted
    enrichment can be absent, including the name until confirmation. Collection indexes
    and provenance are rebuilt together so evidence never moves to the wrong item.
    """
    profile = result.profile.model_copy(deep=True)
    profile.brand_identity.website_url = website_url
    if registered_name:
        profile.brand_identity.name = registered_name
    raw = profile.model_dump()
    values = leaves(raw)
    evidence = {}
    duplicates = set()
    for entry in result.evidence:
        if entry.path in evidence:
            duplicates.add(entry.path)
        evidence[entry.path] = entry
    documents = {doc.url: doc for doc in sources.documents}
    timestamp = now().isoformat()
    verified = {}
    rejected = set()

    def warn(path: str, reason: str):
        label = "Legal name" if path == LEGAL_NAME_PATH else path
        if warnings is not None:
            warnings.append(f"{label}: {reason}")
        logger.warning("optional_field_omitted path=%s", path)

    for path, value in values.items():
        if path == "/brand_identity/website_url" or (
            path == "/brand_identity/name" and registered_name
        ):
            verified[path] = {
                "path": path,
                "origin": "USER",
                "citations": [],
                "reasoning": None,
                "recorded_at": timestamp,
            }
            continue
        try:
            if path in duplicates:
                raise DomainError("LLM_EVIDENCE_INVALID", "Duplicate field evidence")
            verified[path] = checked_evidence(path, value, evidence.get(path), documents, timestamp)
        except DomainError:
            rejected.add(path)
            warn(path, "omitted because its source evidence could not be verified.")

    for path in sorted(set(evidence) - set(values)):
        # Stale/section-level citations are not evidence for other populated leaves.
        if path != LEGAL_NAME_PATH:
            warn(path, "ignored evidence for an empty or unknown field.")

    missing = object()

    def clean(value, old_path="", new_path=""):
        if isinstance(value, dict):
            parts = old_path.split("/")
            if (
                re.fullmatch(r"/offerings/\d+/pricing_tiers/\d+", old_path)
                and old_path + "/name" not in verified
            ):
                warn(old_path, "pricing tier omitted because it has no verified name.")
                return missing, []
            if re.fullmatch(
                r"/(?:trust_and_proof/scoped_claims|offerings/\d+/claims)/\d+", old_path
            ):
                if any(old_path + suffix not in verified for suffix in ("/text", "/scope")):
                    warn(old_path, "claim omitted because its text or scope is unsupported.")
                    return missing, []
            if len(parts) == 3 and parts[1] == "offerings" and old_path + "/name" not in verified:
                warn(old_path, "offering omitted because it has no verified name.")
                return missing, []
            if old_path.startswith("/trust_and_proof/testimonials/") and len(parts) == 4:
                if old_path + "/quote" not in verified:
                    warn(old_path, "testimonial omitted because it has no verified quote.")
                    return missing, []
            if old_path.startswith("/offerings/") and old_path.endswith("/pricing"):
                if any(path.startswith(old_path + "/") for path in rejected):
                    # Avoid displaying a verified amount with an unverified currency/period.
                    from app.schemas import Pricing

                    return Pricing().model_dump(), []
            output, provenance = {}, []
            for key, child in value.items():
                escaped = key.replace("~", "~0").replace("/", "~1")
                cleaned, proof = clean(child, old_path + "/" + escaped, new_path + "/" + escaped)
                output[key] = None if cleaned is missing else cleaned
                provenance.extend(proof)
            return output, provenance
        if isinstance(value, list):
            output, provenance = [], []
            for index, child in enumerate(value):
                cleaned, proof = clean(child, f"{old_path}/{index}", f"{new_path}/{len(output)}")
                if cleaned is missing or (isinstance(cleaned, dict) and not leaves(cleaned)):
                    continue
                output.append(cleaned)
                provenance.extend(proof)
            return output, provenance
        if value is None:
            return None, []
        if old_path not in verified:
            return missing, []
        return value, [{**verified[old_path], "path": new_path}]

    cleaned, provenance = clean(raw)
    return BIP.model_validate(cleaned), provenance


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
