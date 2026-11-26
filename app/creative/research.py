"""Pure bounded context assembly: immutable BIP/source revision, never a crawl or inference."""

import hashlib
import json
import re
from itertools import zip_longest

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


PASSAGE_METADATA = re.compile(
    r"^(?:logo url|meta description|observed visual declaration|site-linked brand/press resource"
    r"|canonical url|structured products|theme color|learn more at)\b|https?://",
    re.I,
)
BOILERPLATE = re.compile(
    r"^(?:all rights reserved|copyright|privacy policy|terms (?:of use|and conditions)"
    r"|cookie (?:policy|preferences)|sign up for our newsletter|subscribe to our newsletter"
    r"|download our app|shop by category|shop by concerns)\b",
    re.I,
)
RESEARCH_SIGNAL = re.compile(
    r"\b(?:reviews?|customers?|users? say|struggl\w*|worr\w*|distract\w*|objections?"
    r"|hard to|concerns?|problems?|experience\w*|use cases?|helps?|benefits?|designed for"
    r"|works? by|built for|routines?|feel\w*|burn\w*|irritat\w*|side effects?)\b",
    re.I,
)
STOP_WORDS = set(
    "a an and are at be by for from has in is it of on or that the to was with you your".split()
)


def terms(text):
    return {
        word[:-1] if word.endswith("s") and len(word) > 4 else word
        for word in re.findall(r"[^\W_]+", normalize(text))
        if len(word) > 2 and word not in STOP_WORDS
    }


def audience_terms(audience):
    words = terms(audience)
    # Common near-neighbours make short briefs useful even when the site uses other wording.
    if words & {"student", "study", "studying", "school"}:
        words |= {"student", "study", "studying", "school", "homework", "exam", "class"}
    return words


def ranked_passages(documents, brief, offerings, focus):
    """Select exact excerpts from across the saved pages and retain their location."""
    audience = audience_terms(brief.audience or "")
    concern, objective = terms(brief.concern or ""), terms(brief.objective)
    if objective & {"awareness", "brand"}:
        objective |= {"mission", "designed", "built", "help", "benefit", "feature"}
    if objective & {"consideration", "conversion", "sale"}:
        objective |= {"question", "review", "price", "compare", "trial", "return"}
    focused = [offering for offering in offerings if offering.key in focus]
    product = terms(" ".join(offering.name for offering in focused))
    selected_urls = {offering.url for offering in focused if offering.url}
    ranked = []
    filtered = irrelevant = 0
    for doc in documents:
        if doc.source_type == "catalog":
            continue
        category = doc.source_metadata.get("category")
        heading = ""
        for number, line in enumerate(doc.content.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            if (
                PASSAGE_METADATA.search(line)
                or BOILERPLATE.search(line)
                or line == doc.title
                or line.startswith(("[{", "{", "Press, interview, and media inquiries:"))
            ):
                filtered += 1
                continue
            if line.startswith("#"):
                heading = line.lstrip("# ").strip()
                continue
            # Long review/FAQ lines are broken at sentence or word boundaries.
            pieces = re.findall(r".{1,480}(?:\s|$)|\S{481,}", line)
            for part, piece in enumerate(pieces):
                piece = piece.strip()
                if len(piece) > 480 or len(piece.split()) < 5:
                    filtered += 1
                    continue
                if len(piece) < 125 and re.search(
                    r"\b(?:founding team|founder|director of|chief executive|CEO)\b", piece, re.I
                ):
                    filtered += 1
                    continue
                words = terms(piece)
                nearby = terms(heading)
                score = (
                    5 * len(words & audience)
                    + 5 * len(words & concern)
                    + 4 * len(words & product)
                    + 2 * len(words & objective)
                    + min(5, 2 * len(nearby & (audience | concern | product)))
                    + min(6, 2 * len(RESEARCH_SIGNAL.findall(piece)))
                    + (4 if doc.url in selected_urls else 0)
                    + (3 if category in {"customer_story", "use_case", "faq", "brand_story"} else 0)
                    + (2 if "review" in heading.lower() else 0)
                )
                if score:
                    ranked.append((score, doc, number, part, piece))
                else:
                    irrelevant += 1
    ranked.sort(key=lambda row: (-row[0], row[1].url, row[2], row[3]))
    seen, passages = set(), []
    for row in ranked:
        fingerprint = re.sub(r"[^\w]+", " ", normalize(row[4])).strip()
        if fingerprint not in seen:
            seen.add(fingerprint)
            passages.append(row)
    return passages, filtered, irrelevant, len(ranked) - len(passages)


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
    # Reserve space for the coverage report, plus separate slots for passages and concerns.
    final_limit = max_chars - 650
    mandatory_size = len(json.dumps(payload, ensure_ascii=False))
    spare = max(0, final_limit - mandatory_size)
    passage_reserve = min(7000, int(spare * 0.24))
    concern_reserve = min(2200, int(spare * 0.1)) if concerns else 0
    content_limit = final_limit - passage_reserve - concern_reserve
    item_limit = 180 - 12 - (min(8, len(concerns)) if concerns else 0)

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
            len(payload["items"]) > item_limit
            or len(json.dumps(payload, ensure_ascii=False)) > content_limit
        ):
            payload["items"].pop()
            omitted += 1
            return False
        return True

    focus = {brief.offering_key} if brief.offering_key else set()
    # Family focus includes its children, never unrelated product claims.
    if brief.promotion_scope == "FAMILY":
        focus.update(o.key for o in bip.offerings if o.parent_offering_key == brief.offering_key)
    offerings = {offering.key: offering for offering in bip.offerings if offering.key}
    payload["capability_scope_version"] = 1
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
            if root == "offerings":
                offering = data[root][int(parts[2])]
                parent = offerings.get(offering["parent_offering_key"])
                extra = {
                    "offering_key": offering["key"],
                    "offering_name": offering["name"],
                    "family_key": (
                        offering["key"]
                        if offering["catalog_level"] == "FAMILY"
                        else parent.key
                        if parent and parent.catalog_level == "FAMILY"
                        else None
                    ),
                    "catalog_level": offering["catalog_level"],
                }
            elif brief.offering_key:
                # General brand positioning is still useful, but cannot substantiate
                # a promise for one named product or family.
                capability = False
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
        if root == "offerings":
            by_offering = {}
            for item in group:
                by_offering.setdefault(item[-1]["offering_key"], []).append(item)
            for offering_items in by_offering.values():
                offering_items.sort(key=lambda item: not item[5])
            group = [item for row in zip_longest(*by_offering.values()) for item in row if item]
        groups.append(group)
    # One large section or product should not fill every slot.
    for row in zip_longest(*groups):
        for item in row:
            if item:
                path, value, kind, origin, citations, capability, extra = item
                add(path, value, kind, origin, citations, capability, **extra)
    omitted_bip_items = omitted
    item_limit = 180 - 12
    content_limit = final_limit - passage_reserve
    target = audience_terms(brief.audience or "") | terms(brief.concern or "")
    ranked_concerns = sorted(
        enumerate(concerns),
        key=lambda entry: -len(terms(json.dumps(entry[1], ensure_ascii=False)) & target),
    )
    selected_concerns = sum(
        add(
            f"/website_concerns/{i}", json.dumps(concern, ensure_ascii=False), "WEBSITE_NEED_SIGNAL"
        )
        for i, concern in ranked_concerns[:8]
    )

    item_limit = 180
    content_limit = final_limit
    passages, filtered, irrelevant, duplicates = ranked_passages(
        documents, brief, bip.offerings, focus
    )
    per_source = {}
    covered_categories = set()
    selected_chars = 0
    for _, doc, line, part, passage in passages:
        if sum(per_source.values()) == 12:
            break
        if per_source.get(doc.url, 0) >= 3:
            continue
        source_key = hashlib.sha256(doc.url.encode()).hexdigest()[:16]
        if add(
            f"/source_passages/{source_key}/{line}/{part}",
            passage,
            "SAVED_SOURCE_PASSAGE",
            source_url=doc.url,
            content_hash=doc.content_hash,
            source_line=line,
        ):
            per_source[doc.url] = per_source.get(doc.url, 0) + 1
            covered_categories.add(doc.source_metadata.get("category") or "unknown")
            selected_chars += len(passage)
    selected_passages = sum(per_source.values())
    payload["coverage"] = {
        "passages_considered": len(passages),
        "passages_selected": selected_passages,
        "passages_omitted": len(passages) - selected_passages,
        "passage_characters": selected_chars,
        "passage_sources": len(per_source),
        "source_categories": sorted(covered_categories),
        "non_passage_lines_or_chunks_filtered": filtered,
        "duplicate_passages_omitted": duplicates,
        "low_relevance_passages_omitted": irrelevant,
        "concerns_selected": selected_concerns,
        "concerns_omitted": len(concerns) - selected_concerns,
    }
    payload["omitted_items"] = (
        omitted_bip_items + len(passages) - selected_passages + len(concerns) - selected_concerns
    )
    # A tiny context cannot claim successful research if all product capabilities were omitted.
    if not any(i["capability"] for i in payload["items"]):
        raise DomainError(
            "RESEARCH_INSUFFICIENT", "Saved BIP has no usable product/brand capability", 422
        )
    if len(json.dumps(payload, ensure_ascii=False)) > max_chars:
        raise DomainError("RESEARCH_BUDGET", "Research context exceeds its limit", 422)
    return payload
