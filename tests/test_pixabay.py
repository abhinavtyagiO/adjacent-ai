"""Stock-provider behavior is verified with local media and mocked HTTP only."""

from datetime import timedelta

import httpx
import pytest

from app.config import get_settings
from app.errors import DomainError
from app.models import now
from app.video.models import MediaSearchCache
from app.video.pixabay import PixabayMediaProvider, parse_candidates
from tests.test_creative_workflow import seed
from tests.test_video import (
    create_clip,
)
from tests.test_video import (
    media_client as local_client,
)
from tests.test_video import (
    media_settings as local_settings,
)

media_settings = local_settings
media_client = local_client


def video_hit():
    return {
        "id": 11,
        "pageURL": "https://pixabay.com/videos/id-11/",
        "user": "Author",
        "tags": "study, desk",
        "duration": 16,
        "videos": {
            "medium": {
                "url": "https://cdn.pixabay.com/video/11.mp4",
                "thumbnail": "https://cdn.pixabay.com/video/11.jpg",
                "width": 360,
                "height": 640,
                "size": 10000,
            }
        },
    }


def test_pixabay_image_and_video_parsing():
    image = {
        "id": 5,
        "pageURL": "https://pixabay.com/photos/id-5/",
        "user": "Photographer",
        "tags": "desk, writing",
        "previewURL": "https://cdn.pixabay.com/photo/example.jpg",
        "webformatURL": "https://pixabay.com/get/example.jpg",
        "webformatWidth": 640,
        "webformatHeight": 480,
    }
    rows = parse_candidates(
        {"hits": [image, {**image, "pageURL": "https://127.0.0.1/a"}]}, "IMAGE", 50000
    )
    assert len(rows) == 1 and rows[0]["orientation"] == "LANDSCAPE"
    rows = parse_candidates({"hits": [video_hit()]}, "VIDEO", 50000)
    assert rows[0]["duration_seconds"] == 16 and rows[0]["orientation"] == "PORTRAIT"
    with pytest.raises(DomainError, match="unexpected"):
        parse_candidates({"hits": None}, "IMAGE", 50000)


def test_download_rejects_redirect_and_oversized_body(tmp_path, media_settings, monkeypatch):
    monkeypatch.setattr("app.video.pixabay.public_address", lambda _host: "8.8.8.8")
    candidate = parse_candidates({"hits": [video_hit()]}, "VIDEO", 50000)[0]
    redirect = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"Location": "https://169.254.169.254/secret"})
    )
    with httpx.Client(transport=redirect) as client:
        with pytest.raises(DomainError) as caught:
            PixabayMediaProvider(media_settings, client).download(candidate, tmp_path / "bad")
        assert caught.value.code == "UNSAFE_MEDIA_URL"
    oversized = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"Content-Type": "video/mp4", "Content-Length": "999999999"}
        )
    )
    with httpx.Client(transport=oversized) as client:
        with pytest.raises(DomainError) as caught:
            PixabayMediaProvider(media_settings, client).download(candidate, tmp_path / "bad")
        assert caught.value.code == "MEDIA_SIZE"


@pytest.mark.integration
def test_cached_stock_search_selected_import_and_internal_reuse(
    media_client,
    factory,
    tmp_path,
    monkeypatch,
):
    brand, _ = seed(factory)
    monkeypatch.setenv("PIXABAY_API_KEY", "mock-only")
    get_settings.cache_clear()
    calls = []
    clip = create_clip(tmp_path / "fixture.mp4")

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/videos/":
            return httpx.Response(200, json={"hits": [video_hit()]})
        assert request.headers["Host"] == "cdn.pixabay.com" and "key" not in request.url.params
        return httpx.Response(200, headers={"Content-Type": "video/mp4"}, content=clip)

    monkeypatch.setattr("app.video.pixabay.public_address", lambda _host: "8.8.8.8")
    path = f"/v1/brands/{brand}/media"
    with httpx.Client(transport=httpx.MockTransport(handler)) as provider_http:
        monkeypatch.setattr(
            "app.video.routes.PixabayMediaProvider",
            lambda settings: PixabayMediaProvider(settings, provider_http),
        )
        params = {
            "q": "Study Desk",
            "media_type": "VIDEO",
            "search_pixabay": "true",
            "orientation": "PORTRAIT",
            "min_duration": 15,
        }
        first = media_client.get(path + "/search", params=params).json()
        second = media_client.get(path + "/search", params={**params, "q": " study  desk "}).json()
        assert first["items"][0]["provider_asset_id"] == "11"
        assert not first["cache_hit"] and second["cache_hit"]
        assert first["search_id"] == second["search_id"] and calls == ["/api/videos/"]
        assert "download_url" not in first["items"][0]
        with factory() as db:
            cache = db.get(MediaSearchCache, first["search_id"])
            assert cache.expires_at >= now() + timedelta(hours=23, minutes=59)
        choice = {
            "search_id": first["search_id"],
            "provider_asset_id": "11",
            "media_type": "VIDEO",
            "confirm_rights": True,
        }
        first_import = media_client.post(path + "/pixabay", json=choice)
        assert first_import.status_code == 201, first_import.text
        assert first_import.json()["details"]["creator"] == "Author"
        assert (
            media_client.post(path + "/pixabay", json=choice).json()["id"]
            == first_import.json()["id"]
        )
        assert media_client.get(path + "/search", params=params).json()["source"] == "INTERNAL"
        assert calls == ["/api/videos/", "/video/11.mp4"]


@pytest.mark.integration
def test_missing_key_and_provider_rate_limit_are_safe(media_client, factory, monkeypatch):
    brand, _ = seed(factory)
    path = f"/v1/brands/{brand}/media/search"
    params = {"q": "desk", "media_type": "IMAGE", "search_pixabay": True}
    monkeypatch.setenv("PIXABAY_API_KEY", "")
    get_settings.cache_clear()
    assert media_client.get(path, params=params).json()["error"]["code"] == "PIXABAY_NOT_CONFIGURED"
    monkeypatch.setenv("PIXABAY_API_KEY", "private-test")
    get_settings.cache_clear()
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(429, text="private-test", headers={"X-RateLimit-Reset": "30"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as upstream:
        monkeypatch.setattr(
            "app.video.routes.PixabayMediaProvider",
            lambda settings: PixabayMediaProvider(settings, upstream),
        )
        first = media_client.get(path, params=params)
        second = media_client.get(path, params={**params, "q": "another desk"})
        assert first.status_code == second.status_code == 429
        assert "private-test" not in first.text
        assert calls == ["/api/"]
