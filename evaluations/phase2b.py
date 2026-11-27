"""Render a saved Phase 2A shortlist entry with locally generated test media."""

import argparse
import json
import logging
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi.encoders import jsonable_encoder
from sqlalchemy.engine import make_url

from app.config import Settings
from app.creative.models import Campaign, CreativeConcept, CreativeJob, ResearchSnapshot
from app.creative.schemas import ConceptPatch, ConceptSelection
from app.creative.service import CampaignService, view
from app.db import session_factory
from app.models import Brand, ExtractionJob
from app.video.models import RenderJob, VideoArtifact
from app.video.routes import asset_view, job_view
from app.video.schemas import CreateVideoSpec
from app.video.service import VideoService, sha256
from app.video.storage import LocalStorage
from app.video.worker import RenderWorker


def write_json(path, data):
    path.write_text(json.dumps(jsonable_encoder(data), ensure_ascii=False, indent=2) + "\n")


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", *args], check=True, timeout=90)


def generate_media(template, target, duration):
    """Generate geometric test visuals. None of the images depict a branded product."""
    if template == "TEXT_OVER_BROLL":
        video = target / "test-background.mp4"
        ffmpeg(
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=s=540x960:r=30:d={duration + 1}",
            "-vf",
            "hue=s=0.18,eq=brightness=-0.14",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        )
        return [video]
    colors = [("0x152a3a", "0x45888b"), ("0x392739", "0xd48971"), ("0x222b3f", "0x827ed0")]
    paths = []
    for index, (background, accent) in enumerate(colors):
        path = target / f"placeholder-{index + 1}.png"
        ffmpeg(
            "-f",
            "lavfi",
            "-i",
            f"color={background}:s=540x960",
            "-vf",
            f"drawgrid=w=45:h=45:t=1:c=white@0.14,"
            f"drawbox=x=105:y={172 + index * 54}:w=330:h=390:color={accent}:t=fill,"
            "drawbox=x=125:y=180:w=290:h=380:color=white@0.55:t=2",
            "-frames:v",
            "1",
            "-c:v",
            "png",
            "-threads",
            "1",
            str(path),
        )
        paths.append(path)
    return paths


def clone_concept(session, report, entry, label):
    """Copy the saved concept and its research binding into a disposable database."""
    binding = report["research_binding"]
    brand = Brand(
        website_url=f"https://phase2b-{uuid.uuid4().hex}.example/",
        name=label,
        revision=binding["brand_revision"],
    )
    session.add(brand)
    session.flush()
    extraction = ExtractionJob(brand_id=brand.id, base_revision=brand.revision, status="SUCCEEDED")
    campaign = Campaign(
        brand_id=brand.id,
        revision=binding["campaign_revision"],
        brief=report["generator_research"]["brief"],
    )
    session.add_all([extraction, campaign])
    session.flush()
    research = ResearchSnapshot(
        campaign_id=campaign.id,
        brand_revision=brand.revision,
        campaign_revision=campaign.revision,
        source_job_id=extraction.id,
        payload=report["generator_research"],
    )
    session.add(research)
    session.flush()
    job = CreativeJob(
        campaign_id=campaign.id,
        research_id=research.id,
        status="SUCCEEDED",
        model="saved-benchmark-fixture",
    )
    session.add(job)
    session.flush()
    concept = CreativeConcept(
        campaign_id=campaign.id,
        job_id=job.id,
        candidate_key=entry["candidate_key"],
        revision=entry["revision"],
        content=entry["content"],
        original_content=entry["original_content"],
        validation=entry["validation"],
        evaluation=entry["evaluation"],
        shortlisted=True,
    )
    session.add(concept)
    session.commit()
    return brand.id, concept.id


def render(args, report, entry, settings):
    factory = session_factory(settings.database_url)
    storage = LocalStorage(settings.media_storage_path)
    video_background = args.template == "TEXT_OVER_BROLL"
    media_label = "test footage" if video_background else "placeholders"
    source_dir = args.output / "test-media"
    source_dir.mkdir()
    paths = generate_media(
        args.template, source_dir, report["generator_research"]["brief"]["duration_seconds"]
    )
    copy = entry["content"]
    legacy = not copy.get("on_screen_copy")
    label = f"{args.label} · {media_label}"
    with factory() as session:
        brand_id, concept_id = clone_concept(session, report, entry, label)
        # Add and review explicit captions only on the benchmark clone.
        campaigns = CampaignService(session, settings)
        revision = entry["revision"]
        if legacy:
            updated = campaigns.update_concept(
                concept_id,
                ConceptPatch(
                    expected_revision=revision,
                    changes={
                        "on_screen_copy": {
                            "treatment": "SINGLE_CAPTION",
                            "text": copy["primary_hook"],
                        }
                    },
                ),
            )
            if not updated.validation.get("eligible"):
                raise RuntimeError("The saved hook needs review before using it as a caption")
            revision = updated.revision
        approved = campaigns.select(
            concept_id,
            ConceptSelection(
                expected_revision=revision,
                human_review_completed=True,
                acknowledge_claim_risks=True,
                acknowledge_asset_requirements=True,
            ),
            approve=True,
        )
        service = VideoService(session, settings, storage)
        assets = [
            service.ingest(
                brand_id,
                path,
                "BACKGROUND" if video_background else "PRODUCT",
                "Synthetic benchmark fixture",
                ["offline-test"],
            )
            for path in paths
        ]
        spec = service.create_spec(
            concept_id,
            CreateVideoSpec(
                expected_concept_revision=approved.revision,
                template=args.template,
                background_asset_id=assets[0].id if video_background else None,
                product_asset_ids=[] if video_background else [asset.id for asset in assets],
                confirm_media_use=True,
                brand_label=label if len(label) <= 40 else None,
            ),
        )
        write_json(args.output / "video-spec.json", view(spec))
        write_json(
            args.output / "media.json",
            [
                {"asset": asset_view(asset), "input": path.name}
                for asset, path in zip(assets, paths, strict=True)
            ],
        )
        job_id = service.enqueue(spec.id, spec.revision).id

    RenderWorker(factory, settings, storage=storage).process_once()
    with factory() as session:
        job = session.get(RenderJob, job_id)
        write_json(args.output / "render-job.json", job_view(job, session))
        if job.status != "SUCCEEDED":
            raise RuntimeError(f"Render {job.id} finished as {job.status}: {job.error}")
        artifact = session.get(VideoArtifact, job_id)
        for name, key in [
            ("render.mp4", artifact.output_key),
            ("preview.jpg", artifact.thumbnail_key),
        ]:
            with storage.read(key) as source, (args.output / name).open("wb") as destination:
                shutil.copyfileobj(source, destination)

    video = args.output / "render.mp4"
    ffprobe = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video)],
        timeout=30,
    )
    probe = json.loads(ffprobe)
    probe["format"]["filename"] = video.name
    write_json(args.output / "ffprobe.json", probe)
    for second in (2, 7, 12):
        if second < spec.payload["duration_frames"] / spec.payload["fps"]:
            ffmpeg(
                "-ss",
                str(second),
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-vf",
                "scale=540:960",
                str(args.output / f"frame-{second:02d}s.jpg"),
            )
    write_json(
        args.output / "benchmark.json",
        {
            "brand": args.label,
            "source_export": args.input.name,
            "source_export_sha256": sha256(args.input),
            "source_concept": entry,
            "original_data_preserved": True,
            "offline_fixture": True,
            "media": "synthetic test footage"
            if video_background
            else "geometric image placeholders",
            "legacy_caption_uses_saved_hook": legacy,
            "inference_calls": 0,
            "render_job_id": job_id,
            "files": sorted(file.name for file in args.output.iterdir()),
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Saved Phase 2A benchmark JSON")
    parser.add_argument("--output", required=True, type=Path, help="New directory for this export")
    parser.add_argument("--label", required=True)
    parser.add_argument("--template", choices=["TEXT_OVER_BROLL", "PRODUCT_REVEAL"], required=True)
    parser.add_argument("--candidate", type=int, default=1, help="One-based shortlist position")
    args = parser.parse_args()
    settings = Settings(_env_file=None)
    database = make_url(settings.database_url)
    if not database.database or not database.database.endswith("_benchmark_test"):
        parser.error("Set DATABASE_URL to a fresh PostgreSQL database ending in _benchmark_test")
    if not settings.render_browser_executable:
        parser.error("Set RENDER_BROWSER_EXECUTABLE to a Chrome or Chromium binary")
    report = json.loads(args.input.read_text())
    if not 1 <= args.candidate <= len(report["shortlist"]):
        parser.error("The candidate must be a position in the saved shortlist")
    if args.output.exists():
        parser.error("--output must be a new directory")
    args.output.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO)
    with tempfile.TemporaryDirectory(prefix="adjacent-video-benchmark-") as storage:
        settings.media_storage_path = Path(storage)
        render(args, report, report["shortlist"][args.candidate - 1], settings)


if __name__ == "__main__":
    main()
