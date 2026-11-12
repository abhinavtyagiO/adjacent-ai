import asyncio
import logging
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from app.config import Settings
from app.crawling.http import PublicFetcher
from app.crawling.urls import normalize_url
from app.enrichment import Document, SourceBundle
from app.errors import DomainError
from app.models import now

logger = logging.getLogger(__name__)
BOT = "BrandIntelligenceBot"
KEYWORDS = ("about", "product", "service", "pricing", "solution", "platform", "company")


def extract_html(html: str, url: str, limit: int) -> tuple[str, str | None, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else None
    links = []
    for link in soup.select("a[href]"):
        href = link.get("href", "")
        candidate = urljoin(url, href)
        if any(word in (href + " " + link.get_text()).lower() for word in KEYWORDS):
            try:
                links.append(normalize_url(candidate))
            except ValueError:
                pass
    assets = []
    for img in soup.select("img[src]"):
        if "logo" in (str(img.get("alt", "")) + str(img.get("src", ""))).lower():
            assets.append("Logo URL: " + urljoin(url, img["src"]))
    theme = soup.find("meta", attrs={"name": "theme-color"})
    if theme and theme.get("content"):
        assets.append("Theme color: " + theme["content"])
    description = soup.find("meta", attrs={"name": "description"})
    if description and description.get("content"):
        assets.append("Meta description: " + description["content"])
    for element in soup(["script", "style", "noscript", "svg", "template"]):
        element.decompose()
    text = "\n".join(filter(None, [title, *assets, soup.get_text(" ", strip=True)]))
    return text[:limit], title, links


class WebsiteSource:
    """Breadth-first relevant-page discovery with robots, host and request limits.

    No JavaScript execution, authentication, form submission or third-party crawling.
    Optional-page failures are warnings; a failed/blocked homepage fails the job.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    async def collect(self, website_url: str) -> SourceBundle:
        host = urlsplit(website_url).hostname
        bare = host.removeprefix("www.")
        hosts = {bare, "www." + bare}
        bundle = SourceBundle([])
        async with httpx.AsyncClient(trust_env=False) as client:
            fetcher = PublicFetcher(self.settings, client)
            robots_cache: dict[str, RobotFileParser] = {}

            async def robots_for(url: str) -> RobotFileParser:
                parsed = urlsplit(url)
                origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
                if origin not in robots_cache:
                    parser = RobotFileParser()
                    try:
                        page = await fetcher.fetch(origin + "/robots.txt", hosts)
                        parser.parse(page.body.splitlines())
                    except DomainError as exc:
                        if exc.code == "CRAWL_HTTP_ERROR" and "HTTP 404" in exc.message:
                            parser.parse([])
                        else:
                            raise DomainError(
                                "ROBOTS_UNAVAILABLE",
                                "Cannot establish website robots policy",
                                retryable=exc.retryable,
                            ) from exc
                    robots_cache[origin] = parser
                return robots_cache[origin]

            async def authorize(url: str):
                robots = await robots_for(url)
                delay = robots.crawl_delay(BOT) or 0.3
                if not robots.can_fetch(BOT, url) or delay > 5:
                    raise DomainError("ROBOTS_DENIED", "Website robots policy blocks crawling")
                if bundle.documents:
                    await asyncio.sleep(delay)

            root = urljoin(website_url, "/")
            fallbacks = [
                urljoin(root, path) for path in ("about", "products", "services", "pricing")
            ]
            pending = [website_url, *fallbacks]
            visited: set[str] = set()
            while pending and len(visited) < self.settings.crawl_max_pages:
                url = pending.pop(0)
                if url in visited or urlsplit(url).hostname not in hosts:
                    continue
                visited.add(url)
                try:
                    # Every redirect hop is authorized before the request is made.
                    page = await fetcher.fetch(url, hosts, authorize=authorize)
                    content, title, links = extract_html(
                        page.body, page.url, self.settings.crawl_max_text_chars
                    )
                    if len(content.strip()) < 30:
                        raise DomainError(
                            "EMPTY_PAGE", "Website page has insufficient readable text"
                        )
                    if page.url not in {doc.url for doc in bundle.documents}:
                        bundle.documents.append(Document(page.url, content, now(), title))
                    visited.add(page.url)
                    pending = [link for link in links if link not in visited] + pending
                except DomainError as exc:
                    logger.warning(
                        "crawl_page_failed code=%s host=%s", exc.code, urlsplit(url).hostname
                    )
                    if not bundle.documents:
                        raise
                    bundle.warnings.append(f"{url}: {exc.code} — {exc.message}")
            if not bundle.documents:
                raise DomainError("CRAWL_EMPTY", "No readable public pages were collected")
        return bundle
