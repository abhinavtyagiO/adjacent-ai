"""MVP draft-first orchestration and small deterministic review checks.

No evidence verifier runs here. Values remain provisional; source documents are saved by
our existing worker. Strict extraction remains a separate opt-in path in intelligence.py.
"""

import asyncio
import json
import logging
import re
import time
from datetime import UTC, datetime

from pydantic import ValidationError

from app.crawling.urls import normalize_url
from app.diagnostics import unavailable_evidence
from app.enrichment import Document, SourceBundle
from app.errors import DomainError
from app.provenance import leaves
from app.schemas import BIP, DraftMarketingResult, PricingTier

logger = logging.getLogger(__name__)


def clean_values(value, warnings, path=""):
    """Deduplicate repeated values and remove unsafe URLs/impossible numeric values only."""
    if isinstance(value, dict):
        return {k: clean_values(v, warnings, f"{path}/{k}") for k, v in value.items()}
    if isinstance(value, list):
        output, seen = [], set()
        for i, child in enumerate(value):
            cleaned = clean_values(child, warnings, f"{path}/{i}")
            key = json.dumps(cleaned, sort_keys=True).casefold()
            if cleaned is not None and key not in seen:
                seen.add(key)
                output.append(cleaned)
        return output
    if value is None:
        return None
    if isinstance(value, str):
        if re.search(
            r"/(?:url|pricing_url|guidelines_url|default_landing_page)$|/logo_urls/\d+$", path
        ):
            try:
                return normalize_url(value)
            except ValueError:
                warnings.append(f"{path}: unsafe or invalid URL omitted.")
                return None
        # Percentages outside [0,100] and negative exact prices are objectively inconsistent.
        if "/ingredients/" in path and any(
            float(n) < 0 or float(n) > 100 for n in re.findall(r"(-?\d+(?:\.\d+)?)\s*%", value)
        ):
            warnings.append(f"{path}: impossible percentage omitted; review original source.")
            return None
        if path.endswith("/amount") and re.search(r"-\s*\d", value):
            warnings.append(f"{path}: negative pricing amount omitted.")
            return None
    if path.endswith("/founded_year") and (value < 1 or value > datetime.now(UTC).year):
        warnings.append(f"{path}: impossible founding year omitted.")
        return None
    return value


def merge_record(first, other, warnings, path):
    """Merge duplicate catalog records, retaining first conflicting scalar for review."""
    for key, value in other.items():
        if isinstance(value, list):
            first[key] = clean_values(first.get(key, []) + value, warnings, f"{path}/{key}")
        elif isinstance(value, dict):
            merge_record(first[key], value, warnings, f"{path}/{key}")
        elif first.get(key) is None:
            first[key] = value
        elif value is not None and value != first[key]:
            warnings.append(
                f"{path}/{key}: conflicting duplicate values; first value retained for review."
            )
    return first


def basic_profile(profile, website_url, registered_name, warnings):
    """Keep uncited offerings, resolve obvious tiers/duplicates and safe hierarchy keys."""
    from app.intelligence import PLAN_NAMES, kind_from_context, slug

    data = clean_values(profile.model_dump(), warnings)
    data["brand_identity"]["website_url"] = website_url
    if registered_name:
        data["brand_identity"]["name"] = registered_name
    catalog, by_name = [], {}
    for item in data["offerings"]:
        # Whitespace-only names are rejected by Pydantic before this boundary.
        key = item["name"].casefold()
        if key in by_name:
            merge_record(by_name[key], item, warnings, "/offerings")
            warnings.append("Duplicate offering records merged by name; review conflicting fields.")
        else:
            by_name[key] = item
            catalog.append(item)
    products = [
        o
        for o in catalog
        if o["name"].casefold() not in PLAN_NAMES
        and o["kind"] in (None, "PRODUCT")
        and re.search(
            r"\bsoftware\b|\bplatform\b|\bsaas\b|\bapp\b|\bapi\b",
            o["name"] + " " + (o["description"] or ""),
            re.I,
        )
    ]
    kept = []
    for item in catalog:
        context = item["name"] + " " + (item["description"] or "")
        is_tier = item["name"].casefold() in PLAN_NAMES and (
            any(item["pricing"].values())
            or re.search(r"\bplan|subscription|credits|per month", context, re.I)
        )
        if is_tier and len(products) == 1:
            products[0]["pricing_tiers"].append(
                {"name": item["name"], "pricing": item["pricing"], "features": item["features"]}
            )
            warnings.append("Subscription plan moved under the software product's pricing tiers.")
            continue
        if is_tier:
            warnings.append(
                "Ambiguous subscription plan kept as provisional OTHER; choose its parent product."
            )
            item["kind"] = "OTHER"
        if item["kind"] == "OTHER" and re.search(
            r"newsletter|calculator|template|ingredient.*look.?up", context, re.I
        ):
            data["creative_context"]["available_assets"].append(item["name"])
            warnings.append(
                "Supporting resource moved to existing assets; not treated as an offering."
            )
            continue
        expected = kind_from_context(context)
        if expected and item["kind"] != expected:
            if item["kind"]:
                warnings.append(
                    "Contradictory product/service classification corrected provisionally."
                )
            item["kind"] = expected
        kept.append(item)
    keys = set()
    for item in kept:
        key = item.get("key") or slug(item["name"])
        base, suffix = key, 2
        while key in keys:
            key = f"{base}-{suffix}"
            suffix += 1
        item["key"] = key
        keys.add(key)
        # Same-name conflicting subscription prices are kept once and flagged, not guessed.
        tiers = {}
        for tier in item["pricing_tiers"]:
            ident = tier["name"].casefold()
            if ident in tiers:
                merge_record(tiers[ident], tier, warnings, "/offerings/pricing_tiers")
            else:
                tiers[ident] = tier
        item["pricing_tiers"] = [PricingTier.model_validate(t).model_dump() for t in tiers.values()]
    by_key = {o["key"]: o for o in kept}
    for item in kept:
        parent = item["parent_offering_key"]
        visited = {item["key"]}
        while parent and parent in by_key and parent not in visited:
            visited.add(parent)
            parent = by_key[parent]["parent_offering_key"]
        if parent:  # dangling reference, self-parent or cycle
            item["parent_offering_key"] = None
            warnings.append("Invalid product-family parent relationship cleared for review.")
    data["offerings"] = kept
    return BIP.model_validate(data)


def usable_offerings(profile):
    """A name plus useful product/service context is required, not just schema validity."""
    return [
        o
        for o in profile.offerings
        if o.kind != "OTHER"
        and (o.description or o.features or o.benefits or o.ingredients or o.use_cases)
    ]


def completeness(profile):
    sections = {k: bool(leaves(v)) for k, v in profile.model_dump().items()}
    missing = []
    if not profile.brand_identity.description and not profile.business_positioning.market_position:
        missing.append("brand description or positioning")
    if not usable_offerings(profile):
        missing.append("a marketable offering with description, features, benefits or use cases")
    if profile.offerings and not any(o.benefits or o.features for o in profile.offerings):
        missing.append("product benefits/features")
    return {
        "populated_fields": len(leaves(profile.model_dump())),
        "offering_count": len(profile.offerings),
        "usable_offering_count": len(usable_offerings(profile)),
        "populated_sections": sections,
        "missing_information": missing,
        "requires_human_review": True,
    }


def claim_review(profile, provenance):
    """Conservative review flags, not a legal/clinical classifier or ad approval system."""
    proof = {e["path"]: e for e in provenance}
    output = []
    for path, value in leaves(profile.model_dump()).items():
        if path.endswith(("/key", "/parent_offering_key", "/scope", "/subject", "/url")):
            continue
        if (
            path.startswith(("/customer_psychology/", "/customer_personas_and_audience_segments/"))
            or "/audiences/" in path
            or "/customer_psychology/" in path
        ):
            # Buyer fears/needs are hypotheses, not product efficacy claims.
            continue
        if path.endswith("/name") and not re.search(r"\d+(?:\.\d+)?\s*%|\bspf\s*\d+", value, re.I):
            continue
        if not isinstance(value, str):
            continue
        reasons = []
        if (
            "/pricing/" in path
            or "/pricing_tiers/" in path
            or re.search(r"\bdiscount|\b\d+%\s*off|\bsale\b", value, re.I)
        ):
            reasons.append("PRICING_OR_PROMOTION")
        if re.search(r"\d+(?:\.\d+)?\s*%", value):
            reasons.append("PERCENTAGE_OR_CONCENTRATION")
        if re.search(
            r"clinical|medical|treat|acne|eczema|pigment|wrinkle|skin|health|nutri|protein|weight|cure|spf|hair.*growth",
            value,
            re.I,
        ):
            reasons.append("HEALTH_OR_EFFICACY")
        if re.search(
            r"certif|guarantee|\biso\b|\bfssai\b|\bfda\b|\bclinically\b", path + " " + value, re.I
        ):
            reasons.append("CERTIFICATION_OR_GUARANTEE")
        if ("/claims/" in path or "/scoped_claims/" in path or "/metrics/" in path) or re.search(
            r"\d+.*(?:faster|improv|reduc|increase|placement|result)", value, re.I
        ):
            reasons.append("ADVERTISING_ASSERTION")
        if reasons:
            entry = proof.get(path, {})
            reviewed = entry.get("confirmed_by_user") is True
            output.append(
                {
                    "path": path,
                    "reasons": reasons,
                    "origin": entry.get("origin", "UNKNOWN"),
                    "status": "USER_REVIEWED" if reviewed else "REQUIRES_REVIEW",
                    "independently_verified": False,
                    "source_verified": entry.get("origin") == "WEBSITE"
                    and bool(entry.get("citations")),
                    "approved_for_advertising": False,
                    "requires_advertising_approval": True,
                }
            )
    return output


def draft_provenance(profile, website_url, registered_name):
    stamp = datetime.now(UTC).isoformat()
    return [
        {
            "path": p,
            "origin": "USER"
            if p == "/brand_identity/website_url"
            or (registered_name and p == "/brand_identity/name")
            else "AI_EXTRACTED",
            "citations": [],
            "reasoning": None,
            "recorded_at": stamp,
        }
        for p in leaves(profile.model_dump())
    ]


def relevant_context(sources, profile, selected_key, max_chars):
    """Bound website passages with deterministic offering-name/URL relevance, no new crawl."""
    offerings = sorted(profile.offerings, key=lambda o: o.key != selected_key)
    terms = [o.name.casefold() for o in offerings]
    ranked = []
    for doc in sources.documents:
        for i in range(0, len(doc.text), 700):
            passage = doc.text[i : i + 700]
            score = sum(2 for t in terms if t in passage.casefold())
            score += 4 if any(o.url == doc.url for o in offerings) else 0
            ranked.append((score, i == 0, doc, passage))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    passages, used, per_doc = {}, 0, {}
    for _, _, doc, text in ranked:
        if used + len(text) + 1 > max_chars or per_doc.get(doc.url, 0) + len(text) > 2800:
            continue
        passages.setdefault(doc.url, (doc, []))[1].append(text)
        used += len(text) + 1
        per_doc[doc.url] = per_doc.get(doc.url, 0) + len(text)
    return SourceBundle(
        [
            Document(url, "\n".join(texts), doc.fetched_at, title=doc.title, metadata=doc.metadata)
            for url, (doc, texts) in passages.items()
        ]
    )


async def run_draft_pipeline(
    provider, settings, website_url, sources, registered_name=None, selected_offering_key=None
):
    """Two calls at most: basic factual draft, optional offering-focused hypotheses."""
    from app.intelligence import PipelineResult, creative_readiness, metrics, source_coverage

    started = time.monotonic()
    provider.usage_records = []
    provider.factual_parse_counts = None
    facts = await provider.extract_facts(website_url, sources)
    warnings = [*sources.warnings, *facts.warnings]
    profile = basic_profile(facts.profile, website_url, registered_name, warnings)
    provenance = draft_provenance(profile, website_url, registered_name)
    fact_status = "SUCCEEDED" if usable_offerings(profile) else "INSUFFICIENT_DATA"
    marketing_status, stage_errors = "SKIPPED", {}
    if fact_status == "SUCCEEDED":
        factual_profile = profile.model_copy(deep=True)
        factual_provenance = list(provenance)
        try:
            remaining = max(
                1,
                settings.job_timeout_seconds
                - settings.crawl_max_elapsed_seconds
                - (time.monotonic() - started)
                - 10,
            )
            async with asyncio.timeout(remaining):
                raw = await provider.infer_marketing(
                    profile,
                    relevant_context(
                        sources,
                        profile,
                        selected_offering_key,
                        settings.marketing_context_max_chars,
                    ),
                    selected_offering_key,
                )
            # Legacy mocked adapters may still return their evidence envelope; never use it here.
            payload = clean_values(raw.model_dump(exclude={"evidence", "explanation"}), warnings)
            if "explanation" in type(raw).model_fields:
                payload["explanation"] = raw.explanation
            result = DraftMarketingResult.model_validate(payload)
            stamp = datetime.now(UTC).isoformat()
            generated = {}
            for root in (
                "customer_personas_and_audience_segments",
                "customer_psychology",
                "brand_voice",
                "creative_context",
            ):
                data = getattr(profile, root).model_dump()
                for key, value in getattr(result, root).model_dump().items():
                    if not value:
                        continue
                    if root == "creative_context" and key in {
                        "available_assets",
                        "existing_content_formats",
                        "calls_to_action",
                    }:
                        continue  # Existing assets are factual observations, not new inventions.
                    data[key] = value
                    generated.update(leaves({key: value}, f"/{root}"))
                setattr(profile, root, type(getattr(profile, root)).model_validate(data))
            indexes = {o.key: i for i, o in enumerate(profile.offerings)}
            seen = set()
            explanations = {}
            for insight in result.offering_insights:
                key = insight.offering_key
                if key not in indexes or key in seen:
                    warnings.append(
                        "Unknown or duplicate offering insight ignored; factual catalog retained."
                    )
                    continue
                seen.add(key)
                index = indexes[key]
                offering = profile.offerings[index]
                offering.audiences = insight.audiences
                offering.customer_psychology = insight.customer_psychology
                for field in ("audiences", "customer_psychology"):
                    paths = leaves(
                        getattr(offering, field).model_dump(), f"/offerings/{index}/{field}"
                    )
                    generated.update(paths)
                    explanations.update({path: insight.explanation for path in paths})
            provenance = [e for e in provenance if e["path"] not in generated]
            provenance.extend(
                {
                    "path": path,
                    "origin": "INFERRED",
                    "citations": [],
                    "reasoning": explanations.get(path) or result.explanation,
                    "recorded_at": stamp,
                }
                for path in generated
                if path in leaves(profile.model_dump())
            )
            warnings.extend(result.warnings)
            marketing_status = "SUCCEEDED" if generated else "INSUFFICIENT_DATA"
        except (DomainError, TimeoutError, ValidationError) as error:
            profile, provenance = factual_profile, factual_provenance
            marketing_status = "FAILED"
            code = (
                error.code
                if isinstance(error, DomainError)
                else "MARKETING_TIMEOUT"
                if isinstance(error, TimeoutError)
                else "MARKETING_INVALID_OUTPUT"
            )
            stage_errors["marketing"] = {"code": code}
            warnings.append(f"Marketing unavailable ({code}); provisional factual draft retained.")
    else:
        stage_errors["facts"] = {"code": "INSUFFICIENT_DATA"}
        stage_errors["marketing"] = {"code": "NO_USABLE_OFFERING"}
        warnings.append("No usable marketable offering; marketing skipped without retry.")
    validation = {
        stage: {**unavailable_evidence(), "status": "NOT_APPLICABLE"}
        for stage in ("facts", "marketing")
    }
    diagnostics = completeness(profile)
    logger.info(
        "draft_extraction stages=%s offerings=%s populated_fields=%s",
        {"facts": fact_status, "marketing": marketing_status},
        diagnostics["offering_count"],
        diagnostics["populated_fields"],
    )
    return PipelineResult(
        profile,
        provenance,
        warnings,
        {
            "pipeline_version": 2,
            "extraction_mode": "draft_first",
            "source_coverage": source_coverage(sources),
            "creative_readiness": creative_readiness(
                profile, selected_offering_key, provenance, mode="draft_first"
            ),
            "extraction_metrics": metrics(
                provider.usage_records, settings, time.monotonic() - started
            ),
            "stages": {"facts": fact_status, "marketing": marketing_status},
            "evidence_validation": validation,
            "stage_errors": stage_errors,
            "completeness": diagnostics,
            "claim_review": claim_review(profile, provenance),
        },
    )
