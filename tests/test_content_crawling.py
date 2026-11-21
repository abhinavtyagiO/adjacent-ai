"""Content-first policy: saved navigation and synthetic HTML/transport, never live requests."""

import html
import json
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.crawling.http import FetchedPage, PublicFetcher
from app.crawling.policy import page_category
from app.crawling.signals import css_colors
from app.crawling.website import WebsiteSource, parse_page
from app.discovery import discover
from app.draft import relevant_context
from app.enrichment import Document, SourceBundle
from app.llm.responses import StructuredResponsesProvider
from app.models import now
from app.schemas import BIP, DraftFactualResult

ROOT = "https://shop.example/"


def nav_html(links):
    return "".join(
        f'<a href="{html.escape(x["url"])}">{html.escape(x["label"])}</a>' for x in links
    )


@pytest.fixture
def offline_crawl(settings, monkeypatch):
    """No real HTTP/DNS, fixed robots and zero real pacing delay."""
    calls = []
    bodies = {}
    settings.crawl_max_sitemaps = 0
    monkeypatch.setattr("app.crawling.website.asyncio.sleep", AsyncMock())

    async def fetch(self, url, hosts, authorize=None):
        assert urlsplit(url).hostname in hosts
        if authorize:
            await authorize(url)
        calls.append(url)
        if url.endswith("/robots.txt"):
            body, media = bodies.get(url, ("User-agent: *\nDisallow:", "text/plain"))
        else:
            body, media = bodies.get(
                url, ("<h1>Example company information and benefits.</h1>", "text/html")
            )
        return FetchedPage(url, body, media)

    monkeypatch.setattr(PublicFetcher, "fetch", fetch)
    return bodies, calls


@pytest.mark.parametrize(
    ("url", "label", "expected"),
    [
        ("/pages/reviews-1", "Reviews", "customer_story"),
        ("/review/competitor", "", "comparison"),
        ("/resources/42", "Brand Kit", "brand_assets"),
        ("/products/cleanser", "MoisturizerTestimonial-VitB5-P6", "product"),
        ("/collections/skin/products/cleanser", "Testimonials", "product"),
        ("/blog/how-to-focus-on-reading", "Reading", "audience_content"),
        ("/pages/ingredients", "", "education"),
        ("/ai/solutions", "Solutions", "use_case"),
    ],
)
def test_content_classification(url, label, expected):
    assert page_category(ROOT.rstrip("/") + url, label) == expected


async def test_saved_navigation_recovers_reviews_and_explicitly_allowed_opal_kit(
    settings, offline_crawl
):
    bodies, calls = offline_crawl
    sites = json.loads((Path(__file__).parent / "fixtures/content-navigation.json").read_text())[
        "sites"
    ]
    opal = sites["opal"]
    settings.crawl_max_pages = 5
    settings.crawl_brand_asset_hosts = {"opalapp.com": ["brandkit.opal.so"]}
    bodies[opal["url"]] = (
        "<h1>Opal company information for better focus.</h1>" + nav_html(opal["navigation"]),
        "text/html",
    )
    bundle = await WebsiteSource(settings).collect(opal["url"])
    assert "https://brandkit.opal.so/" in calls
    assert "https://brandkit.opal.so/robots.txt" in calls
    assert any(d.metadata["category"] == "brand_assets" for d in bundle.documents)
    assert not any("twitter.com" in x or "linkedin.com" in x for x in calls)
    # These are real recorded links; the kit response above is synthetic, not a live palette.
    minimal = sites["minimalist"]
    bodies[minimal["url"]] = (
        "<h1>Skincare for everyday routines and concerns.</h1>" + nav_html(minimal["navigation"]),
        "text/html",
    )
    await WebsiteSource(settings).collect(minimal["url"])
    assert "https://beminimalist.co/pages/reviews-1" in calls


async def test_external_kits_require_both_company_mapping_and_explicit_site_link(
    settings, offline_crawl
):
    bodies, calls = offline_crawl
    bodies[ROOT] = (
        "<h1>Company and product information for teams.</h1>"
        '<a href="https://kit.example/">Brand Kit</a>',
        "text/html",
    )
    bundle = await WebsiteSource(settings).collect(ROOT)
    assert not any("kit.example" in x for x in calls)
    assert bundle.coverage["unfetched_external_brand_assets"] == ["https://kit.example/"]
    calls.clear()
    settings.crawl_brand_asset_hosts = {"shop.example": ["kit.example"]}
    bodies[ROOT] = (
        "<h1>Company and product information for teams.</h1>"
        '<a href="https://kit.example/">Other website</a>',
        "text/html",
    )
    await WebsiteSource(settings).collect(ROOT)
    assert not any("kit.example" in x for x in calls)


async def test_reviews_assets_and_audience_win_before_product_depth_and_sitemaps(
    settings, offline_crawl
):
    bodies, calls = offline_crawl
    links = [{"url": f"/products/p{i}", "label": f"Product {i}"} for i in range(12)]
    links += [
        {"url": x, "label": label}
        for x, label in [
            ("/about", "About"),
            ("/proof", "Testimonials"),
            ("/resources/kit", "Brand Kit"),
            ("/for/teams", "For teams"),
        ]
    ]
    bodies[ROOT] = (
        "<h1>Company and products for busy customers.</h1>" + nav_html(links),
        "text/html",
    )
    settings.crawl_max_sitemaps = 1
    bodies[ROOT + "sitemap.xml"] = ("<urlset/>", "application/xml")
    bundle = await WebsiteSource(settings).collect(ROOT)
    content = [x for x in calls if not x.endswith(("robots.txt", "sitemap.xml"))]
    assert all("/products/" not in x for x in content[:5])
    assert calls.index(ROOT + "sitemap.xml") > calls.index(ROOT + "about")
    assert bundle.coverage["category_attempts"]["product"] == 3
    assert bundle.coverage["category_capped_urls"]["product"] == 9
    assert bundle.coverage["fetch_operations"] <= settings.crawl_max_requests
    assert not bundle.coverage["budget_limited"]


async def test_declared_locale_duplicates_skipped_without_guessing_two_letter_paths(
    settings, offline_crawl
):
    bodies, calls = offline_crawl
    bodies[ROOT] = (
        """<html lang="en"><head><link rel="alternate" hreflang="fr" href="/fr/"></head>
    <body><h1>Company for focus and productivity.</h1><a href="/fr/about">About in French</a>
    <a href="/about">About</a><a href="/ai/solutions">Solutions</a></body></html>""",
        "text/html",
    )
    bundle = await WebsiteSource(settings).collect(ROOT)
    assert ROOT + "fr/about" not in calls
    assert ROOT + "ai/solutions" in calls
    assert bundle.coverage["skipped"]["alternate_locale"] >= 1
    calls.clear()
    settings.crawl_skip_alternate_locales = False
    await WebsiteSource(settings).collect(ROOT)
    assert ROOT + "fr/about" in calls


async def test_shopify_catalog_is_broad_but_duplicate_detail_paths_are_visited_once(
    settings, offline_crawl
):
    bodies, calls = offline_crawl
    bodies[ROOT] = (
        """<h1>Skincare company with transparent ingredients.</h1><script>Shopify.shop="x";</script>
    <a href="/collections/skin/products/cleanser">Cleanser</a>
    <a href="/products/cleanser">Cleanser</a>""",
        "text/html",
    )
    records = [
        {"id": i, "title": f"Product {i}", "handle": f"p{i}", "product_type": "Skin"}
        for i in range(30)
    ]
    bodies[ROOT + "products.json?limit=250&page=1"] = (
        json.dumps({"products": records}),
        "application/json",
    )
    bundle = await WebsiteSource(settings).collect(ROOT)
    assert ROOT + "products/cleanser" in calls
    assert ROOT + "collections/skin/products/cleanser" not in calls
    products, _, _ = discover(bundle, ROOT)
    assert len(products) >= 30


def test_static_customer_quotes_and_visual_declarations_are_literal_and_scoped():
    body = """<html lang="en"><head>
    <style>:root {--brand-primary: #aabbcc; color:#ff0000;}</style></head>
    <body><h1>Example Company</h1>
    <blockquote>I can focus on my reading again.<cite>Published Name</cite></blockquote>
    <div class="testimonial-text">An everyday routine that works for me.</div>
    <div data-brand-color="#123456" data-color-name="Ocean"></div>
    <script type="application/ld+json">{"@type":"Review",
    "reviewBody":"My mornings feel more organized.","author":{"name":"Example Reviewer"}}</script>
    </body></html>"""
    text, _, _, metadata = parse_page(body, ROOT, 12000)
    quotes = metadata["testimonials"]
    assert len(quotes) == 3
    assert any(
        q["quote"] == "I can focus on my reading again." and q["attribution"] == "Published Name"
        for q in quotes
    )
    assert all(q["interpretation"] == "PUBLISHED_QUOTATION_NOT_VERIFIED_RESULT" for q in quotes)
    assert "An everyday routine that works for me." in text
    assert {v["value"] for v in metadata["visual_signals"]} == {"#aabbcc", "#123456"}
    assert css_colors(":root {color:#aabbcc;--primary:rgb(1,2,3);}", ROOT) == []


async def test_stylesheet_and_asset_robots_share_existing_request_ceiling(settings, offline_crawl):
    bodies, calls = offline_crawl
    bodies[ROOT] = (
        "<h1>A company for daily focus and attention.</h1>"
        '<link rel="stylesheet" href="/style.css">'
        '<link rel="stylesheet" href="https://cdn.example/x.css">',
        "text/html",
    )
    bodies[ROOT + "style.css"] = (":root {--brand-primary: #112233;}", "text/css")
    bundle = await WebsiteSource(settings).collect(ROOT)
    assert ROOT + "style.css" in calls
    assert not any("cdn.example" in x for x in calls)
    home = bundle.documents[0]
    assert home.metadata["visual_signals"][0]["source_url"] == ROOT + "style.css"
    assert "#112233" in home.text
    calls.clear()
    bodies[ROOT + "robots.txt"] = ("User-agent: *\nDisallow: /style.css", "text/plain")
    bundle = await WebsiteSource(settings).collect(ROOT)
    assert ROOT + "style.css" not in calls
    assert not bundle.documents[0].metadata["visual_signals"]
    assert any("ROBOTS_DENIED" in x for x in bundle.warnings)
    settings.crawl_max_requests = 2
    bundle = await WebsiteSource(settings).collect(ROOT)
    assert bundle.coverage["fetch_operations"] == 2
    assert bundle.coverage["budget_limited"]


def test_context_prefers_proof_and_audience_over_repetitive_skus(settings):
    profile = BIP.model_validate(
        {
            "brand_identity": {"name": "Example", "website_url": ROOT},
            "offerings": [{"name": "Cleanser"}],
        }
    )
    docs = [
        Document(
            ROOT + f"products/p{i}",
            "Cleanser feature detail. " * 400,
            now(),
            metadata={"category": "product"},
        )
        for i in range(4)
    ]
    docs += [
        Document(
            ROOT + "testimonials",
            "Customers describe a calmer everyday routine. " * 300,
            now(),
            metadata={"category": "customer_story"},
        )
    ]
    sources = SourceBundle(docs)
    result = relevant_context(sources, profile, None, 2800)
    assert result.documents[0].url.endswith("testimonials")
    assert sum(len(d.text) for d in result.documents) <= 2800
    settings.extraction_mode = "draft_first"
    settings.factual_context_max_chars = 10000
    provider = StructuredResponsesProvider(
        settings, name="test", model="mock", api_key=None, api_key_variable="UNUSED"
    )
    payload = provider._documents(sources, {}, DraftFactualResult, "Offline test")
    lengths = {d["category"]: len(d["content"]) for d in payload["documents"]}
    assert lengths["customer_story"] > lengths["product"]
    assert sum(len(d["content"]) for d in payload["documents"]) <= 10000


@pytest.mark.parametrize(
    "mapping",
    [
        {"shop.example": ["*.example"]},
        {"shop.example": ["127.0.0.1"]},
        {"shop.example": ["user@kit.example"]},
        {"shop.example": ["kit.example/path"]},
    ],
)
def test_asset_host_configuration_rejects_unsafe_or_broad_entries(mapping):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, crawl_brand_asset_hosts=mapping)


async def test_late_proof_links_survive_a_full_product_candidate_set(settings, offline_crawl):
    bodies, calls = offline_crawl
    settings.crawl_max_links = 5
    settings.crawl_max_pages = 3
    links = [{"url": f"/products/p{i}", "label": f"Product {i}"} for i in range(20)]
    links += [{"url": "/customer-stories", "label": "Customer stories"}]
    bodies[ROOT] = (
        "<h1>Company products and useful audience context.</h1>" + nav_html(links),
        "text/html",
    )
    bundle = await WebsiteSource(settings).collect(ROOT)
    assert ROOT + "customer-stories" in calls
    assert bundle.coverage["skipped"]["candidate_evicted"] == 1
    assert bundle.coverage["discovered_urls"] <= 5
