"""Conservative deterministic checks, not an automated truth or advertising approval system."""

import json
import re
import unicodedata
from difflib import SequenceMatcher

from app.creative.schemas import EvaluatorResult
from app.errors import DomainError


def normalized(text):
    return re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def check_candidate(candidate, research):
    items = {i["id"]: i for i in research["items"]}
    brief = research["brief"]
    flags = []

    def flag(code, severity="REVIEW", field=None):
        entry = {"code": code, "severity": severity, "field": field}
        if entry not in flags:
            flags.append(entry)

    refs = candidate.research_reference_ids + candidate.capability_reference_ids
    if any(r not in items for r in refs):
        flag("UNKNOWN_RESEARCH_REFERENCE", "BLOCK")
    capabilities = [items[r] for r in candidate.capability_reference_ids if r in items]
    if not any(
        i["capability"] and normalized(candidate.product_capability) == normalized(i["text"])
        for i in capabilities
    ):
        flag("UNSUPPORTED_PRODUCT_CONNECTION", "BLOCK", "product_capability")
    if any(not i["capability"] for i in capabilities):
        flag("NON_FACTUAL_CAPABILITY_REFERENCE", "BLOCK")
    if normalized(candidate.audience) != normalized(brief.get("audience") or brief.get("concern")):
        flag("AUDIENCE_MISMATCH", "BLOCK", "audience")
    if brief.get("cta") and normalized(candidate.cta) != normalized(brief["cta"]):
        flag("CTA_MISMATCH", "BLOCK", "cta")
    text = json.dumps(
        candidate.model_dump(
            exclude={
                "potential_risks",
                "research_reference_ids",
                "capability_reference_ids",
                "factual_assumptions",
            }
        ),
        ensure_ascii=False,
    )
    norm = normalized(text)
    # Exclusions are literal forbidden phrases, not promises of semantic policy detection.
    for term in brief.get("creative_exclusions", []):
        if normalized(term) in norm:
            flag("CREATIVE_EXCLUSION", "BLOCK")
    restrictions = research.get("restrictions", {})
    for term in restrictions.get("prohibited_claims", []):
        if normalized(term) in norm:
            flag("PROHIBITED_CLAIM", "BLOCK")
    if any(
        re.search(r"\b" + re.escape(normalized(term)) + r"\b", norm)
        for term in restrictions.get("prohibited_terms", [])
    ):
        flag("PROHIBITED_TERM", "BLOCK")
    if re.search(
        r"\b(?:cures?|guaranteed results|safe for everyone|100\s*%\s*safe|no side effects)\b",
        text,
        re.I,
    ):
        flag("UNSUPPORTED_HEALTH_OR_SAFETY_ASSURANCE", "BLOCK")
    if re.search(
        r"clinical|dermatolog|certif|guarantee|\bSPF\b|\d+\s*%|\b(?:faster|cure|treat|safe)\b",
        text,
        re.I,
    ):
        flag("SENSITIVE_OR_QUANTIFIED_CLAIM")
    if re.search(r"\d", text):
        flag("NUMERIC_STATEMENT_REVIEW")
    if candidate.factual_assumptions:
        flag("FACTUAL_ASSUMPTIONS_REQUIRE_REVIEW")
    if any(i["kind"] in {"EXTRACTED_PROVISIONAL", "USER_ASSERTION"} for i in capabilities):
        flag("PROVISIONAL_PRODUCT_FACTS")
    opinions = [i for i in items.values() if i["kind"] == "CUSTOMER_OPINION"]
    if any(i["id"] in refs for i in opinions):
        flag("CUSTOMER_OPINION_NOT_AD_APPROVED")
    spoken = " ".join(
        [
            candidate.primary_hook,
            *candidate.alternative_hooks,
            *candidate.story.model_dump().values(),
            candidate.closing_line,
            candidate.cta,
        ]
    )
    for opinion in opinions:
        if len(opinion["text"]) > 20 and normalized(opinion["text"]) in normalized(spoken):
            flag("TESTIMONIAL_USAGE_REQUIRES_REVIEW")
            if opinion.get("sentiment") == "NEGATIVE_OR_MIXED":
                flag("NEGATIVE_FEEDBACK_AS_TESTIMONIAL", "BLOCK")
    if any(normalized(topic) in norm for topic in restrictions.get("sensitive_topics", [])):
        flag("SENSITIVE_TOPIC_REVIEW")
    production = " ".join(candidate.b_roll + [a.description for a in candidate.asset_requirements])
    if re.search(
        r"celebrity endorsement|voice clon|photorealistic avatar|required integration",
        production,
        re.I,
    ):
        flag("UNSUPPORTED_PRODUCTION_ASSUMPTION", "BLOCK")
    if restrictions.get("brand_safety_rules") or restrictions.get("approval_requirements"):
        flag("BRAND_ASSET_AND_COPY_RULES_REVIEW")
    for asset in candidate.asset_requirements:
        if any(r not in items for r in asset.research_reference_ids):
            flag("UNKNOWN_ASSET_REFERENCE", "BLOCK")
        # Phase 2A has no asset uploads or ownership/availability attestations.
        if not asset.requires_creation:
            flag("UNESTABLISHED_ASSET_AVAILABILITY", "BLOCK")
        flag("ASSET_CREATION_OR_PERMISSION_REQUIRED")
    hooks = [normalized(h) for h in [candidate.primary_hook, *candidate.alternative_hooks]]
    if len(set(hooks)) != len(hooks):
        flag("DUPLICATE_HOOK", "BLOCK")
    # Text-over-video suggestion only: detect obvious oversize, not a timing guarantee.
    if len(spoken.split()) > brief["duration_seconds"] * 7:
        flag("DURATION_FEASIBILITY_REVIEW")
    return {
        "eligible": not any(f["severity"] == "BLOCK" for f in flags),
        "flags": flags,
        "requires_human_review": True,
        "advertising_claims_approved": False,
    }


def validate_candidates(result, research):
    accepted, rejected, seen_keys, seen_hooks, signatures = [], [], set(), set(), []
    for candidate in result.candidates:
        validation = check_candidate(candidate, research)
        hooks = {normalized(h) for h in [candidate.primary_hook, *candidate.alternative_hooks]}
        signature = normalized(candidate.core_insight + " " + candidate.problem_or_aspiration)
        repeat = next(
            (
                key
                for key, sig, mechanism, insight in signatures
                if normalized(candidate.core_insight) == insight
                or signature == sig
                or (
                    normalized(candidate.strategic_mechanism) == mechanism
                    and SequenceMatcher(None, signature, sig).ratio() >= 0.78
                )
            ),
            None,
        )
        if candidate.candidate_key in seen_keys or seen_hooks & hooks or repeat:
            validation["flags"].append(
                {
                    "code": "REPEATED_CONCEPT" if repeat else "DUPLICATE_KEY_OR_HOOK",
                    "severity": "BLOCK",
                    "field": None,
                }
            )
            validation["eligible"] = False
        seen_keys.add(candidate.candidate_key)
        if validation["eligible"]:
            accepted.append((candidate, validation))
            seen_hooks.update(hooks)
            signatures.append(
                (
                    candidate.candidate_key,
                    signature,
                    normalized(candidate.strategic_mechanism),
                    normalized(candidate.core_insight),
                )
            )
        else:
            rejected.append(
                {
                    "candidate_key": candidate.candidate_key,
                    "reasons": [f["code"] for f in validation["flags"] if f["severity"] == "BLOCK"],
                }
            )
    return accepted, {
        "generated": len(result.candidates),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "rejections": rejected,
        "below_candidate_target": len(accepted) < 6,
        "limitations": result.limitations,
    }


def validate_evaluation(result: EvaluatorResult, candidates):
    expected = {c.candidate_key for c in candidates}
    keys = [e.candidate_key for e in result.evaluations]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise DomainError(
            "EVALUATOR_REFERENCE_INVALID",
            "Evaluator must assess each accepted candidate exactly once",
            502,
        )
    if sum(e.selected for e in result.evaluations) > 5:
        raise DomainError(
            "EVALUATOR_SELECTION_INVALID", "Evaluator selected more than five concepts", 502
        )
    return {e.candidate_key: e.model_dump(mode="json") for e in result.evaluations}
