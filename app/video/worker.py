"""Separate render queue with immutable inputs, a bounded subprocess and output checks."""

import json
import logging
import os
import shutil
import signal
import tempfile
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.creative.service import require
from app.db import session_factory
from app.errors import DomainError
from app.models import now
from app.video.models import RenderJob, VideoArtifact, VideoSpec
from app.video.process import run_local
from app.video.schemas import VideoPlan
from app.video.service import VideoService, sha256

logger = logging.getLogger(__name__)


class RemotionRenderer:
    def __init__(self, settings):
        self.settings = settings

    def render(self, plan, folder, assets, output):
        package = self.settings.render_package_path.resolve()
        browser = self.settings.render_browser_executable
        if not browser or not browser.is_file():
            raise DomainError("RENDER_BROWSER_MISSING", "Configure a local Chrome or Chromium", 503)
        # Pass only props the composition uses. The child receives no API keys.
        props = {
            **plan.model_dump(mode="json", exclude={"background", "products"}),
            "background_file": "background.mp4" if plan.background else None,
            "product_files": [
                {
                    "file": f"product-{i}.{scene.asset.extension}",
                    "start_frame": scene.start_frame,
                    "end_frame": scene.end_frame,
                }
                for i, scene in enumerate(plan.products)
            ],
        }
        (folder / "plan.json").write_text(plan.model_dump_json(indent=2))
        (folder / "props.json").write_text(json.dumps(props))
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(folder),
            "TMPDIR": str(folder),
            "TZ": "UTC",
            "REMOTION_DISABLE_TELEMETRY": "1",
        }
        run_local(
            [
                "node",
                "--import",
                "tsx",
                "src/render.ts",
                str(folder / "props.json"),
                str(output),
                str(assets),
                str(folder),
                str(browser.resolve()),
                str(self.settings.render_concurrency),
            ],
            self.settings.render_timeout_seconds,
            "RENDER_FAILED",
            "Remotion did not finish the video",
            cwd=package,
            env=env,
        )


class RenderWorker:
    """One render per worker at a time. Expired leases fail without an automatic retry."""

    def __init__(self, factory, settings, renderer=None, storage=None):
        self.factory = factory
        self.settings = settings
        self.renderer = renderer or RemotionRenderer(settings)
        self.storage = storage

    def claim(self):
        with self.factory.begin() as session:
            expired = session.scalars(
                select(RenderJob)
                .where(
                    RenderJob.status == "RUNNING",
                    RenderJob.lease_until < now(),
                )
                .with_for_update(skip_locked=True)
            )
            for job in expired:
                job.status, job.finished_at = "FAILED", now()
                job.error = {"code": "RENDER_LEASE_EXPIRED", "message": "Render worker stopped"}
                job.lease_token = job.lease_until = None
            job = session.scalar(
                select(RenderJob)
                .where(RenderJob.status == "PENDING")
                .order_by(RenderJob.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            job.status, job.started_at, job.lease_token = "RUNNING", now(), uuid.uuid4()
            job.lease_until = now() + timedelta(
                seconds=self.settings.render_timeout_seconds
                + 6 * self.settings.media_process_timeout_seconds
                + 30
            )
            return job.id, job.lease_token

    @staticmethod
    def owned(session, job_id, token):
        return session.scalar(
            select(RenderJob)
            .where(
                RenderJob.id == job_id,
                RenderJob.lease_token == token,
                RenderJob.status == "RUNNING",
                RenderJob.lease_until > now(),
            )
            .with_for_update()
        )

    def check_current(self, session, job, plan):
        spec = require(session, VideoSpec, job.spec_id, True)
        if spec.revision != job.spec_revision or spec.payload != job.inputs:
            raise DomainError("VIDEO_SPEC_STALE", "The VideoSpec changed during rendering", 409)
        service = VideoService(session, self.settings, self.storage)
        service.current(plan)
        return service

    def checkpoint(self, job_id, token, stage):
        logger.info("render_stage job_id=%s stage=%s", job_id, stage)
        with self.factory.begin() as session:
            job = self.owned(session, job_id, token)
            if not job:
                raise DomainError("RENDER_LEASE_LOST", "The render lease expired", 409)
            job.diagnostics = {**job.diagnostics, "stage": stage}

    def process_once(self):
        claim = self.claim()
        if claim is None:
            return False
        job_id, token = claim
        started = time.monotonic()
        stage = "preflight"
        try:
            with self.factory.begin() as session:
                job = self.owned(session, job_id, token)
                if job is None:
                    return True
                plan = VideoPlan.model_validate(job.inputs)
                service = self.check_current(session, job, plan)
            with tempfile.TemporaryDirectory(prefix="adjacent-render-") as temp:
                folder = Path(temp)
                stage = "staging"
                self.checkpoint(job_id, token, stage)
                assets = folder / "assets"
                assets.mkdir()
                inputs = [
                    (scene.asset, f"product-{i}.{scene.asset.extension}")
                    for i, scene in enumerate(plan.products)
                ]
                if plan.background:
                    inputs.append((plan.background, "background.mp4"))
                for asset, filename in inputs:
                    destination = assets / filename
                    with (
                        service.storage.read(asset.object_key) as src,
                        destination.open("wb") as dst,
                    ):
                        shutil.copyfileobj(src, dst, length=1024 * 1024)
                    if sha256(destination) != asset.render_hash:
                        raise DomainError(
                            "MEDIA_HASH_MISMATCH", "Media differs from the frozen input", 409
                        )
                path = folder / "output.mp4"
                stage = "rendering"
                self.checkpoint(job_id, token, stage)
                render_started = time.monotonic()
                self.renderer.render(plan, folder, assets, path)
                render_seconds = time.monotonic() - render_started
                stage = "validation"
                self.checkpoint(job_id, token, stage)
                validation = service.processor.validate_render(
                    path, plan.duration_frames, plan.audio == "BACKGROUND"
                )
                thumbnail = folder / "preview.jpg"
                service.processor.thumbnail(path, thumbnail)
                stage = "storage"
                self.checkpoint(job_id, token, stage)
                with self.factory.begin() as session:
                    job = self.owned(session, job_id, token)
                    if job is None:
                        return True
                    self.check_current(session, job, plan)
                    base = f"renders/{plan.brand_id}/{plan.campaign_id}/{job_id}"
                    key, preview = f"{base}/output.mp4", f"{base}/preview.jpg"
                    stored = []
                    try:
                        for source, object_key in [(path, key), (thumbnail, preview)]:
                            with source.open("rb") as handle:
                                service.storage.write(object_key, handle)
                            stored.append(object_key)
                        session.add(
                            VideoArtifact(
                                id=job_id,
                                output_key=key,
                                thumbnail_key=preview,
                                metadata_json={**validation, "sha256": sha256(path)},
                            )
                        )
                        job.status, job.finished_at = "SUCCEEDED", now()
                        job.lease_token = job.lease_until = None
                        job.diagnostics = {
                            "stage": "finished",
                            "render_seconds": round(render_seconds, 3),
                            "total_seconds": round(time.monotonic() - started, 3),
                            "file_bytes": path.stat().st_size,
                            "validation_passed": True,
                            "renderer_version": plan.renderer_version,
                            "build_revision": self.settings.app_build_revision or None,
                        }
                        session.flush()
                    except Exception:
                        for object_key in stored:
                            service.storage.delete(object_key)
                        raise
            logger.info("render_finished job_id=%s", job_id)
        except Exception as error:
            safe = (
                error
                if isinstance(error, DomainError)
                else DomainError("RENDER_INTERNAL", "Unexpected render failure")
            )
            logger.warning(
                "render_failed job_id=%s stage=%s code=%s type=%s",
                job_id,
                stage,
                safe.code,
                type(error).__name__,
            )
            with self.factory.begin() as session:
                job = self.owned(session, job_id, token)
                if job:
                    job.status, job.finished_at = "FAILED", now()
                    job.lease_token = job.lease_until = None
                    job.error = {"code": safe.code, "message": safe.message}
                    if safe.details:
                        job.error["details"] = safe.details
                    job.diagnostics = {
                        **job.diagnostics,
                        "failure_stage": stage,
                        "total_seconds": round(time.monotonic() - started, 3),
                    }
        return True


def main():
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    factory = session_factory(settings.database_url)
    worker = RenderWorker(factory, settings)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    try:
        while not stop.is_set():
            try:
                if worker.process_once():
                    continue
            except Exception as error:
                logger.error("render_poll_failed type=%s", type(error).__name__)
            stop.wait(settings.worker_poll_seconds)
    finally:
        factory.kw["bind"].dispose()


if __name__ == "__main__":
    main()
