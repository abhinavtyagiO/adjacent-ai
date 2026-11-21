"""Single-request marketing enrichment with a conservative, provenance-aware merge."""

from datetime import UTC, datetime

from app.draft import clean_values, relevant_context, usable_offerings
from app.errors import DomainError
from app.provenance import leaves
from app.schemas import BIP, DraftMarketingResult

MARKETING_ROOTS = (
    "customer_personas_and_audience_segments",
    "customer_psychology",
    "brand_voice",
    "creative_context",
)
FACTUAL_CREATIVE = {"available_assets", "existing_content_formats", "calls_to_action"}


def merge_marketing(
    profile, provenance, response, selected_key=None, replace_reviewed=False, reviewed_paths=()
):
    """Only audience/creative fields change. Arrays are atomic to protect edited indexes.

    Even explicit replacement cannot overwrite factual AI_EXTRACTED/WEBSITE/CLASSIFIED
    observations. Confirmed/user-edited hypotheses require replace_reviewed=true.
    """
    warnings = []
    payload = clean_values(response.model_dump(exclude={"evidence"}), warnings)
    result = DraftMarketingResult.model_validate(payload)
    data = profile.model_dump()
    generated, explanations, protected = {}, {}, set()
    proof = {p["path"]: p for p in provenance}
    changed_prefixes = set()

    def blocked(prefix):
        if (
            any(
                prefix == p or prefix.startswith(p + "/") or p.startswith(prefix + "/")
                for p in reviewed_paths
            )
            and not replace_reviewed
        ):
            return True
        for path, entry in proof.items():
            if path == prefix or path.startswith(prefix + "/"):
                if entry["origin"] in {"AI_EXTRACTED", "WEBSITE", "CLASSIFIED"}:
                    return True
                if (
                    entry["origin"] == "USER" or entry.get("confirmed_by_user")
                ) and not replace_reviewed:
                    return True
        return False

    def merge(old, incoming, path):
        if isinstance(incoming, dict):
            return {
                key: merge(old.get(key), val, path + "/" + key) for key, val in incoming.items()
            }
        if incoming in (None, [], "") or old == incoming:
            return old
        if blocked(path):
            protected.add(path)
            return old
        changed_prefixes.add(path)
        generated.update(leaves(incoming, path))
        return incoming

    for root in MARKETING_ROOTS:
        incoming = getattr(result, root).model_dump()
        if root == "creative_context":
            for field in FACTUAL_CREATIVE:
                incoming[field] = data[root][field]
        data[root] = merge(data[root], incoming, "/" + root)
    indexes = {o.key: i for i, o in enumerate(profile.offerings)}
    seen = set()
    for insight in result.offering_insights:
        key = insight.offering_key
        if key not in indexes or key in seen or (selected_key and key != selected_key):
            warnings.append("Unknown, duplicate or out-of-focus offering insight ignored.")
            continue
        seen.add(key)
        i = indexes[key]
        for field in ("audiences", "customer_psychology"):
            path = f"/offerings/{i}/{field}"
            data["offerings"][i][field] = merge(
                data["offerings"][i][field], getattr(insight, field).model_dump(), path
            )
            explanations.update(
                {p: insight.explanation for p in generated if p.startswith(path + "/")}
            )
    updated = BIP.model_validate(data)
    retained = [
        p
        for p in provenance
        if not any(
            p["path"] == prefix or p["path"].startswith(prefix + "/") for prefix in changed_prefixes
        )
    ]
    stamp = datetime.now(UTC).isoformat()
    retained.extend(
        {
            "path": p,
            "origin": "INFERRED",
            "citations": [],
            "reasoning": explanations.get(p) or result.explanation,
            "recorded_at": stamp,
        }
        for p in generated
    )
    return (
        updated,
        retained,
        warnings + result.warnings,
        {
            "generated_fields": sum(
                not p.endswith(("/offering_key", "/explanation"))
                for p in leaves(result.model_dump(exclude={"warnings", "explanation"}))
            ),
            "merged_fields": len(generated),
            "protected_fields": len(protected),
            "status": "SUCCEEDED" if generated else "INSUFFICIENT_DATA",
        },
    )


async def enrich(
    provider,
    settings,
    profile,
    provenance,
    sources,
    selected_key=None,
    replace_reviewed=False,
    reviewed_paths=(),
):
    if not usable_offerings(profile):
        raise DomainError("NO_USABLE_OFFERING", "A saved marketable offering is required", 422)
    if selected_key and selected_key not in {o.key for o in usable_offerings(profile)}:
        raise DomainError(
            "OFFERING_NOT_FOUND",
            "Selected offering is missing or lacks useful product context",
            422,
        )
    concerns = sources.discovery.get("concerns", [])
    sources.discovery = {
        **sources.discovery,
        "concerns": [
            {
                **c,
                "offering_keys": sorted(
                    set(c.get("offering_keys", []))
                    | {
                        o.key
                        for o in profile.offerings
                        if o.url in c.get("source_urls", []) and o.key
                    }
                ),
            }
            for c in concerns
        ],
    }
    response = await provider.infer_marketing(
        profile.model_copy(deep=True),
        relevant_context(sources, profile, selected_key, settings.marketing_context_max_chars),
        selected_key,
    )
    return merge_marketing(
        profile, provenance, response, selected_key, replace_reviewed, reviewed_paths
    )
