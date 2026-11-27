"""Media and render tests use locally generated files and no model or stock requests."""

import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.creative.schemas import SingleCaption
from app.creative.service import require
from app.errors import DomainError
from app.main import create_app
from app.models import Brand
from app.video.models import VideoSpec
from app.video.process import MediaProcessor, run_local
from app.video.service import compile_captions
from app.video.storage import LocalStorage
from app.video.worker import RenderWorker
from tests.creative_helpers import MockCreativeTransport
from tests.test_creative_workflow import campaign, enqueue, seed

HEADERS = {"Authorization": "Bearer media-test-token"}


@pytest.fixture
def media_settings(factory, monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_STORAGE_PATH", str(tmp_path / "private"))
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def media_client(media_settings, monkeypatch):
    monkeypatch.setenv("API_ACCESS_TOKEN", "media-test-token")
    get_settings.cache_clear()
    with TestClient(create_app(), headers=HEADERS) as client:
        yield client


def create_clip(path, duration=16, codec="libx264"):
    run_local(
        [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=s=360x640:r=30:d={duration}",
            "-c:v",
            codec,
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        45,
        "FIXTURE_FAILED",
        "Could not generate the local test clip",
    )
    return path.read_bytes()


def test_private_storage_atomic_reads_and_key_isolation(tmp_path):
    from io import BytesIO

    storage = LocalStorage(tmp_path / "store")
    storage.write("media/brand/test.mp4", BytesIO(b"123456"))
    assert b"".join(storage.stream("media/brand/test.mp4", 1, 2)) == b"23"
    with pytest.raises(DomainError):
        storage.read("media/../test.mp4")
    storage.delete("media/brand/test.mp4")
    with pytest.raises(DomainError) as error:
        storage.size("media/brand/test.mp4")
    assert error.value.code == "MEDIA_FILE_MISSING"


def test_caption_frames_and_readability():
    frames = compile_captions(
        SingleCaption(
            treatment="SINGLE_CAPTION",
            text="Focus on one thing.",
            closing_brand_message="Opal",
        ),
        450,
    )
    assert frames[0]["start_frame"] == 0
    assert frames[0]["end_frame"] == frames[1]["start_frame"]
    assert frames[1]["end_frame"] == 450
    with pytest.raises(DomainError) as error:
        compile_captions(
            SingleCaption(treatment="SINGLE_CAPTION", text="A long phrase. " * 12), 150
        )
    assert error.value.code == "CAPTION_TOO_LONG"


def test_media_processing_normalizes_only_incompatible_inputs(tmp_path):
    source = tmp_path / "source.mp4"
    original = create_clip(source, codec="mpeg4", duration=2)
    metadata, derivative, thumb = MediaProcessor(30).process(source, tmp_path)
    assert derivative.is_file() and thumb.is_file()
    assert metadata["codec"] == "mpeg4" and metadata["normalized"]["codec"] == "h264"
    assert source.read_bytes() == original
    with pytest.raises(DomainError) as error:
        MediaProcessor(30).validate_render(derivative, 450, False)
    assert error.value.code == "INVALID_OUTPUT"


def test_media_processing_remuxes_compatible_quicktime(tmp_path):
    source = tmp_path / "source.mov"
    original = create_clip(source, duration=2)
    metadata, derivative, _ = MediaProcessor(30).process(source, tmp_path)
    assert metadata["extension"] == "mov"
    assert metadata["normalized"]["extension"] == "mp4"
    assert metadata["normalized"]["codec"] == metadata["codec"] == "h264"
    assert derivative.is_file()
    assert source.read_bytes() == original


def upload(client, brand, data):
    return client.post(
        f"/v1/brands/{brand}/media?purpose=BACKGROUND&confirm_rights=true",
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


@pytest.mark.integration
def test_media_upload_deduplication_auth_limits_and_brand_boundary(
    media_client,
    factory,
    tmp_path,
):
    first, _ = seed(factory)
    with factory.begin() as db:
        other = Brand(website_url="https://example.org/")
        db.add(other)
        db.flush()
        second = other.id
    data = create_clip(tmp_path / "fixture.mp4", duration=2)
    result = upload(media_client, first, data)
    assert result.status_code == 201, result.text
    asset = result.json()
    assert asset["details"]["width"] == 360
    assert asset["details"]["duration_seconds"] == 2
    assert not asset["has_normalized_derivative"]
    assert upload(media_client, first, data).json()["id"] == asset["id"]
    assert upload(media_client, second, data).json()["id"] != asset["id"]
    assert media_client.get(f"/v1/brands/{second}/media/{asset['id']}").status_code == 404
    assert (
        media_client.get(asset["thumbnail_url"], headers={"Authorization": ""}).status_code == 401
    )
    assert media_client.get(asset["thumbnail_url"]).headers["content-type"] == "image/jpeg"
    assert upload(media_client, first, b"not media").status_code == 422


async def approve_fixture(factory, client, settings):
    """Use mock Phase 2A output to exercise the existing campaign review workflow."""
    from app.creative.worker import CreativeWorker

    brand, _ = seed(factory)
    c = campaign(client, brand)
    j = enqueue(client, c["id"])
    assert await CreativeWorker(factory, settings, MockCreativeTransport(settings)).process_once()
    rows = client.get(f"/v1/campaigns/{c['id']}/concepts?job_id={j['id']}").json()["items"]
    concept = rows[0]
    response = client.post(
        f"/v1/concepts/{concept['id']}/approve",
        json={
            "expected_revision": 1,
            "human_review_completed": True,
            "acknowledge_claim_risks": True,
            "acknowledge_asset_requirements": True,
        },
    )
    assert response.status_code == 200, response.text
    return brand, response.json()


@pytest.mark.integration
async def test_spec_permissions_stale_claim_and_worker_failure(
    media_client, factory, settings, tmp_path
):
    brand, concept = await approve_fixture(factory, media_client, settings)
    data = create_clip(tmp_path / "fixture.mp4")
    asset = upload(media_client, brand, data).json()
    route = f"/v1/concepts/{concept['id']}/video-specs"
    payload = {
        "expected_concept_revision": concept["revision"],
        "background_asset_id": asset["id"],
        "confirm_media_use": True,
    }
    assert media_client.post(route, json={**payload, "confirm_media_use": False}).status_code == 422
    assert (
        media_client.post(route, json={**payload, "expected_concept_revision": 1}).status_code
        == 409
    )
    response = media_client.post(route, json=payload)
    assert response.status_code == 201, response.text
    spec = response.json()
    assert spec["payload"]["captions"][0]["text"] == concept["content"]["on_screen_copy"]["text"]
    job = media_client.post(
        f"/v1/video-specs/{spec['id']}/renders", json={"expected_revision": 1}
    ).json()
    assert job["status"] == "PENDING"
    assert (
        media_client.post(
            f"/v1/video-specs/{spec['id']}/renders", json={"expected_revision": 1}
        ).json()["id"]
        == job["id"]
    )
    with factory.begin() as db:
        row = require(db, VideoSpec, __import__("uuid").UUID(spec["id"]))
        row.revision += 1
    renderer = SimpleNamespace(render=lambda *_: pytest.fail("Stale work must not render"))
    worker = RenderWorker(factory, get_settings(), renderer=renderer)
    assert worker.process_once()
    failed = media_client.get(job["poll_url"]).json()
    assert failed["status"] == "FAILED" and failed["error"]["code"] == "VIDEO_SPEC_STALE"
    assert not worker.process_once()


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("RUN_REAL_RENDER") != "1", reason="Set RUN_REAL_RENDER=1")
async def test_real_text_over_broll_render_and_seek(media_client, factory, settings, tmp_path):
    brand, concept = await approve_fixture(factory, media_client, settings)
    asset = upload(media_client, brand, create_clip(tmp_path / "fixture.mp4")).json()
    response = media_client.post(
        f"/v1/concepts/{concept['id']}/video-specs",
        json={
            "expected_concept_revision": concept["revision"],
            "background_asset_id": asset["id"],
            "confirm_media_use": True,
            "brand_label": "Opal",
        },
    )
    assert response.status_code == 201, response.text
    job = media_client.post(
        f"/v1/video-specs/{response.json()['id']}/renders", json={"expected_revision": 1}
    ).json()
    assert RenderWorker(factory, get_settings()).process_once()
    done = media_client.get(job["poll_url"]).json()
    assert done["status"] == "SUCCEEDED", done
    assert done["artifact"]["metadata"]["decoded"]
    assert done["artifact"]["metadata"]["media"]["duration_seconds"] == 15
    assert done["artifact"]["metadata"]["media"]["audio_streams"] == 0
    played = media_client.get(done["artifact"]["play_url"], headers={"Range": "bytes=0-31"})
    assert played.status_code == 206 and len(played.content) == 32
    assert media_client.get(done["artifact"]["preview_url"]).status_code == 200


def create_photo(path, color):
    run_local(
        [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            f"color={color}:s=360x640",
            "-frames:v",
            "1",
            "-threads",
            "1",
            "-update",
            "1",
            str(path),
        ],
        20,
        "FIXTURE_FAILED",
        "Could not generate the local test image",
    )
    return path.read_bytes()


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("RUN_REAL_RENDER") != "1", reason="Set RUN_REAL_RENDER=1")
async def test_real_product_reveal_with_sequential_captions(
    media_client,
    factory,
    settings,
    tmp_path,
):
    brand, concept = await approve_fixture(factory, media_client, settings)
    target = f"/v1/concepts/{concept['id']}"
    approved = media_client.patch(
        target,
        json={
            "expected_revision": concept["revision"],
            "changes": {
                "on_screen_copy": {
                    "treatment": "SEQUENTIAL_CAPTIONS",
                    "captions": [
                        {"beat": "HOOK", "text": "A closer look."},
                        {"beat": "DEVELOPMENT", "text": "Meet the first look."},
                        {"beat": "PRODUCT_CONNECTION", "text": "Compare the next view."},
                        {"beat": "PAYOFF", "text": "Find the details you like."},
                    ],
                }
            },
        },
    )
    assert approved.status_code == 200, approved.text
    reviewed = media_client.post(
        target + "/approve",
        json={
            "expected_revision": approved.json()["revision"],
            "human_review_completed": True,
            "acknowledge_claim_risks": True,
            "acknowledge_asset_requirements": True,
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    photo_ids = []
    for i, color in enumerate(("teal", "gray")):
        response = media_client.post(
            f"/v1/brands/{brand}/media?purpose=PRODUCT&confirm_rights=true",
            content=create_photo(tmp_path / f"photo-{i}.png", color),
        )
        assert response.status_code == 201, response.text
        photo_ids.append(response.json()["id"])
    response = media_client.post(
        target + "/video-specs",
        json={
            "expected_concept_revision": reviewed.json()["revision"],
            "template": "PRODUCT_REVEAL",
            "product_asset_ids": photo_ids,
            "confirm_media_use": True,
            "fit": "contain",
        },
    )
    assert response.status_code == 201, response.text
    spec = response.json()
    assert len(spec["payload"]["captions"]) == 4 and len(spec["payload"]["products"]) == 2
    job = media_client.post(
        f"/v1/video-specs/{spec['id']}/renders", json={"expected_revision": 1}
    ).json()
    assert RenderWorker(factory, get_settings()).process_once()
    done = media_client.get(job["poll_url"]).json()
    assert done["status"] == "SUCCEEDED", done.get("error")
    assert done["artifact"]["metadata"]["decoded"]
    assert (
        media_client.get(done["artifact"]["download_url"])
        .headers["content-disposition"]
        .startswith("attachment")
    )
