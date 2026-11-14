"""Deterministic business consistency checks and two-stage intelligence orchestration."""

import asyncio
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError

from app.crawling.website import CATEGORIES, page_category
from app.diagnostics import EvidenceAudit, unavailable_evidence
from app.enrichment import SourceBundle
from app.errors import DomainError
from app.provenance import leaves, normalized, validated_profile
from app.schemas import BIP, ExtractionResult, LLMEvidence

PLAN_NAMES = {"starter", "growth", "pro", "enterprise", "basic", "premium", "free", "business"}
MARKETING_ROOTS = {
    "customer_personas_and_audience_segments",
    "customer_psychology",
    "brand_voice",
    "creative_context",
}
logger = logging.getLogger(__name__)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "offering"


def quantities(text: str) -> set:
    """Normalize numeric qualifiers, e.g. 1M and 1,000,000."""
    result = set()
    for match in re.finditer(
        r"(?<!\w)(\d[\d,]*(?:\.\d+)?)\s*(million|billion|thousand|[mkb])?\b", text.lower()
    ):
        value = Decimal(match[1].replace(",", ""))
        factor = {
            "m": 1000000,
            "million": 1000000,
            "b": 1000000000,
            "billion": 1000000000,
            "k": 1000,
            "thousand": 1000,
        }.get(match[2], 1)
        result.add(value * factor)
    return result


def detected_scope(text: str) -> str | None:
    text = text.lower()
    if re.search(r"newsletter|subscribers|readers|readership", text):
        return "NEWSLETTER"
    if re.search(r"facility|facilities|manufactur|factory|plant", text) and re.search(
        r"certif|iso|fssai|brc|haccp", text
    ):
        return "FACILITY"
    if re.search(r"\bmy video\b|\bmy channel\b|\bmy account\b", text):
        return "CUSTOMER_ANECDOTE"
    return None


def kind_from_context(text: str, structured_product: bool = False) -> str | None:
    if re.search(r"\bcourse\b|\bprogram\b|\btraining\b|\btutor|\bconsult|\benrolled\b", text, re.I):
        return "SERVICE"
    if structured_product or re.search(
        r"\bprotein|\bchocolat|\bmuesli|\bpeanut|\bnut spread|\bbar\b|ingredients|"
        r"add to cart|\bsoftware\b|"
        r"\bplatform\b|\btool\b|\bapp\b|\bapi\b",
        text,
        re.I,
    ):
        return "PRODUCT"
    return None


def literal_catalog_evidence(result: ExtractionResult, sources: SourceBundle) -> ExtractionResult:
    """Recover literal catalog leaves from grouped citations, never paraphrases.

    Compatible models sometimes cite an offering/list rather than each scalar. A
    grouped quote can support a child only when the child's actual value occurs in
    that quote. Ordinary citation checks still verify the quote against saved text.
    Explicit/duplicate leaf citations are never overridden. Hypotheses and scope
    labels are never expanded by this literal adapter.
    """
    existing = {e.path for e in result.evidence}
    additions = []
    for i, offering in enumerate(result.profile.offerings):
        path = f"/offerings/{i}/name"
        if path in existing:
            continue
        prefix = f"/offerings/{i}"
        if not any(e.path == prefix or e.path.startswith(prefix + "/") for e in result.evidence):
            continue
        # An independently located literal name is a sourced fact, not a guessed name.
        for document in sources.documents:
            text = normalized(document.text)
            match = re.search(
                r"(?<!\w)" + re.escape(normalized(offering.name)) + r"(?!\w)", text, re.I
            )
            if match:
                quote = text[max(0, match.start() - 80) : match.end() + 160]
                additions.append(
                    LLMEvidence(
                        path=path,
                        origin="WEBSITE",
                        citations=[{"source_url": document.url, "quote": quote}],
                    )
                )
                break
    existing.update(e.path for e in additions)
    for path, value in leaves(result.profile.model_dump()).items():
        if (
            not path.startswith("/offerings/")
            or path in existing
            or path.endswith(("/kind", "/catalog_level", "/scope", "/parent_offering_key"))
        ):
            continue
        candidates = [
            e
            for e in result.evidence
            if e.origin == "WEBSITE"
            and path.startswith(e.path + "/")
            and e.path.startswith("/offerings/")
            and any(
                normalized(str(value)).casefold() in normalized(c.quote).casefold()
                for c in e.citations
            )
        ]
        if len(candidates) == 1:
            additions.append(candidates[0].model_copy(update={"path": path}))
    return result.model_copy(update={"evidence": [*result.evidence, *additions]})


def factual_consistency(
    result: ExtractionResult, sources: SourceBundle, warnings: list[str], audit=None
):
    """Reject known scope/classification contradictions before general citation validation.

    This is targeted checking, not a claim of universal semantic entailment. User review
    remains required. Removing a field also removes its evidence so indexes stay coherent.
    """
    data = result.profile.model_dump()
    evidence = {e.path: e for e in result.evidence}
    rejected = set()

    def reject(path, reason):
        rejected.add(path)
        if audit:
            audit.reject(path, reason)

    def support(path):
        entry = evidence.get(path)
        return " ".join(c.quote for c in entry.citations) if entry else ""

    additions = []
    product_targets = [
        i
        for i, o in enumerate(data["offerings"])
        if o["name"].casefold() not in PLAN_NAMES and o["kind"] in {None, "PRODUCT"}
    ]
    for i, offering in enumerate(data["offerings"]):
        prefix = f"/offerings/{i}"
        name_support = support(prefix + "/name")
        if offering["kind"] == "OTHER" and re.search(
            r"newsletter|ingredient.*look.?up|tool to look up", offering["description"] or "", re.I
        ):
            reject(prefix, "NON_MARKETABLE_RESOURCE")
            warnings.append(
                f"{prefix}: editorial/supporting resource omitted from marketable catalog."
            )
        if offering["name"].casefold() in PLAN_NAMES and re.search(
            r"plan|month|tier|credits|pricing|subscription", name_support, re.I
        ):
            if len(product_targets) == 1:
                target = product_targets[0]
                tiers = data["offerings"][target]["pricing_tiers"]
                tier_index = len(tiers)
                tiers.append(
                    {
                        "name": offering["name"],
                        "pricing": offering["pricing"],
                        "features": offering["features"],
                    }
                )
                if audit:
                    for old_path in leaves(
                        {k: offering[k] for k in ("name", "pricing", "features")}, prefix
                    ):
                        audit.alias(
                            old_path,
                            f"/offerings/{target}/pricing_tiers/{tier_index}"
                            + old_path[len(prefix) :],
                        )
                for entry in result.evidence:
                    for field in ("name", "pricing", "features"):
                        original = prefix + "/" + field
                        if entry.path == original or entry.path.startswith(original + "/"):
                            additions.append(
                                entry.model_copy(
                                    update={
                                        "path": f"/offerings/{target}/pricing_tiers/{tier_index}/"
                                        + entry.path[len(prefix) + 1 :]
                                    }
                                )
                            )
            reject(prefix, "SUBSCRIPTION_TIER_RELOCATED_OR_OMITTED")
            warnings.append(
                f"{prefix}: subscription tier omitted from products; "
                "tiers belong under the actual product."
            )
        for field in ("kind", "catalog_level"):
            path = prefix + "/" + field
            if field == "kind" and offering[field] and name_support:
                expected = kind_from_context(name_support + " " + support(path))
                if expected and offering[field] != expected:
                    reject(path, "CONTRADICTORY_CLASSIFICATION")
                    warnings.append(f"{path}: contradictory classification omitted.")
    for path, value in leaves(data).items():
        quote = support(path)
        if (
            (
                "/claims/" in path
                or "/scoped_claims/" in path
                or path.startswith("/trust_and_proof/metrics/")
            )
            and path.endswith("/text")
            or path.startswith("/trust_and_proof/metrics/")
        ):
            if quote and not quantities(str(value)).issubset(quantities(quote)):
                reject(path, "NUMERIC_CLAIM_MISMATCH")
                warnings.append(
                    f"{path}: numeric advertising claim does not match its cited qualifiers."
                )
        if path.startswith("/marketing_defaults/target_channels/"):
            if not re.search(
                r"follow us|our (?:social|marketing|instagram|youtube|linkedin|tiktok)|"
                r"we market|our channels",
                quote,
                re.I,
            ):
                reject(path, "MARKETING_CHANNEL_NOT_EXPLICIT")
                warnings.append(
                    f"{path}: supported platform is not an explicit brand marketing preference."
                )
        if path.endswith("/scope"):
            text_path = path.rsplit("/", 1)[0] + "/text"
            actual = detected_scope(support(text_path) + " " + quote)
            if actual and value != actual:
                reject(path.rsplit("/", 1)[0], "CLAIM_SCOPE_MISMATCH")
                warnings.append(
                    f"{path}: claim scope contradicts the cited "
                    "facility/newsletter/customer context."
                )
        if path.startswith(
            ("/trust_and_proof/certifications/", "/trust_and_proof/metrics/")
        ) and detected_scope(quote):
            entry = evidence.get(path)
            if entry and quantities(str(value)).issubset(quantities(quote)):
                scoped = data["trust_and_proof"]["scoped_claims"]
                index = len(scoped)
                scoped.append({"text": str(value), "scope": detected_scope(quote), "subject": None})
                if audit:
                    audit.alias(path, f"/trust_and_proof/scoped_claims/{index}/text")
                additions.extend(
                    [
                        entry.model_copy(
                            update={"path": f"/trust_and_proof/scoped_claims/{index}/text"}
                        ),
                        entry.model_copy(
                            update={
                                "path": f"/trust_and_proof/scoped_claims/{index}/scope",
                                "origin": "CLASSIFIED",
                                "reasoning": (
                                    "Scope follows explicit facility, newsletter or "
                                    "customer context in the cited passage."
                                ),
                            }
                        ),
                    ]
                )
            reject(path, "SCOPED_CLAIM_RELOCATED_OR_OMITTED")
            warnings.append(
                f"{path}: scoped claim omitted from company-wide proof; use scoped_claims."
            )

    filtered = [
        entry
        for entry in result.evidence
        if not any(entry.path == p or entry.path.startswith(p + "/") for p in rejected)
    ]
    return ExtractionResult(
        profile=BIP.model_validate(data), evidence=filtered + additions, warnings=result.warnings
    )


def add_catalog_classifications(
    profile: BIP, provenance: list[dict], sources: SourceBundle, warnings, audit=None
):
    proof = {p["path"]: p for p in provenance}
    keys = set()
    for i, offering in enumerate(profile.offerings):
        path = f"/offerings/{i}"
        name = proof.get(path + "/name")
        key = slug(offering.name)
        if key in keys:
            key += "-" + str(i + 1)
        keys.add(key)
        offering.key = key
        if name:
            provenance.append(
                {
                    **name,
                    "path": path + "/key",
                    "origin": "CLASSIFIED",
                    "reasoning": "Stable identifier derived from the verified offering name.",
                }
            )
        if offering.kind is None and name:
            context = " ".join(c["quote"] for c in name["citations"]) + " " + offering.name
            structured = any(
                offering.name.casefold() == str(p.get("name", "")).casefold()
                for d in sources.documents
                for p in d.metadata.get("products", [])
            )
            kind = kind_from_context(context, structured)
            if kind:
                offering.kind = kind
                provenance.append(
                    {
                        **name,
                        "path": path + "/kind",
                        "origin": "CLASSIFIED",
                        "reasoning": (
                            "Product/service classification from verified name, "
                            "page context and structured product data."
                        ),
                    }
                )
    for i, offering in enumerate(profile.offerings):
        if offering.parent_offering_key and (
            offering.parent_offering_key not in keys or offering.parent_offering_key == offering.key
        ):
            offering.parent_offering_key = None
            if audit:
                audit.discard(f"/offerings/{i}/parent_offering_key", "UNRESOLVED_PARENT_OFFERING")
            warnings.append(f"{offering.key}: unresolved product-family relationship omitted.")


def source_coverage(sources: SourceBundle) -> dict:
    if sources.coverage:
        return sources.coverage
    counts = Counter(d.metadata.get("category") or page_category(d.url) for d in sources.documents)
    return {
        "pages": len(sources.documents),
        "categories": dict(counts),
        "missing_categories": [c for c in CATEGORIES if not counts[c]],
        "text_characters": sum(len(d.text) for d in sources.documents),
    }


def creative_readiness(
    profile: BIP, selected_offering_key: str | None = None, provenance: list[dict] | None = None
) -> dict:
    """Assess retained content, with product-specific checks only for explicit selection.

    Claim support requires validated website provenance or an explicitly user-confirmed
    assertion. Hypotheses, other products' claims and facility/newsletter proof do not
    establish an offering claim. This is a review aid, never a save/confirmation gate.
    """
    missing = []
    selected = next(
        (o for o in profile.offerings if selected_offering_key and o.key == selected_offering_key),
        None,
    )
    audience = selected.audiences if selected else profile.customer_personas_and_audience_segments

    def audience_details(value):
        return any(
            p.description or p.roles or p.goals or p.pain_points for p in value.personas
        ) or any(s.description or s.needs for s in value.segments)

    checks = [
        (
            profile.brand_identity.name,
            "brand_identity.name",
            "What company name should the campaign use?",
        ),
        (profile.offerings, "offerings", "Which products or services should we feature?"),
        (
            profile.brand_identity.description or profile.value_propositions.primary,
            "value_propositions",
            "What makes your offering meaningfully different?",
        ),
        (
            audience_details(audience),
            "audience",
            "Who is the intended buyer and what are they trying to achieve?",
        ),
        (profile.brand_voice.tone, "brand_voice", "What tone should the brand use?"),
        (
            profile.creative_context.content_pillars or profile.creative_context.creative_angles,
            "creative_context",
            "Which stories or everyday situations resonate with your customers?",
        ),
    ]
    for present, field, question in checks:
        if not present:
            missing.append(
                {
                    "field": field,
                    "reason": "No supported information available.",
                    "suggested_question": question,
                }
            )
    assessment = None
    if selected:
        index = profile.offerings.index(selected)
        proof = {p["path"]: p for p in provenance or []}

        def supported(path, classification=False):
            entry = proof.get(path, {})
            return (
                (entry.get("origin") == "WEBSITE" and bool(entry.get("citations")))
                or (entry.get("origin") == "USER" and entry.get("confirmed_by_user") is True)
                or (
                    classification
                    and entry.get("origin") == "CLASSIFIED"
                    and bool(entry.get("citations"))
                    and bool(entry.get("reasoning"))
                )
            )

        def supported_claim(claim, prefix):
            return (
                claim.scope == "OFFERING"
                and (not claim.subject or claim.subject in {selected.key, selected.name})
                and all(
                    supported(prefix + "/" + field, field == "scope")
                    for field in (
                        ["text", "scope", "subject"] if claim.subject else ["text", "scope"]
                    )
                )
            )

        has_claim = any(
            supported_claim(c, f"/offerings/{index}/claims/{i}")
            for i, c in enumerate(selected.claims)
        ) or any(
            c.subject in {selected.key, selected.name}
            and supported_claim(c, f"/trust_and_proof/scoped_claims/{i}")
            for i, c in enumerate(profile.trust_and_proof.scoped_claims)
        )
        # Scope labels are classifications, not new advertising assertions.
        # A source-grounded CLASSIFIED scope is allowed, but inferred claim text is not.
        assessment = {
            "benefits": bool(selected.benefits or selected.value_propositions),
            "target_audience": bool(audience_details(selected.audiences)),
            "motivations": bool(selected.customer_psychology.motivations),
            "supported_marketing_claims": bool(has_claim),
        }
        questions = {
            "benefits": ("benefits", "Which concrete benefits does this offering deliver?"),
            "target_audience": (
                "audiences",
                "Who buys this specific offering and what do they need?",
            ),
            "motivations": (
                "customer_psychology.motivations",
                "Why would this offering matter to the intended buyer?",
            ),
            "supported_marketing_claims": (
                "claims",
                "Which product-specific claims have source evidence or explicit customer approval?",
            ),
        }
        for check, present in assessment.items():
            if not present:
                field, question = questions[check]
                missing.append(
                    {
                        "field": f"offerings.{selected.key}.{field}",
                        "reason": "Selected offering lacks sufficient "
                        + check.replace("_", " ")
                        + ".",
                        "suggested_question": question,
                    }
                )
    else:
        missing.append(
            {
                "field": "selected_offering_key",
                "reason": (
                    "The requested offering key was not found."
                    if selected_offering_key
                    else "Brand-level brief only; offering-specific targeting "
                    "still needs a selected offering."
                ),
                "suggested_question": "Which offering should the next creative brief target?",
            }
        )
    return {
        "status": "NEEDS_REVIEW" if missing else "READY_FOR_CREATIVE_BRIEF",
        "scope": "OFFERING" if selected else "BRAND",
        "selected_offering_key": selected.key if selected else None,
        "requested_offering_key": selected_offering_key,
        "offering_assessment": assessment,
        "missing_information": missing,
        "requires_human_review": True,
    }


def metrics(records: list[dict], settings, elapsed: float) -> dict:
    known = [r for r in records if r.get("input_tokens") is not None]
    uses_plan = any(r.get("billing_source") == "chatgpt_plan" for r in records)
    estimated = None
    if (
        records
        and not uses_plan
        and len(known) == len(records)
        and settings.llm_input_usd_per_million is not None
        and settings.llm_output_usd_per_million is not None
    ):
        estimated = sum(
            (
                (r["input_tokens"] - (r.get("cached_input_tokens") or 0))
                * settings.llm_input_usd_per_million
                + (r.get("cached_input_tokens") or 0)
                * (
                    settings.llm_cached_input_usd_per_million
                    if settings.llm_cached_input_usd_per_million is not None
                    else settings.llm_input_usd_per_million
                )
                + r["output_tokens"] * settings.llm_output_usd_per_million
            )
            / 1000000
            for r in known
        )
    return {
        "calls": records,
        "latency_seconds": round(elapsed, 3),
        "input_tokens": sum(r["input_tokens"] for r in known),
        "output_tokens": sum(r["output_tokens"] for r in known),
        "usage_complete": len(known) == len(records),
        "estimated_cost_usd": estimated,
        "billing_source": "chatgpt_plan" if uses_plan else "api_key",
        "cost_basis": (
            "ChatGPT plan/credits; no API-key USD charge is calculated."
            if uses_plan
            else "Configured token rates; not an invoice. "
            "Null if rates/attempt usage are unavailable."
        ),
        "rates": {
            "input": settings.llm_input_usd_per_million,
            "output": settings.llm_output_usd_per_million,
            "cached_input": settings.llm_cached_input_usd_per_million,
        },
    }


@dataclass
class PipelineResult:
    profile: BIP
    provenance: list[dict]
    warnings: list[str]
    metadata: dict


async def run_pipeline(
    provider,
    settings,
    website_url: str,
    sources: SourceBundle,
    registered_name=None,
    selected_offering_key=None,
) -> PipelineResult:
    started = time.monotonic()
    provider.usage_records = []
    warnings = list(sources.warnings)
    facts = await provider.extract_facts(website_url, sources)
    # Fact call never supplies audience hypotheses or creative reasoning.
    raw = facts.profile.model_dump()
    for root in MARKETING_ROOTS:
        raw[root] = BIP(brand_identity=raw["brand_identity"]).model_dump()[root]
    facts = ExtractionResult(
        profile=BIP.model_validate(raw),
        evidence=[
            e
            for e in facts.evidence
            if e.path.startswith("/") and e.path.split("/")[1] not in MARKETING_ROOTS
        ],
        warnings=facts.warnings,
    )
    factual_audit = EvidenceAudit(
        p
        for p in leaves(facts.profile.model_dump())
        if p != "/brand_identity/website_url"
        and not (registered_name and p == "/brand_identity/name")
    )
    checked = factual_consistency(
        literal_catalog_evidence(facts, sources), sources, warnings, factual_audit
    )
    bip, provenance = validated_profile(
        checked, sources, website_url, registered_name, warnings=warnings, audit=factual_audit
    )
    warnings.extend(facts.warnings)
    add_catalog_classifications(bip, provenance, sources, warnings, factual_audit)
    marketing_status = "SUCCEEDED"
    marketing_audit = None
    stage_errors = {}
    try:
        remaining = max(
            1,
            settings.job_timeout_seconds
            - settings.crawl_max_elapsed_seconds
            - (time.monotonic() - started)
            - 10,
        )
        async with asyncio.timeout(remaining):
            reasoning = await provider.infer_marketing(bip, sources, selected_offering_key)
        marketing_audit = EvidenceAudit(
            p
            for p in leaves(reasoning.model_dump(exclude={"evidence", "warnings"}))
            if not p.endswith("/offering_key")
        )
        data = bip.model_dump()
        for root in MARKETING_ROOTS:
            data[root] = getattr(reasoning, root).model_dump()
        mapped = []
        key_indexes = {o.key: i for i, o in enumerate(bip.offerings)}
        insight_indexes, seen_keys = {}, set()
        for i, insight in enumerate(reasoning.offering_insights):
            prefix = f"/offering_insights/{i}"
            if insight.offering_key not in key_indexes:
                marketing_audit.reject(prefix, "UNKNOWN_OFFERING_REFERENCE")
            elif insight.offering_key in seen_keys:
                marketing_audit.reject(prefix, "DUPLICATE_OFFERING_REFERENCE")
            else:
                index = key_indexes[insight.offering_key]
                insight_indexes[i] = index
                seen_keys.add(insight.offering_key)
                for path in marketing_audit.generated:
                    if path.startswith(prefix + "/"):
                        marketing_audit.alias(path, f"/offerings/{index}" + path[len(prefix) :])
        for entry in reasoning.evidence:
            parts = entry.path.split("/")
            if len(parts) > 3 and parts[1] == "offering_insights":
                try:
                    if not parts[2].isdigit():
                        raise ValueError("Invalid insight index")
                    index = insight_indexes[int(parts[2])]
                    if parts[3] not in {"audiences", "customer_psychology"}:
                        continue
                    mapped.append(
                        entry.model_copy(
                            update={"path": f"/offerings/{index}/" + "/".join(parts[3:])}
                        )
                    )
                except (ValueError, IndexError, KeyError):
                    warnings.append("Unknown offering insight reference omitted.")
            elif len(parts) > 1 and parts[1] in MARKETING_ROOTS:
                mapped.append(entry)
            else:
                marketing_audit.ignored_evidence_entries += 1
        for i, insight in enumerate(reasoning.offering_insights):
            if i in insight_indexes:
                index = insight_indexes[i]
                data["offerings"][index]["audiences"] = insight.audiences.model_dump()
                data["offerings"][index]["customer_psychology"] = (
                    insight.customer_psychology.model_dump()
                )
        # Keep only reasoning-stage fields for validation; do not allow stage 2 to replace facts.
        skeleton = BIP(brand_identity={"website_url": website_url}).model_dump()
        for root in MARKETING_ROOTS:
            skeleton[root] = data[root]
        skeleton["offerings"] = [
            {
                **BIP(
                    brand_identity={"website_url": website_url}, offerings=[{"name": o.name}]
                ).model_dump()["offerings"][0],
                "audiences": data["offerings"][i]["audiences"],
                "customer_psychology": data["offerings"][i]["customer_psychology"],
            }
            for i, o in enumerate(bip.offerings)
        ]
        # Offering names anchor indexes and come from already verified facts.
        name_entries = [
            LLMEvidence.model_validate(
                {
                    **{k: p[k] for k in ("path", "origin", "reasoning")},
                    "citations": [
                        {k: c[k] for k in ("source_url", "quote")} for c in p["citations"]
                    ],
                }
            )
            for p in provenance
            if re.fullmatch(r"/offerings/\d+/name", p["path"]) and p["origin"] != "USER"
        ]
        insights, insight_proof = validated_profile(
            ExtractionResult(profile=BIP.model_validate(skeleton), evidence=name_entries + mapped),
            sources,
            website_url,
            None,
            warnings=warnings,
            audit=marketing_audit,
        )
        if len(insights.offerings) != len(bip.offerings):
            raise DomainError(
                "MARKETING_ANCHOR_INVALID", "Marketing offering anchors did not validate"
            )
        for root in MARKETING_ROOTS:
            setattr(bip, root, getattr(insights, root))
        # Verified anchors preserve order. Names can repeat; merging by name corrupts
        # offering-specific targeting and disagrees with the remapped provenance.
        for original, offering in zip(bip.offerings, insights.offerings, strict=True):
            original.audiences, original.customer_psychology = (
                offering.audiences,
                offering.customer_psychology,
            )
        provenance.extend(
            p
            for p in insight_proof
            if p["path"].split("/")[1] in MARKETING_ROOTS
            or "/audiences/" in p["path"]
            or "/customer_psychology/" in p["path"]
        )
        warnings.extend(reasoning.warnings)
        evidence_result = marketing_audit.report()
        if (
            not evidence_result["accepted"]
            or evidence_result["accepted"] * 2 < evidence_result["generated"]
        ):
            marketing_status = "INSUFFICIENT_EVIDENCE"
            warnings.append(
                "Audience/creative stage has insufficient retained evidence; "
                "review the validation diagnostics. Verified factual draft retained."
            )
    except (DomainError, TimeoutError, ValidationError) as error:
        marketing_status = "FAILED"
        if marketing_audit:
            marketing_audit.accepted.clear()
            marketing_audit.reject("", "MARKETING_STAGE_FAILED")
        code = (
            error.code
            if isinstance(error, DomainError)
            else "MARKETING_TIMEOUT"
            if isinstance(error, TimeoutError)
            else "MARKETING_INVALID_OUTPUT"
        )
        stage_errors["marketing"] = {
            "code": code,
            **(
                {"details": error.details}
                if isinstance(error, DomainError) and error.details
                else {}
            ),
        }
        warnings.append(
            f"Audience/creative stage unavailable ({code}); verified factual draft retained."
        )
    current = leaves(bip.model_dump())
    provenance = list({p["path"]: p for p in provenance if p["path"] in current}.values())
    validation = {
        "facts": factual_audit.report(),
        "marketing": marketing_audit.report() if marketing_audit else unavailable_evidence(),
    }
    for stage, report in validation.items():
        logger.info(
            "evidence_validation stage=%s generated=%s accepted=%s rejected=%s reasons=%s",
            stage,
            report["generated"],
            report["accepted"],
            report["rejected"],
            report["rejection_reasons"],
        )
    return PipelineResult(
        bip,
        provenance,
        warnings,
        {
            "pipeline_version": 2,
            "source_coverage": source_coverage(sources),
            "creative_readiness": creative_readiness(bip, selected_offering_key, provenance),
            "extraction_metrics": metrics(
                getattr(provider, "usage_records", []), settings, time.monotonic() - started
            ),
            "stages": {"facts": "SUCCEEDED", "marketing": marketing_status},
            "evidence_validation": validation,
            "stage_errors": stage_errors,
        },
    )
