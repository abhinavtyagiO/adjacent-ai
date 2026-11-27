"""Pixabay-only discovery and explicit downloads. URLs never come from an import request."""

import hashlib
import json
import logging
import re
import socket
import time
from datetime import timedelta
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import delete, select, text

from app.crawling.urls import public_ip
from app.errors import DomainError
from app.models import now
from app.video.models import MediaAsset, MediaSearchCache

logger = logging.getLogger(__name__)
HOSTS = {"pixabay.com", "www.pixabay.com", "cdn.pixabay.com"}
LICENSE = "https://pixabay.com/service/license-summary/"
CONTENT_TYPES = {
    "VIDEO": {"video/mp4", "video/webm"},
    "IMAGE": {"image/jpeg", "image/png", "image/webp"},
}


def allowed_url(value):
    """Only HTTPS URLs on Pixabay's exact host list can reach the downloader."""
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or any(char.isspace() or ord(char) < 32 for char in value)
    ):
        return False
    try:
        url = urlsplit(value)
        return (
            url.scheme == "https"
            and url.hostname in HOSTS
            and url.port in (443, None)
            and url.username is None
            and url.password is None
            and not url.fragment
            and not url.path.startswith("/api")
        )
    except ValueError:
        return False


def public_address(host):
    try:
        addresses = {row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise DomainError(
            "PIXABAY_DNS_FAILED", "Pixabay download host could not be resolved", 502
        ) from exc
    if not addresses or not all(public_ip(address) for address in addresses):
        raise DomainError("UNSAFE_MEDIA_URL", "Pixabay download host did not resolve publicly", 422)
    return sorted(addresses, key=lambda ip: (":" in ip, ip))[0]


def canonical_query(query):
    return " ".join(query.casefold().split())


def parse_candidates(payload, media_type, max_bytes):
    """Convert documented image or video fields to one safe, bounded candidate shape."""
    if not isinstance(payload, dict) or not isinstance(payload.get("hits"), list):
        raise DomainError(
            "PIXABAY_RESPONSE_INVALID", "Pixabay returned an unexpected search result", 502
        )
    candidates = []
    for hit in payload["hits"][:40]:
        if (
            not isinstance(hit, dict)
            or type(hit.get("id")) is not int
            or hit["id"] <= 0
            or not allowed_url(hit.get("pageURL"))
        ):
            continue
        if media_type == "IMAGE":
            # Optional original and large renditions are not always available.
            width = hit.get("imageWidth")
            height = hit.get("imageHeight")
            url = hit.get("largeImageURL")
            if (
                allowed_url(url)
                and type(width) is int
                and type(height) is int
                and min(width, height) > 0
            ):
                scale = min(1, 1280 / max(width, height))
                width, height = int(width * scale), int(height * scale)
            else:
                url = hit.get("webformatURL")
                width, height = hit.get("webformatWidth"), hit.get("webformatHeight")
            preview, duration = hit.get("previewURL"), None
        else:
            variants = hit.get("videos")
            if not isinstance(variants, dict):
                continue
            rendition = next(
                (
                    row
                    for size in ("large", "medium", "small", "tiny")
                    if isinstance(row := variants.get(size), dict)
                    and allowed_url(row.get("url"))
                    and type(row.get("size")) is int
                    and 0 < row["size"] <= max_bytes
                    and type(row.get("width")) is int
                    and type(row.get("height")) is int
                    and max(row["width"], row["height"]) <= 4096
                ),
                None,
            )
            if not rendition or type(hit.get("duration")) is not int or hit["duration"] <= 0:
                continue
            url, preview = rendition["url"], rendition.get("thumbnail")
            width, height, duration = rendition["width"], rendition["height"], hit["duration"]
        if (
            not allowed_url(url)
            or type(width) is not int
            or type(height) is not int
            or not (0 < width <= 4096 and 0 < height <= 4096)
        ):
            continue
        tags = [tag.strip()[:100] for tag in str(hit.get("tags", "")).split(",") if tag.strip()][
            :20
        ]
        candidates.append(
            {
                "provider": "PIXABAY",
                "provider_asset_id": str(hit["id"]),
                "media_type": media_type,
                "source_url": hit["pageURL"],
                "download_url": url,
                "preview_url": preview if allowed_url(preview) else None,
                "creator": str(hit.get("user") or "")[:120] or None,
                "license_name": "Pixabay Content License",
                "license_url": LICENSE,
                "width": width,
                "height": height,
                "duration_seconds": duration,
                "tags": tags,
                "orientation": "PORTRAIT"
                if height > width
                else "LANDSCAPE"
                if width > height
                else "SQUARE",
            }
        )
    return candidates


class PixabayMediaProvider:
    """Use a shared search cache and provider rate window. Downloads have no API credential."""

    def __init__(self, settings, client=None):
        self.key = settings.pixabay_api_key
        self.max_bytes = settings.media_max_bytes
        self.client = client

    def _client(self):
        from contextlib import nullcontext

        return (
            nullcontext(self.client)
            if self.client
            else httpx.Client(
                timeout=8,
                follow_redirects=False,
                trust_env=False,
            )
        )

    def _request_search(self, query, media_type):
        if not self.key:
            raise DomainError(
                "PIXABAY_NOT_CONFIGURED", "Set PIXABAY_API_KEY to search stock media", 503
            )
        endpoint = (
            "https://pixabay.com/api/videos/"
            if media_type == "VIDEO"
            else "https://pixabay.com/api/"
        )
        params = {
            "key": self.key.get_secret_value(),
            "q": query,
            "safesearch": "true",
            "per_page": 40,
        }
        if media_type == "IMAGE":
            params["image_type"] = "photo"
        try:
            with (
                self._client() as client,
                client.stream("GET", endpoint, params=params) as response,
            ):
                if response.status_code == 429:
                    logger.warning("pixabay_rate_limited")
                    reset = response.headers.get("X-RateLimit-Reset", "60")
                    wait = (
                        int(reset)
                        if reset.isascii() and reset.isdecimal() and len(reset) < 6
                        else 60
                    )
                    raise DomainError(
                        "PIXABAY_RATE_LIMITED",
                        "Pixabay rate limit reached",
                        429,
                        details={"retry_after_seconds": max(1, min(wait, 3600))},
                    )
                if response.status_code != 200:
                    raise DomainError("PIXABAY_SEARCH_FAILED", "Pixabay rejected the search", 502)
                remaining = response.headers.get("X-RateLimit-Remaining")
                reset = response.headers.get("X-RateLimit-Reset", "60")
                raw = bytearray()
                deadline = time.monotonic() + 12
                for chunk in response.iter_bytes(65536):
                    raw.extend(chunk)
                    if len(raw) > 1024 * 1024 or time.monotonic() > deadline:
                        raise DomainError(
                            "PIXABAY_RESPONSE_LIMIT", "Pixabay search exceeded its limit", 502
                        )
        except httpx.HTTPError as exc:
            raise DomainError(
                "PIXABAY_SEARCH_FAILED", "Pixabay search is unavailable", 502
            ) from exc
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise DomainError(
                "PIXABAY_RESPONSE_INVALID", "Pixabay returned invalid JSON", 502
            ) from exc
        cooldown = 0
        if remaining == "0":
            cooldown = (
                int(reset) if reset.isascii() and reset.isdecimal() and len(reset) < 6 else 60
            )
            cooldown = max(1, min(cooldown, 3600))
        return parse_candidates(payload, media_type, self.max_bytes), cooldown

    def search(self, session, query, media_type):
        query = canonical_query(query)
        search_id = hashlib.sha256(f"pixabay/v1/{media_type}/{query}".encode()).hexdigest()
        # Equivalent misses are serialized across API processes for the bounded provider request.
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": search_id}
        )
        row = session.get(MediaSearchCache, search_id)
        current = now()
        if row and row.expires_at > current:
            candidates = row.payload["items"]
            session.commit()
            logger.info("media_search source=pixabay cache_hit=true results=%s", len(candidates))
            return search_id, candidates, True
        if not self.key:
            raise DomainError(
                "PIXABAY_NOT_CONFIGURED", "Set PIXABAY_API_KEY to search stock media", 503
            )
        # Serialize outbound searches and leave a margin below the provider limit.
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pixabay-rate'))"))
        rate = session.get(MediaSearchCache, "pixabay-rate")
        active = rate and rate.expires_at > current
        count = rate.payload["count"] if active else 0
        if active and (count >= 90 or rate.payload.get("blocked")):
            raise DomainError(
                "PIXABAY_RATE_LIMITED",
                "Wait before making another stock search",
                429,
                details={
                    "retry_after_seconds": max(1, int((rate.expires_at - current).total_seconds()))
                },
            )
        window_end = rate.expires_at if active else current + timedelta(seconds=60)
        session.merge(
            MediaSearchCache(
                key="pixabay-rate", payload={"count": count + 1}, expires_at=window_end
            )
        )
        # Keep the equivalent search lock until its result is stored. No paid retry.
        try:
            candidates, cooldown = self._request_search(query, media_type)
        except DomainError as exc:
            if exc.status == 429:
                wait = (exc.details or {}).get("retry_after_seconds", 60)
                session.merge(
                    MediaSearchCache(
                        key="pixabay-rate",
                        payload={"count": count + 1, "blocked": True},
                        expires_at=now() + timedelta(seconds=wait),
                    )
                )
            session.commit()
            raise
        if cooldown:
            session.merge(
                MediaSearchCache(
                    key="pixabay-rate",
                    payload={"count": count + 1, "blocked": True},
                    expires_at=now() + timedelta(seconds=cooldown),
                )
            )
        session.execute(
            delete(MediaSearchCache).where(
                MediaSearchCache.expires_at < current - timedelta(days=1)
            )
        )
        session.merge(
            MediaSearchCache(
                key=search_id, payload={"items": candidates}, expires_at=now() + timedelta(hours=24)
            )
        )
        session.commit()
        logger.info("media_search source=pixabay cache_hit=false results=%s", len(candidates))
        return search_id, candidates, False

    def download(self, candidate, destination):
        """Pin the connection to public DNS for each hop and cap the streamed file."""
        url, media_type = candidate["download_url"], candidate["media_type"]
        deadline = time.monotonic() + 35
        try:
            with self._client() as client:
                for _ in range(3):
                    if not allowed_url(url):
                        raise DomainError(
                            "UNSAFE_MEDIA_URL", "Pixabay download URL was blocked", 422
                        )
                    host = urlsplit(url).hostname
                    target = httpx.URL(url).copy_with(host=public_address(host))
                    with client.stream(
                        "GET", target, headers={"Host": host}, extensions={"sni_hostname": host}
                    ) as response:
                        if time.monotonic() > deadline:
                            raise DomainError(
                                "PIXABAY_DOWNLOAD_TIMEOUT", "Stock download took too long", 504
                            )
                        if response.status_code in {301, 302, 303, 307, 308}:
                            url = urljoin(url, response.headers.get("location", ""))
                            continue
                        if response.status_code != 200:
                            raise DomainError(
                                "PIXABAY_DOWNLOAD_FAILED",
                                "The selected stock asset is unavailable",
                                502,
                            )
                        kind = (
                            response.headers.get("content-type", "").split(";")[0].strip().lower()
                        )
                        if kind not in CONTENT_TYPES[media_type]:
                            raise DomainError(
                                "PIXABAY_MEDIA_TYPE",
                                "Stock response is not the selected media type",
                                422,
                            )
                        size = response.headers.get("content-length", "")
                        if re.fullmatch(r"[0-9]{1,18}", size) and int(size) > self.max_bytes:
                            raise DomainError(
                                "MEDIA_SIZE", "Stock asset exceeds the upload limit", 413
                            )
                        count = 0
                        with destination.open("wb") as target_file:
                            for chunk in response.iter_bytes(65536):
                                count += len(chunk)
                                if count > self.max_bytes:
                                    raise DomainError(
                                        "MEDIA_SIZE", "Stock asset exceeds the upload limit", 413
                                    )
                                if time.monotonic() > deadline:
                                    raise DomainError(
                                        "PIXABAY_DOWNLOAD_TIMEOUT",
                                        "Stock download took too long",
                                        504,
                                    )
                                target_file.write(chunk)
                        return
        except httpx.HTTPError as exc:
            raise DomainError(
                "PIXABAY_DOWNLOAD_FAILED", "The selected stock asset could not be downloaded", 502
            ) from exc
        raise DomainError(
            "PIXABAY_REDIRECT_LIMIT", "Stock download returned too many redirects", 502
        )


def existing_stock(session, brand_id, media_type, provider_asset_id):
    """An earlier permitted import can be reused without another provider request."""
    return session.scalar(
        select(MediaAsset)
        .where(
            MediaAsset.brand_id == brand_id,
            MediaAsset.media_type == media_type,
            MediaAsset.source_type == "PIXABAY",
            MediaAsset.status == "READY",
            MediaAsset.source_identity == f"{media_type}:{provider_asset_id}",
            MediaAsset.details["usage_permission"].astext == "CAMPAIGN_RENDER",
        )
        .order_by(MediaAsset.created_at)
        .limit(1)
    )
