"""Public-only HTTP transport with DNS pinning, redirect and response-size checks."""

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.config import Settings
from app.crawling.urls import normalize_url, public_ip
from app.errors import DomainError

logger = logging.getLogger(__name__)
USER_AGENT = "BrandIntelligenceBot/1.0"


@dataclass(frozen=True)
class FetchedPage:
    url: str
    body: str
    content_type: str


async def resolve_public(host: str, port: int) -> str:
    try:
        records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise DomainError("DNS_FAILED", "Website DNS resolution failed", retryable=True) from exc
    addresses = {record[4][0] for record in records}
    if not addresses or any(not public_ip(address) for address in addresses):
        raise DomainError("UNSAFE_URL", "Website resolves to a private or reserved address")
    # Connect to this exact validated IP; HTTPX does not re-resolve the original hostname.
    return sorted(addresses, key=lambda ip: (":" in ip, ip))[0]


class PublicFetcher:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    async def fetch(
        self,
        url: str,
        allowed_hosts: set[str],
        authorize: Callable[[str], Awaitable[None]] | None = None,
    ) -> FetchedPage:
        for attempt in range(2):
            try:
                async with asyncio.timeout(self.settings.crawl_timeout_seconds):
                    return await self._fetch(url, allowed_hosts, authorize)
            except (httpx.TransportError, TimeoutError) as exc:
                error = DomainError(
                    "CRAWL_TIMEOUT", "Website request failed or timed out", retryable=True
                )
                error.__cause__ = exc
            except DomainError as exc:
                error = exc
            if not error.retryable or attempt == 1:
                raise error
            await asyncio.sleep(0.5 * (attempt + 1))
        raise AssertionError("unreachable")

    async def _fetch(
        self,
        url: str,
        allowed_hosts: set[str],
        authorize: Callable[[str], Awaitable[None]] | None = None,
    ) -> FetchedPage:
        for _ in range(6):
            try:
                url = normalize_url(url)
            except ValueError as exc:
                raise DomainError("UNSAFE_URL", "Invalid website or redirect URL") from exc
            parsed = urlsplit(url)
            if parsed.hostname not in allowed_hosts:
                raise DomainError("OFFSITE_REDIRECT", "Cross-site redirect was blocked")
            if authorize:
                await authorize(url)
            ip = await resolve_public(parsed.hostname, 443 if parsed.scheme == "https" else 80)
            target = httpx.URL(url).copy_with(host=ip)
            async with self.client.stream(
                "GET",
                target,
                headers={"Host": parsed.netloc, "User-Agent": USER_AGENT},
                extensions={"sni_hostname": parsed.hostname},
                follow_redirects=False,
                timeout=self.settings.crawl_timeout_seconds,
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise DomainError("CRAWL_REDIRECT", "Website returned an invalid redirect")
                    url = urljoin(url, location)
                    continue
                if response.status_code >= 400:
                    raise DomainError(
                        "CRAWL_HTTP_ERROR",
                        f"Website returned HTTP {response.status_code}",
                        retryable=response.status_code >= 500 or response.status_code == 429,
                    )
                media_type = response.headers.get("content-type", "").split(";")[0].lower()
                if media_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                    raise DomainError("UNSUPPORTED_CONTENT", "Website response is not HTML or text")
                chunks = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65_536):
                    chunks.extend(chunk)
                    if len(chunks) > self.settings.crawl_max_bytes:
                        raise DomainError("PAGE_TOO_LARGE", "Website page exceeds the size limit")
                return FetchedPage(url, chunks.decode("utf-8", errors="replace"), media_type)
        raise DomainError("CRAWL_REDIRECT", "Website exceeded the redirect limit")
