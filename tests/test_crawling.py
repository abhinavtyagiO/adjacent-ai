from unittest.mock import AsyncMock

import httpx
import pytest

from app.crawling.http import PublicFetcher, resolve_public
from app.crawling.website import WebsiteSource, extract_html
from app.errors import DomainError


def test_extracts_readable_content_relevant_links_and_assets():
    text, title, links = extract_html(
        """<title>Acme</title><script>secret()</script>
        <meta name="theme-color" content="#123456"><img src="/logo.png" alt="Logo">
        <a href="/about">About</a><a href="https://external.example/product">Product</a>
        <a href="/blog">Blog</a><h1>Useful company details</h1>""",
        "https://acme.example/",
        1000,
    )
    assert title == "Acme" and "secret()" not in text
    assert "Logo URL: https://acme.example/logo.png" in text
    assert "https://acme.example/about" in links
    assert "https://acme.example/blog" not in links


async def test_dns_blocks_mixed_private_and_public_addresses(monkeypatch):
    import asyncio

    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(
            return_value=[
                (2, 1, 6, "", ("93.184.216.34", 443)),
                (2, 1, 6, "", ("127.0.0.1", 443)),
            ]
        ),
    )
    with pytest.raises(DomainError) as error:
        await resolve_public("acme.example", 443)
    assert error.value.code == "UNSAFE_URL"


async def test_request_pins_validated_ip_preserving_host_and_tls_name(settings, monkeypatch):
    monkeypatch.setattr("app.crawling.http.resolve_public", AsyncMock(return_value="93.184.216.34"))

    def handler(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "acme.example"
        assert request.extensions["sni_hostname"] == "acme.example"
        return httpx.Response(200, text="<h1>Acme</h1>", headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        page = await PublicFetcher(settings, client).fetch(
            "https://acme.example/", {"acme.example"}
        )
    assert page.url == "https://acme.example/"


@pytest.mark.parametrize("location", ["http://127.0.0.1/", "https://evil.example/product"])
async def test_redirects_cannot_escape_public_company_host(settings, monkeypatch, location):
    resolve = AsyncMock(return_value="93.184.216.34")
    monkeypatch.setattr("app.crawling.http.resolve_public", resolve)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(302, headers={"location": location})
        )
    ) as client:
        with pytest.raises(DomainError):
            await PublicFetcher(settings, client).fetch("https://acme.example/", {"acme.example"})
    assert resolve.await_count == 1


async def test_response_body_is_bounded(settings, monkeypatch):
    monkeypatch.setattr("app.crawling.http.resolve_public", AsyncMock(return_value="93.184.216.34"))
    settings.crawl_max_bytes = 1000
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=b"a" * 1001, headers={"content-type": "text/html"}
            )
        )
    ) as client:
        with pytest.raises(DomainError) as error:
            await PublicFetcher(settings, client).fetch("https://acme.example/", {"acme.example"})
    assert error.value.code == "PAGE_TOO_LARGE"


async def test_disallowed_redirect_is_never_requested(settings, monkeypatch):
    monkeypatch.setattr("app.crawling.http.resolve_public", AsyncMock(return_value="93.184.216.34"))
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "/private"})

    async def authorize(url):
        if url.endswith("/private"):
            raise DomainError("ROBOTS_DENIED", "Disallowed by robots")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DomainError) as error:
            await PublicFetcher(settings, client).fetch(
                "https://acme.example/", {"acme.example"}, authorize=authorize
            )
    assert len(calls) == 1 and error.value.code == "ROBOTS_DENIED"


async def test_site_crawl_respects_robots_and_discovers_pricing(settings, monkeypatch):
    from app.crawling.http import FetchedPage

    seen = []

    async def fetch(self, url, hosts, authorize=None):
        if authorize:
            await authorize(url)
        seen.append(url)
        if url.endswith("robots.txt"):
            return FetchedPage(url, "User-agent: *\nDisallow: /services", "text/plain")
        if url.endswith("/pricing"):
            return FetchedPage(
                url, "<h1>Pricing</h1><p>Our product costs $10 per month.</p>", "text/html"
            )
        return FetchedPage(
            url,
            '<h1>Acme makes software for teams.</h1><a href="/pricing">Pricing</a>'
            '<a href="https://evil.example/products">Products</a>',
            "text/html",
        )

    monkeypatch.setattr(PublicFetcher, "fetch", fetch)
    bundle = await WebsiteSource(settings).collect("https://acme.example/")
    assert any(d.url.endswith("/pricing") for d in bundle.documents)
    assert "https://acme.example/services" not in seen
    assert not any("evil.example" in url for url in seen)
    assert any("ROBOTS_DENIED" in warning for warning in bundle.warnings)
