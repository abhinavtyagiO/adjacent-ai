"""Capture public snapshots and compare staged extraction against saved Phase 1 baselines.

This evaluator NEVER calls an LLM. Snapshot replays use no website network calls. Cost rates are
explicit assumptions; reported source-backed coverage is NOT universal semantic precision.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.crawling.website import WebsiteSource
from app.enrichment import Document, SourceBundle
from app.errors import DomainError
from app.intelligence import run_pipeline, source_coverage
from app.provenance import leaves
from app.schemas import BIP, ExtractionResult, MarketingResult

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "fastlane": "https://www.usefastlane.ai/",
    "whole_truth": "https://thewholetruthfoods.com/",
    "newton": "https://www.newtonschool.co/",
}


def load_bundle(path):
    raw = json.loads(path.read_text())
    return SourceBundle(
        [
            Document(
                d["url"],
                d["text"],
                datetime.fromisoformat(d["fetched_at"]),
                d.get("title"),
                metadata=d.get("metadata", {}),
            )
            for d in raw["documents"]
        ],
        raw.get("warnings", []),
        raw.get("coverage", {}),
    )


def describe(profile, provenance=None):
    data = profile.model_dump()
    paths = leaves(data)
    roots = (
        "customer_personas_and_audience_segments",
        "customer_psychology",
        "brand_voice",
        "creative_context",
    )
    return {
        "offerings": len(profile.offerings),
        "named_offerings": [o.name for o in profile.offerings],
        "classified_offerings": sum(o.kind is not None for o in profile.offerings),
        "pricing_tiers": sum(len(o.pricing_tiers) for o in profile.offerings),
        "audience_creative_fields": len(leaves({k: data[k] for k in roots})),
        "populated_fields": len(paths),
        "inferred_fields": sum(p["origin"] == "INFERRED" for p in provenance)
        if provenance is not None
        else None,
        "fields_without_provenance": len(set(paths) - {p["path"] for p in provenance or []})
        if provenance is not None
        else None,
    }


async def main(args):
    settings = Settings(
        marketing_reasoning_effort=args.effort,
        factual_reasoning_effort="none",
        llm_input_usd_per_million=args.input_rate,
        llm_output_usd_per_million=args.output_rate,
        llm_cached_input_usd_per_million=args.cached_rate,
    )
    snapshots = ROOT / "evaluations" / "snapshots"
    snapshots.mkdir(exist_ok=True)
    baseline_data = json.loads((ROOT / "tests/fixtures/phase1b/phase1_snapshots.json").read_text())
    baselines = {row["website_url"]: row for row in baseline_data}
    report = {
        "label": "Offline revalidation of recorded model responses"
        if args.replay_saved
        else "Snapshot capture only",
        "effort": args.effort,
        "cases": {},
        "precision_note": (
            "Counts and citation/annotated checks are automatic; "
            "semantic factual precision and marketing usefulness require human "
            "review. No fabricated score is reported."
        ),
    }
    selected = args.cases.split(",")
    for name in selected:
        url = CASES[name]
        snapshot = snapshots / (name + ".json")
        if args.capture:
            bundle = await WebsiteSource(settings).collect(url)
            snapshot.write_text(
                json.dumps(
                    {
                        "website_url": url,
                        "captured_at": datetime.now(UTC).isoformat(),
                        "label": "Actual public crawl; raw facts not independently certified",
                        "documents": [
                            {
                                "url": d.url,
                                "text": d.text,
                                "title": d.title,
                                "fetched_at": d.fetched_at.isoformat(),
                                "metadata": d.metadata,
                            }
                            for d in bundle.documents
                        ],
                        "warnings": bundle.warnings,
                        "coverage": bundle.coverage,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n"
            )
        bundle = load_bundle(snapshot)
        row = {"source_coverage": source_coverage(bundle)}
        if url in baselines:
            old = baselines[url]
            row["baseline"] = describe(BIP.model_validate(old["baseline"]))
            row["baseline_coverage"] = source_coverage(
                SourceBundle(
                    [
                        Document(d["url"], d["text"], datetime.fromisoformat(d["fetched_at"]))
                        for d in old["documents"]
                    ]
                )
            )
        if args.replay_saved:
            record_dir = ROOT / "evaluations/results"
            fact_path = record_dir / (name + "-" + args.effort + "-facts.json")
            prior_path = record_dir / (name + "-" + args.effort + "-profile.json")
            if not fact_path.exists() or not prior_path.exists():
                row["replay_status"] = "NO_COMPLETE_RECORDING; no paid request made"
                report["cases"][name] = row
                continue
            facts = ExtractionResult.model_validate_json(fact_path.read_text())
            prior = json.loads(prior_path.read_text())

            class RecordedProvider:
                usage_records = []

                def __init__(self, facts, prior):
                    self.facts = facts
                    self.prior = prior

                async def extract_facts(self, website_url, sources):
                    return self.facts

                async def infer_marketing(self, profile, sources, selected_offering_key=None):
                    if self.prior["stages"]["marketing"] == "FAILED":
                        raise DomainError("RECORDED_FAILURE", "Recorded optional-stage failure")
                    data = self.prior["profile"]
                    roots = (
                        "customer_personas_and_audience_segments",
                        "customer_psychology",
                        "brand_voice",
                        "creative_context",
                    )
                    evidence = [
                        {k: p[k] for k in ("path", "origin", "reasoning", "citations")}
                        for p in self.prior["provenance"]
                        if p["path"].split("/")[1] in roots
                    ]
                    for entry in evidence:
                        entry["citations"] = [
                            {k: c[k] for k in ("source_url", "quote")} for c in entry["citations"]
                        ]
                    return MarketingResult(**{k: data[k] for k in roots}, evidence=evidence)

            result = await run_pipeline(RecordedProvider(facts, prior), settings, url, bundle)
            result.metadata["extraction_metrics"] = prior["extraction_metrics"]
            result.metadata["recorded_stages"] = prior["stages"]
            result.metadata["evidence_validation"]["facts"]["input_basis"] = (
                "SAVED_FACTUAL_RESPONSE"
            )
            result.metadata["evidence_validation"]["marketing"]["input_basis"] = (
                "PREVIOUSLY_RETAINED_FIELDS_ONLY"
            )
            result.metadata["replay_note"] = (
                "No new model calls. Audience replay uses only previously "
                "retained brand-level insights, not raw stage-2 output."
            )
            row.update(
                result=describe(result.profile, result.provenance),
                metadata=result.metadata,
                warnings=result.warnings,
            )
            detail = {
                "website_url": url,
                "profile": result.profile.model_dump(mode="json"),
                "provenance": result.provenance,
                "warnings": result.warnings,
                **result.metadata,
            }
            (ROOT / args.output).parent.mkdir(parents=True, exist_ok=True)
            (ROOT / args.output).with_name(name + "-" + args.effort + "-profile.json").write_text(
                json.dumps(detail, indent=2, ensure_ascii=False) + "\n"
            )
        report["cases"][name] = row
        target = ROOT / args.output
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(
            json.dumps(
                {
                    "case": name,
                    "pages": len(bundle.documents),
                    "result": row.get("result"),
                    "stages": row.get("metadata", {}).get("stages"),
                }
            ),
            flush=True,
        )

    target = ROOT / args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--replay-saved", action="store_true")
    parser.add_argument("--cases", default="fastlane,whole_truth,newton")
    parser.add_argument("--effort", choices=["none", "low", "high"], default="low")
    parser.add_argument("--input-rate", type=float, default=None)
    parser.add_argument("--output-rate", type=float, default=None)
    parser.add_argument("--cached-rate", type=float, default=None)
    parser.add_argument("--output", default="evaluations/replays/phase1b-low.json")
    asyncio.run(main(parser.parse_args()))
