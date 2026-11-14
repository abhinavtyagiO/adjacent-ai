"""Budgeted, category-balanced public page discovery and structure-preserving ingestion."""

import asyncio
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter
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
CATEGORIES = (
    "homepage",
    "brand_story",
    "category",
    "product",
    "use_case",
    "customer_story",
    "faq",
    "pricing",
)
SCORES = {
    "homepage": 1000,
    "brand_story": 90,
    "category": 100,
    "product": 85,
    "use_case": 85,
    "customer_story": 80,
    "faq": 80,
    "pricing": 85,
    "other": 10,
    "comparison": -1,
}


def page_category(url: str, label: str = "") -> str:
    path = urlsplit(url).path.lower().rstrip("/")
    context = path + " " + label.lower()
    if re.search(r"^/(?:review|reviews|compare|comparison|alternatives)(?:/|$)", path):
        return "comparison"
    if re.search(r"^/(?:terms|privacy|search|cart|checkout|login|sign-in)(?:/|$)", path):
        return "comparison"
    if re.search(r"compare|comparison|alternatives|\bvs\b", context):
        return "comparison"
    if path.startswith("/pricing/") and path.rsplit("/", 1)[-1] not in {
        "starter",
        "growth",
        "pro",
        "enterprise",
        "plans",
    }:
        return "comparison"
    if not path:
        return "homepage"
    if re.search(r"/(?:faq[s]?|frequently-asked[^/]*|questions)(?:/|$)", path):
        return "faq"
    if re.search(r"customer-stor|case-stud|testimonials|placement-stor|success-stor", context):
        return "customer_story"
    if path.startswith(("/blog/", "/learn/", "/articles/")):
        return "other"
    if re.search(r"/(?:our-story|brand-story|about[^/]*|our-mission|our-values)(?:/|$)", path):
        return "brand_story"
    if re.search(r"/(?:pricing|plans)(?:/|$)", path):
        return "pricing"
    if re.search(r"/(?:use-cases?|solutions|who-we-help|for|tools)(?:/|$)", path):
        return "use_case"
    if re.search(r"/products?/[^/]+|/courses?/[^/]+|/programs?/[^/]+|/p/[^/]+", path):
        return "product"
    if path.endswith(("-program", "-course")):
        return "product"
    if re.search(
        r"/(?:collections?|categories|category|shop|products?|services?|courses?|programs?|platform)(?:/|$)",
        path,
    ):
        return "category"
    return "other"


def structured_data(soup: BeautifulSoup) -> dict:
    """Extract whitelisted JSON-LD context without following any embedded URLs."""
    products, faqs = [], []

    def walk(value, depth=0):
        if depth > 12:
            return
        if isinstance(value, list):
            for child in value[:100]:
                walk(child, depth + 1)
        elif isinstance(value, dict):
            types = value.get("@type", [])
            types = [types] if isinstance(types, str) else types
            if "Product" in types and len(products) < 100:
                keys = (
                    "@type",
                    "name",
                    "description",
                    "category",
                    "brand",
                    "offers",
                    "hasVariant",
                    "isVariantOf",
                    "additionalProperty",
                )
                products.append({key: value[key] for key in keys if key in value})
            if "Question" in types and len(faqs) < 50:
                answer = value.get("acceptedAnswer", {})
                if isinstance(answer, dict):
                    faqs.append({"question": value.get("name"), "answer": answer.get("text")})
            for key in ("@graph", "mainEntity", "itemListElement", "item", "hasVariant"):
                if key in value:
                    walk(value[key], depth + 1)

    for tag in soup.select('script[type="application/ld+json"]')[:20]:
        try:
            walk(json.loads(tag.string or tag.get_text()))
        except (ValueError, TypeError, RecursionError):
            continue
    return {"products": products, "faqs": faqs}


def parse_page(html: str, url: str, limit: int):
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else None
    heading = soup.find("h1")
    error_context = " ".join([title or "", heading.get_text(" ", strip=True) if heading else ""])
    if re.search(
        r"\b404\b|page (?:not found|does not exist)|couldn.t find (?:this|that) page",
        error_context,
        re.I,
    ):
        raise DomainError("PAGE_NOT_FOUND", "Website returned a soft-404 error page")
    links = []
    for anchor in soup.select("a[href]")[:4000]:
        href = str(anchor.get("href", ""))
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        try:
            candidate = normalize_url(urljoin(url, href))
            label = anchor.get_text(" ", strip=True)
            if page_category(candidate, label) not in {"other", "comparison"}:
                links.append(candidate)
        except ValueError:
            continue
    structured = structured_data(soup)
    metadata = []
    for name, field in [("description", "Meta description"), ("theme-color", "Theme color")]:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            metadata.append(field + ": " + tag["content"])
    for img in soup.select("img[src]")[:100]:
        if "logo" in (str(img.get("alt", "")) + str(img.get("src", ""))).lower():
            metadata.append("Logo URL: " + urljoin(url, img["src"]))
    for tag in soup(["script", "style", "noscript", "svg", "template", "form"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    lines = []
    for tag in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "tr", "dt", "dd"]):
        if tag.name == "p" and tag.find_parent(["li", "td", "th"]):
            continue
        content = tag.get_text(" ", strip=True)
        if not content:
            continue
        if tag.name.startswith("h"):
            content = "#" * int(tag.name[1]) + " " + content
        elif tag.name == "li":
            content = "- " + content
        elif tag.name == "tr":
            content = " | ".join(c.get_text(" ", strip=True) for c in tag.find_all(["th", "td"]))
        lines.append(content)
    if not lines:
        lines = [main.get_text(" ", strip=True)]
    prefix = "\n".join(filter(None, [title, *metadata]))
    body = "\n".join(dict.fromkeys(lines))
    extra = ""
    if structured["products"]:
        extra += "\n## Structured products (JSON-LD)\n" + json.dumps(
            structured["products"], ensure_ascii=False
        )
    for faq in structured["faqs"]:
        extra += (
            "\nQuestion: "
            + str(faq["question"])
            + "\nAnswer: "
            + BeautifulSoup(str(faq["answer"]), "html.parser").get_text(" ", strip=True)
        )
    reserve = min(len(extra), limit // 3)
    text = (prefix + "\n" + body)[: max(0, limit - reserve)] + extra[:reserve]
    return (
        text[:limit],
        title,
        links,
        {
            **structured,
            "category": page_category(url),
            "truncated": len(prefix + body + extra) > limit,
        },
    )


def extract_html(html: str, url: str, limit: int):
    text, title, links, _ = parse_page(html, url, limit)
    return text, title, links


def sitemap_urls(body: str, limit: int) -> tuple[list[str], bool]:
    if "<!DOCTYPE" in body.upper() or "<!ENTITY" in body.upper():
        raise ValueError("Sitemap entities are not allowed")
    root = ET.fromstring(body)
    return [
        node.text.strip() for node in root.iter() if node.tag.split("}")[-1] == "loc" and node.text
    ][:limit], root.tag.split("}")[-1] == "sitemapindex"


class WebsiteSource:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def collect(self, website_url: str) -> SourceBundle:
        host = urlsplit(website_url).hostname
        bare = host.removeprefix("www.")
        hosts = {bare, "www." + bare}
        bundle = SourceBundle([])
        robots_cache, last_request = {}, {}
        candidates, visited = {website_url: "homepage"}, set()
        counts, operations, sitemap_count = Counter(), 0, 0
        total_chars = 0
        started = time.monotonic()
        async with httpx.AsyncClient(trust_env=False) as client:
            fetcher = PublicFetcher(self.settings, client)

            def discover(url):
                try:
                    url = normalize_url(url)
                    if (
                        urlsplit(url).hostname not in hosts
                        or len(candidates) >= self.settings.crawl_max_links
                    ):
                        return
                    category = page_category(url)
                    if category != "comparison":
                        candidates.setdefault(url, category)
                except ValueError:
                    pass

            async def fetch(url, authorize=None):
                nonlocal operations
                if operations >= self.settings.crawl_max_requests:
                    raise DomainError("CRAWL_BUDGET", "Website fetch-operation budget exhausted")
                operations += 1
                return await fetcher.fetch(url, hosts, authorize=authorize)

            async def robots_for(url):
                parsed = urlsplit(url)
                origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
                if origin not in robots_cache:
                    parser = RobotFileParser()
                    try:
                        page = await fetch(origin + "/robots.txt")
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

            async def authorize(url):
                robots = await robots_for(url)
                rate = robots.request_rate(BOT)
                delay = max(
                    robots.crawl_delay(BOT) or 0.3,
                    rate.seconds / rate.requests if rate and rate.requests else 0,
                )
                if not robots.can_fetch(BOT, url) or delay > 5:
                    raise DomainError("ROBOTS_DENIED", "Website robots policy blocks crawling")
                origin = urlsplit(url).netloc
                elapsed = time.monotonic() - last_request.get(origin, 0)
                if elapsed < delay:
                    await asyncio.sleep(delay - elapsed)
                last_request[origin] = time.monotonic()

            async def discover_sitemaps():
                nonlocal sitemap_count
                robot = await robots_for(website_url)
                queue = list(robot.site_maps() or []) + [urljoin(website_url, "/sitemap.xml")]
                seen = set()
                while queue and sitemap_count < self.settings.crawl_max_sitemaps:
                    raw = queue.pop(0)
                    try:
                        url = normalize_url(urljoin(website_url, raw))
                        if urlsplit(url).hostname not in hosts or url in seen:
                            continue
                        seen.add(url)
                        sitemap_count += 1
                        page = await fetch(url, authorize)
                        urls, is_index = sitemap_urls(page.body, self.settings.crawl_max_links)
                        if is_index:
                            queue.extend(urls)
                        else:
                            for candidate in urls:
                                discover(candidate)
                    except (DomainError, ValueError, ET.ParseError) as exc:
                        bundle.warnings.append(
                            f"Sitemap {raw}: {getattr(exc, 'code', 'INVALID_SITEMAP')}"
                        )

            root = urljoin(website_url, "/")
            while candidates and len(bundle.documents) < self.settings.crawl_max_pages:
                if time.monotonic() - started >= self.settings.crawl_max_elapsed_seconds:
                    break
                if (
                    operations >= self.settings.crawl_max_requests
                    or total_chars >= self.settings.crawl_max_total_chars
                ):
                    break
                available = [(url, cat) for url, cat in candidates.items() if url not in visited]
                if not available:
                    break
                url, category = max(
                    available,
                    key=lambda pair: (
                        SCORES[pair[1]]
                        + (25 if not counts[pair[1]] else 0)
                        - 8 * counts[pair[1]]
                        + (30 if urlsplit(pair[0]).path.startswith("/for/") else 0)
                        - (20 if urlsplit(pair[0]).path.startswith("/tools/") else 0),
                        pair[0],
                    ),
                )
                visited.add(url)
                try:
                    page = await fetch(url, authorize)
                    if page.content_type not in {
                        "text/html",
                        "application/xhtml+xml",
                        "text/plain",
                    }:
                        raise DomainError(
                            "UNSUPPORTED_CONTENT", "A content page must be HTML or text"
                        )
                    limit = min(
                        self.settings.crawl_max_text_chars,
                        self.settings.crawl_max_total_chars - total_chars,
                    )
                    content, title, links, metadata = parse_page(page.body, page.url, limit)
                    if len(content.strip()) < 30:
                        raise DomainError(
                            "EMPTY_PAGE", "Website page has insufficient readable text"
                        )
                    if page.url not in {d.url for d in bundle.documents}:
                        bundle.documents.append(
                            Document(page.url, content, now(), title, metadata=metadata)
                        )
                        counts[metadata["category"]] += 1
                        total_chars += len(content)
                    visited.add(page.url)
                    for link in links:
                        discover(link)
                    if len(bundle.documents) == 1:
                        await discover_sitemaps()
                        # Fall back to common paths only when discovery yielded almost nothing.
                        for path in (
                            ()
                            if len(candidates) > 2
                            else (
                                "about",
                                "our-story",
                                "products",
                                "collections",
                                "services",
                                "pricing",
                                "faq",
                                "use-cases",
                                "customer-stories",
                            )
                        ):
                            discover(urljoin(root, path))
                except DomainError as exc:
                    if not bundle.documents:
                        raise
                    bundle.warnings.append(f"{url}: {exc.code} — {exc.message}")
        bundle.coverage = {
            "categories": dict(counts),
            "missing_categories": [c for c in CATEGORIES if not counts[c]],
            "pages": len(bundle.documents),
            "fetch_operations": operations,
            "sitemaps_checked": sitemap_count,
            "discovered_urls": len(candidates),
            "text_characters": total_chars,
            "estimated_input_tokens": (total_chars + 2) // 3,
            "budget_limited": total_chars >= self.settings.crawl_max_total_chars
            or operations >= self.settings.crawl_max_requests
            or len(bundle.documents) >= self.settings.crawl_max_pages
            or time.monotonic() - started >= self.settings.crawl_max_elapsed_seconds,
        }
        return bundle
