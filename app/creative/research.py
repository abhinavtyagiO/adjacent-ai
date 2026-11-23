"""Pure bounded context assembly: immutable BIP/source revision, never a crawl or inference."""

import hashlib
import json
import re

from app.errors import DomainError
from app.provenance import leaves

CAPABILITY_FIELDS = {
    "features",
    "benefits",
    "use_cases",
    "functional_benefits",
    "primary",
    "description",
}


def normalize(text):
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


def validate_focus(brief, bip):
    if not brief.offering_key:
        return
    selected = next((o for o in bip.offerings if o.key == brief.offering_key), None)
    if not selected:
        raise DomainError("OFFERING_NOT_FOUND", "The selected BIP offering does not exist", 422)
    family = selected.catalog_level == "FAMILY"
    if family != (brief.promotion_scope == "FAMILY"):
        raise DomainError("OFFERING_SCOPE_MISMATCH", "Offering does not match promotion scope", 422)


def build_context(bip, provenance, documents, brief, concerns, max_chars):
    """Reference IDs are path-derived within this snapshot; origins never come from the LLM.

    Source passages and quotes are observations/opinions, not new product substantiation.
    Full marketing restrictions are reserved first; no silently truncated prohibition list.
    """
    validate_focus(brief, bip)
    data = bip.model_dump(mode="json")
    proof = {p["path"]: p for p in provenance}
    sources = {d.url: d for d in documents}
    payload = {
        "brief": brief.model_dump(mode="json"),
        "restrictions": {
            **data["marketing_guardrails"],
            "prohibited_terms": data["brand_voice"]["avoided_terms"],
        },
        "items": [],
        "source_documents": [
            {"url": d.url, "fetched_at": d.fetched_at.isoformat(), "content_hash": d.content_hash}
            for d in documents[:40]
        ],
        "limitations": [
            "Saved sources only; no recrawl or additional brand extraction.",
            "Audience hypotheses and creative references are not verified product facts.",
            "Customer quotations may be negative, mixed or aspirational; "
            "never turn them into positive proof.",
            "A source URL or asset mention does not establish possession "
            "or advertising permission.",
        ],
    }
    if len(json.dumps(payload, ensure_ascii=False)) > max_chars:
        raise DomainError(
            "RESEARCH_BUDGET", "Brief or mandatory guardrails exceed context limit", 422
        )
    omitted = 0
    content_limit = max_chars - 5000 if max_chars >= 14000 else max_chars - 128

    def add(path, text, kind, origin=None, citations=(), capability=False, **extra):
        nonlocal omitted
        item = {
            "id": "r-" + hashlib.sha256(path.encode()).hexdigest()[:16],
            "path": path,
            "text": str(text)[:1000],
            "kind": kind,
            "origin": origin,
            "source_urls": list(
                dict.fromkeys(c["source_url"] for c in citations if c.get("source_url") in sources)
            )[:5],
            "capability": capability,
            **extra,
        }
        if len(str(text)) > 1000:
            item["truncated"] = True
        payload["items"].append(item)
        if (
            len(payload["items"]) > 180
            or len(json.dumps(payload, ensure_ascii=False)) > content_limit
        ):
            payload["items"].pop()
            omitted += 1

    focus = {brief.offering_key} if brief.offering_key else set()
    # Family focus includes its children, never unrelated product claims.
    if brief.promotion_scope == "FAMILY":
        focus.update(o.key for o in bip.offerings if o.parent_offering_key == brief.offering_key)
    roots = [
        "marketing_guardrails",
        "brand_identity",
        "business_positioning",
        "brand_voice",
        "value_propositions",
        "offerings",
        "customer_personas_and_audience_segments",
        "customer_psychology",
        "trust_and_proof",
        "visual_identity",
        "creative_context",
    ]
    groups = []
    for root in roots:
        group = []
        for path, value in leaves(data[root], "/" + root).items():
            parts = path.split("/")
            if root == "offerings" and focus and data[root][int(parts[2])]["key"] not in focus:
                continue
            if path.endswith(("/key", "/parent_offering_key")) or root == "marketing_guardrails":
                continue
            entry = proof.get(path, {})
            origin = entry.get("origin", "UNKNOWN")
            citations = entry.get("citations", [])
            verified = origin == "WEBSITE" and any(
                c.get("source_url") in sources
                and c.get("quote")
                and normalize(c["quote"]) in normalize(sources[c["source_url"]].content)
                and normalize(value) in normalize(c["quote"])
                for c in citations
            )
            opinion = root == "trust_and_proof" and "/testimonials/" in path
            kind = (
                "CUSTOMER_OPINION"
                if opinion
                else (
                    "AI_HYPOTHESIS"
                    if origin == "INFERRED"
                    else "VERIFIED_SOURCE_OBSERVATION"
                    if verified
                    else "USER_ASSERTION"
                    if origin == "USER"
                    else "EXTRACTED_PROVISIONAL"
                )
            )
            capability = (
                root in {"offerings", "value_propositions", "brand_identity"}
                and any(p in CAPABILITY_FIELDS for p in parts)
                and not opinion
                and len(str(value)) <= 700
                and kind not in {"AI_HYPOTHESIS"}
                and not any(p in {"audiences", "customer_psychology", "claims"} for p in parts)
            )
            extra = {}
            if opinion and not path.endswith("/quote"):
                continue
            if opinion:
                testimonial = data["trust_and_proof"]["testimonials"][int(parts[3])]
                extra["attribution"] = testimonial.get("attribution")
                extra["sentiment"] = (
                    "NEGATIVE_OR_MIXED"
                    if re.search(
                        r"burn|flaky|irritat|disappoint|didn.t|not work|extremely dry|worse",
                        str(value),
                        re.I,
                    )
                    else "UNASSESSED"
                )
                extra["advertising_use"] = "REQUIRES_HUMAN_REVIEW"
            group.append((path, value, kind, origin, citations, capability, extra))
        # Cap each section before round-robin allocation; a SKU catalog cannot consume
        # all room before reviews, audience hypotheses, visuals and creative themes.
        if root == "offerings":
            group.sort(key=lambda item: not item[5])
        groups.append(group)
    for index in range(max((len(g) for g in groups), default=0)):
        for group in groups:
            if index < len(group):
                path, value, kind, origin, citations, capability, extra = group[index]
                add(path, value, kind, origin, citations, capability, **extra)
    content_limit = max_chars - 128
    for i, concern in enumerate(concerns[:12]):
        add(
            f"/website_concerns/{i}", json.dumps(concern, ensure_ascii=False), "WEBSITE_NEED_SIGNAL"
        )
    # Bounded relevant passages supplement BIP values without resending the crawl.
    ordered = sorted(
        documents,
        key=lambda d: (
            d.source_metadata.get("category")
            not in {"customer_story", "use_case", "brand_assets", "faq"}
        ),
    )
    for i, doc in enumerate(ordered[:12]):
        if doc.source_type != "catalog":
            add(
                f"/source_passages/{i}",
                doc.content[:900],
                "SAVED_SOURCE_PASSAGE",
                source_url=doc.url,
            )
    payload["omitted_items"] = omitted
    # A tiny context cannot claim successful research if all product capabilities were omitted.
    if not any(i["capability"] for i in payload["items"]):
        raise DomainError(
            "RESEARCH_INSUFFICIENT", "Saved BIP has no usable product/brand capability", 422
        )
    if len(json.dumps(payload, ensure_ascii=False)) > max_chars:
        raise DomainError("RESEARCH_BUDGET", "Research context exceeds its limit", 422)
    return payload
