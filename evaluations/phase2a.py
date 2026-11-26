"""Prepare a local Opal draft or export an existing creative job via the app API."""

import argparse
import json
from uuid import UUID

import httpx

from app.config import get_settings
from app.creative.schemas import CampaignBrief

OPAL_BRIEF = CampaignBrief(
    objective="Awareness",
    audience="Students",
    promotion_scope="BRAND",
    target_platform="Instagram Reels",
    aspect_ratio="9:16",
    duration_seconds=15,
)
TERMINAL = {"SUCCEEDED", "PARTIAL", "FAILED", "INSUFFICIENT_DATA"}


def get(client, path, **params):
    response = client.get(path, params=params)
    response.raise_for_status()
    return response.json()


def prepare(client, brand_id):
    """Reuse a matching draft when present. This command never enqueues generation."""
    path = f"/v1/brands/{brand_id}"
    profile = get(client, f"{path}/profile")
    if profile["profile"]["brand_identity"]["name"].casefold() != "opal":
        raise ValueError("This benchmark expects the existing Opal brand profile")
    source_job_id = profile["source_job_id"]
    if not source_job_id or not any(
        item["job_id"] == source_job_id for item in get(client, f"{path}/sources")["items"]
    ):
        raise ValueError("The Opal profile needs a saved source snapshot")
    brief = OPAL_BRIEF.model_dump(mode="json")
    offset = 0
    while True:
        page = get(client, f"{path}/campaigns", limit=100, offset=offset)["items"]
        existing = next((campaign for campaign in page if campaign["brief"] == brief), None)
        if existing:
            campaign = existing
            break
        if len(page) < 100:
            response = client.post(f"{path}/campaigns", json=brief)
            response.raise_for_status()
            campaign = response.json()
            break
        offset += len(page)
    return {
        "campaign": campaign,
        "checked_profile_revision": profile["revision"],
        "checked_source_job_id": source_job_id,
    }


def copy_record(candidate):
    return {
        field: candidate.get(field)
        for field in ("primary_hook", "alternative_hooks", "on_screen_copy", "closing_line", "cta")
    }


def export(client, campaign_id, job_id):
    """Read a terminal job and its original candidates. No generation requests are sent."""
    path = f"/v1/campaigns/{campaign_id}"
    job = get(client, f"/v1/creative-generations/{job_id}")
    if job["campaign_id"] != str(campaign_id) or job["status"] not in TERMINAL:
        raise ValueError("Supply a finished generation job from this campaign")
    snapshot = get(client, f"{path}/research/{job['research_id']}")
    concepts = get(client, f"{path}/concepts", job_id=str(job_id), limit=100)["items"]
    shortlist = get(client, f"{path}/shortlist", job_id=str(job_id))
    rejected = job["diagnostics"].get("rejected_candidates", [])
    return {
        "job": job,
        "runtime": job["diagnostics"].get("runtime"),
        "generator_research": snapshot["payload"],
        "research_binding": {key: value for key, value in snapshot.items() if key != "payload"},
        "generated_candidates": [
            {
                "candidate": concept["original_content"],
                "viewer_copy": copy_record(concept["original_content"]),
                "validation": concept["validation"] if not concept["edited_by_user"] else None,
                "evaluation": concept["evaluation"],
                "current_validation": concept["validation"],
                "edited_by_user": concept["edited_by_user"],
            }
            for concept in concepts
        ]
        + [
            {**row, "viewer_copy": copy_record(row["candidate"]), "rejected": True}
            for row in rejected
        ],
        "rejections": job["diagnostics"].get("rejections", []),
        "shortlist": [
            {**concept, "viewer_copy": copy_record(concept["content"])}
            for concept in shortlist["items"]
        ],
        "shortlist_insufficient": shortlist["insufficient"],
        "shortlist_stale": shortlist.get("stale", False),
        "stage_usage": job.get("metrics", {}).get("calls", []),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    draft = commands.add_parser("prepare")
    draft.add_argument("brand_id", type=UUID)
    report = commands.add_parser("export")
    report.add_argument("campaign_id", type=UUID)
    report.add_argument("job_id", type=UUID)
    args = parser.parse_args()

    token = get_settings().api_access_token
    headers = {"Authorization": f"Bearer {token.get_secret_value()}"} if token else {}
    try:
        with httpx.Client(
            base_url="http://127.0.0.1:8000", headers=headers, trust_env=False, timeout=20
        ) as client:
            if args.command == "prepare":
                result = prepare(client, args.brand_id)
            else:
                result = export(client, args.campaign_id, args.job_id)
    except (httpx.HTTPError, ValueError, KeyError) as error:
        parser.exit(1, f"Benchmark {args.command} failed: {error}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
