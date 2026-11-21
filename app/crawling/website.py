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
from app.crawling.policy import CATEGORIES, SCORES, asset_label, page_category
from app.crawling.signals import css_colors, extract_signals, signal_text
from app.crawling.urls import normalize_url
from app.enrichment import Document, SourceBundle
from app.errors import DomainError
from app.models import now

logger = logging.getLogger(__name__)
BOT = "BrandIntelligenceBot"


def structured_data(soup: BeautifulSoup) -> dict:
    """Extract whitelisted JSON-LD context without following any embedded URLs."""
    products, faqs, reviews = [], [], []

    def walk(value, depth=0, parent=None):
        if depth > 12:
            return
        if isinstance(value, list):
            for child in value[:100]:
                walk(child, depth + 1, parent)
        elif isinstance(value, dict):
            types = value.get("@type", [])
            types = [types] if isinstance(types, str) else types if isinstance(types, list) else []
            if (
                any(t in types for t in ("Product", "ProductGroup", "Course", "Service"))
                and len(products) < 100
            ):
                keys = (
                    "@type",
                    "@id",
                    "url",
                    "image",
                    "sku",
                    "productID",
                    "gtin",
                    "mpn",
                    "name",
                    "description",
                    "category",
                    "brand",
                    "offers",
                    "hasVariant",
                    "isVariantOf",
                    "additionalProperty",
                )
                record = {key: value[key] for key in keys if key in value}
                if parent and not record.get("isVariantOf"):
                    record["isVariantOf"] = parent
                products.append(record)
            if "Question" in types and len(faqs) < 50:
                answer = value.get("acceptedAnswer", {})
                if isinstance(answer, dict):
                    faqs.append({"question": value.get("name"), "answer": answer.get("text")})
            if "Review" in types and isinstance(value.get("reviewBody"), str) and len(reviews) < 20:
                author = value.get("author")
                author = (
                    author.get("name")
                    if isinstance(author, dict)
                    else author
                    if isinstance(author, str)
                    else None
                )
                reviews.append({"quote": value["reviewBody"], "attribution": author})
            for key in ("@graph", "mainEntity", "itemListElement", "item", "hasVariant", "review"):
                if key in value:
                    parent_info = (
                        {k: value[k] for k in ("name", "url", "@id") if k in value}
                        if key == "hasVariant"
                        else None
                    )
                    walk(value[key], depth + 1, parent_info)

    for tag in soup.select('script[type="application/ld+json"]')[:20]:
        try:
            walk(json.loads(tag.string or tag.get_text()))
        except (ValueError, TypeError, RecursionError):
            continue
    return {"products": products, "faqs": faqs, "reviews": reviews}


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
    links, navigation, catalog_links = [], [], []
    for anchor in soup.select("a[href]")[:4000]:
        href = str(anchor.get("href", ""))
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        try:
            candidate = normalize_url(urljoin(url, href))
            label = anchor.get_text(" ", strip=True)
            if len(navigation) < 300 and label:
                navigation.append({"label": label[:300], "url": candidate})
            if (
                re.search(
                    r"/(?:products|product|courses|programs|p)/[^/]+", urlsplit(candidate).path
                )
                and len(catalog_links) < 1000
            ):
                image = anchor.find("img")
                name = (str(image.get("alt", "")) if image else "") or label
                if name:
                    catalog_links.append(
                        {
                            "name": name[:300],
                            "url": candidate,
                            "image": urljoin(
                                url, str(image.get("src") or image.get("data-src") or "")
                            )
                            if image
                            else None,
                        }
                    )
            if page_category(candidate, label) not in {"other", "comparison"}:
                links.append(candidate)
        except ValueError:
            continue
    structured = structured_data(soup)
    signals = extract_signals(soup, url, structured["reviews"])

    def meta_value(*names):
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find(
                "meta", attrs={"name": name}
            )
            if tag and tag.get("content"):
                return str(tag["content"])
        return None

    html_product = None
    if page_category(url) == "product":
        html_product = {
            "name": heading.get_text(" ", strip=True) if heading else meta_value("og:title"),
            "url": url,
            "image": meta_value("og:image"),
            "offers": {
                "price": meta_value("product:price:amount", "og:price:amount"),
                "priceCurrency": meta_value("product:price:currency", "og:price:currency"),
            },
        }
    shopify = bool(
        soup.find("script", src=re.compile("cdn.shopify.com"))
        or re.search(r"Shopify\.(?:shop|theme)|cdn\.shopify\.com", html)
    )
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
    prefix = "\n".join(filter(None, [title, *metadata, signal_text(signals)]))
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
            **signals,
            "navigation": navigation,
            "catalog_links": catalog_links,
            "html_product": html_product,
            "heading": heading.get_text(" ", strip=True) if heading else None,
            "shopify": shopify,
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
        attempts, skipped = Counter(), Counter()
        alternate_prefixes, alternate_urls = set(), set()
        approved_assets = set(self.settings.crawl_brand_asset_hosts.get(bare, []))
        linked_assets, stylesheet_seen = set(), set()
        stylesheet_count, shopify = 0, False
        sitemaps_done = False
        excluded_asset_urls, product_aliases = set(), set()

        def is_alternate(url):
            return self.settings.crawl_skip_alternate_locales and (
                url in alternate_urls
                or any(url.startswith(prefix) for prefix in alternate_prefixes)
            )

        def canonical(url):
            if shopify and urlsplit(url).hostname in hosts:
                parsed = urlsplit(url)
                path = re.sub(r"^/collections/[^/]+(/products/[^/]+)$", r"\1", parsed.path)
                return urlunsplit(parsed._replace(path=path))
            return url

        started = time.monotonic()
        async with httpx.AsyncClient(trust_env=False) as client:
            fetcher = PublicFetcher(self.settings, client)

            def discover(url, label="", origin=None):
                try:
                    original = normalize_url(url)
                    url = canonical(original)
                    if url != original:
                        product_aliases.add(original)
                    target_host = urlsplit(url).hostname
                    category = page_category(url, label)
                    if target_host not in hosts:
                        # Only explicit site-linked kits on a per-company allowlist.
                        if (
                            target_host in approved_assets
                            and category == "brand_assets"
                            and origin
                            and urlsplit(origin).hostname in hosts | linked_assets
                            and (asset_label(label) or target_host in linked_assets)
                        ):
                            linked_assets.add(target_host)
                        else:
                            if asset_label(label):
                                excluded_asset_urls.add(url)
                            return
                    if is_alternate(url):
                        skipped["alternate_locale"] += int(url not in candidates)
                        return
                    if len(candidates) >= self.settings.crawl_max_links and url not in candidates:
                        # A long product menu must not crowd a late footer proof/kit link
                        # out of the bounded candidate set. Evict only unvisited lower priorities.
                        replaceable = [u for u in candidates if u not in visited]
                        weakest = min(
                            replaceable, key=lambda u: SCORES[candidates[u]], default=None
                        )
                        if weakest and SCORES[category] > SCORES[candidates[weakest]]:
                            del candidates[weakest]
                            skipped["candidate_evicted"] += 1
                        else:
                            skipped["candidate_limit"] += 1
                            return
                    if category not in {"comparison", "other"}:
                        previous = candidates.get(url)
                        if previous is None or SCORES[category] > SCORES[previous]:
                            candidates[url] = category
                except ValueError:
                    pass

            async def fetch(url, authorize=None):
                nonlocal operations
                if operations >= self.settings.crawl_max_requests:
                    raise DomainError("CRAWL_BUDGET", "Website fetch-operation budget exhausted")
                operations += 1
                remaining = self.settings.crawl_max_elapsed_seconds - (time.monotonic() - started)
                try:
                    async with asyncio.timeout(max(0.001, remaining)):
                        allowed = (
                            hosts if urlsplit(url).hostname in hosts else {urlsplit(url).hostname}
                        )
                        if not allowed <= hosts | linked_assets:
                            raise DomainError("OFFSITE_REDIRECT", "Unapproved resource host")
                        return await fetcher.fetch(url, allowed, authorize=authorize)
                except TimeoutError as exc:
                    raise DomainError(
                        "CRAWL_BUDGET", "Website discovery elapsed-time budget exhausted"
                    ) from exc

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

            async def styles(metadata, page_url):
                nonlocal stylesheet_count
                # Leave a slot for a later brand kit rather than spending both on home CSS.
                limit = self.settings.crawl_max_stylesheets
                if metadata["category"] != "brand_assets":
                    limit = min(limit, 1)
                for target in metadata.get("stylesheets", []):
                    if stylesheet_count >= limit:
                        break
                    if target in stylesheet_seen:
                        continue
                    stylesheet_seen.add(target)
                    if urlsplit(target).hostname != urlsplit(page_url).hostname:
                        skipped["external_stylesheet"] += 1
                        continue
                    if operations >= self.settings.crawl_max_requests:
                        break
                    stylesheet_count += 1
                    try:
                        resource = await fetch(target, authorize)
                        if resource.content_type not in {"text/css", "text/plain"}:
                            raise DomainError("UNSUPPORTED_CONTENT", "Expected a public stylesheet")
                        for color in css_colors(resource.body, resource.url):
                            metadata["visual_signals"].append(color)
                        metadata["visual_signals"] = metadata["visual_signals"][:24]
                    except DomainError as exc:
                        bundle.warnings.append(f"Stylesheet {target}: {exc.code}")

            root = urljoin(website_url, "/")
            while len(bundle.documents) < self.settings.crawl_max_pages:
                if time.monotonic() - started >= self.settings.crawl_max_elapsed_seconds:
                    break
                if (
                    operations >= self.settings.crawl_max_requests
                    or total_chars >= self.settings.crawl_max_total_chars
                ):
                    break
                available = [
                    (url, cat)
                    for url, cat in candidates.items()
                    if url not in visited
                    and not is_alternate(url)
                    and attempts[cat] < self.settings.crawl_category_limits[cat]
                ]
                # Spend the first slots on site-linked audience/proof/assets; then use
                # sitemaps to fill gaps. They share the same operation/time ceilings.
                if not sitemaps_done and (len(bundle.documents) >= 5 or not available):
                    sitemaps_done = True
                    await discover_sitemaps()
                    continue
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
                attempts[category] += 1
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
                    metadata["category"] = category
                    shopify = shopify or metadata.get("shopify", False)
                    if self.settings.crawl_skip_alternate_locales:
                        current_lang = str(metadata.get("language") or "").lower().split("-")[0]
                        for alternate in metadata.get("alternate_locales", []):
                            if (
                                not current_lang
                                or alternate["language"].split("-")[0] == current_lang
                            ):
                                continue
                            target = alternate["url"]
                            if urlsplit(target).hostname not in hosts:
                                continue
                            alternate_urls.add(target)
                            prefix = urlsplit(target).path.strip("/").split("/")[0]
                            if prefix.lower() == alternate["language"]:
                                parsed = urlsplit(target)
                                alternate_prefixes.add(
                                    urlunsplit(
                                        (parsed.scheme, parsed.netloc, "/" + prefix + "/", "", "")
                                    )
                                )
                    await styles(metadata, page.url)
                    observations = signal_text(
                        {
                            "visual_signals": [
                                v for v in metadata["visual_signals"] if v["source_url"] != page.url
                            ]
                        }
                    )
                    if observations:
                        content = (observations + "\n" + content)[:limit]
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
                    for candidate, kind in list(candidates.items()):
                        normalized = canonical(candidate)
                        if normalized != candidate:
                            del candidates[candidate]
                            candidates.setdefault(normalized, kind)
                            product_aliases.add(candidate)
                    labels = {link["url"]: link["label"] for link in metadata["navigation"]}
                    for link in links:
                        discover(link, labels.get(link, ""), page.url)
                    if len(bundle.documents) == 1:
                        if metadata.get("shopify"):
                            for number in range(1, self.settings.catalog_max_pages + 1):
                                if (
                                    operations >= self.settings.crawl_max_requests
                                    or len(bundle.documents) >= self.settings.crawl_max_pages
                                    or time.monotonic() - started
                                    >= self.settings.crawl_max_elapsed_seconds
                                ):
                                    break
                                try:
                                    endpoint = (
                                        urljoin(page.url, "/products.json")
                                        + f"?limit=250&page={number}"
                                    )
                                    product_page = await fetch(endpoint, authorize)
                                    payload = json.loads(product_page.body)
                                    products = (
                                        payload.get("products")
                                        if isinstance(payload, dict)
                                        else None
                                    )
                                    if not isinstance(products, list):
                                        raise ValueError("Invalid public product data")
                                    bundle.documents.append(
                                        Document(
                                            product_page.url,
                                            "Public product catalog: "
                                            + str(len(products))
                                            + " records.",
                                            now(),
                                            source_type="catalog",
                                            metadata={
                                                "public_products": products[:250],
                                                "category": "catalog",
                                            },
                                        )
                                    )
                                    counts["catalog"] += 1
                                    if len(products) < 250:
                                        break
                                except (DomainError, ValueError) as exc:
                                    bundle.warnings.append(
                                        "Public catalog discovery: "
                                        + getattr(exc, "code", "INVALID_PRODUCT_DATA")
                                    )
                                    break
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
            "policy": "content_first_v1",
            "category_limits": self.settings.crawl_category_limits,
            "category_attempts": dict(attempts),
            "category_capped_urls": dict(
                Counter(
                    cat
                    for url, cat in candidates.items()
                    if url not in visited
                    and not is_alternate(url)
                    and attempts[cat] >= self.settings.crawl_category_limits[cat]
                )
            ),
            "skipped": {**dict(skipped), "duplicate_product_path": len(product_aliases)},
            "alternate_locale_urls_skipped": sum(is_alternate(url) for url in candidates),
            "unfetched_external_brand_assets": sorted(excluded_asset_urls)[:20],
            "stylesheets_checked": stylesheet_count,
            "pending_eligible_urls": sum(
                url not in visited
                and not is_alternate(url)
                and attempts[cat] < self.settings.crawl_category_limits[cat]
                for url, cat in candidates.items()
            ),
            "categories": dict(counts),
            "missing_categories": [c for c in CATEGORIES if not counts[c]],
            "pages": len(bundle.documents),
            "catalog_pages": sum(d.source_type == "catalog" for d in bundle.documents),
            "html_pages": sum(d.source_type != "catalog" for d in bundle.documents),
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
        bundle.coverage["stop_reason"] = (
            "GLOBAL_BUDGET"
            if bundle.coverage["budget_limited"]
            else "CATEGORY_LIMITS"
            if bundle.coverage["category_capped_urls"]
            else "NO_ELIGIBLE_URLS"
        )
        logger.info(
            "Content-first crawl completed",
            extra={"crawl_coverage": bundle.coverage},
        )
        return bundle
