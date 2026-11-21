"""Deterministic discovery: saved snapshots and constructed public responses only."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.crawling.http import FetchedPage, PublicFetcher
from app.crawling.website import WebsiteSource, parse_page
from app.discovery import discover
from app.enrichment import Document, SourceBundle
from app.models import now
from evaluations.phase1b import ROOT, load_bundle

URL = "https://shop.example/"


def html_bundle(html, url=URL):
    text, title, _, metadata = parse_page(html, url, 12000)
    return SourceBundle([Document(url, text, now(), title, metadata=metadata)])


def test_minimalist_saved_snapshot_has_real_records_and_concerns():
    bundle = load_bundle(ROOT / "evaluations/snapshots/minimalist.json")
    products, concerns, coverage = discover(bundle, "https://beminimalist.co/")
    assert len(products) == 7  # No invented expansion to an alleged competitor count.
    assert len(concerns) >= 5
    assert any("oily" in c["name"].lower() for c in concerns.values())
    assert all(
        c["interpretation"] == "WEBSITE_DERIVED_CONCERN_NOT_MEASURED_BEHAVIOR"
        for c in concerns.values()
    )
    assert all(p["price_observed_at"] and p["price_is_observed"] for p in products.values())
    assert not coverage["catalog_limit_reached"]


def test_jsonld_products_images_prices_variants_and_canonical_deduplication():
    raw = {
        "@graph": [
            {
                "@type": "Product",
                "productID": "p1",
                "name": "Cleanser",
                "url": "/collections/skin/products/cleanser?variant=1",
                "image": "https://cdn.example/cleanser.png",
                "brand": {"name": "Example"},
                "category": "Skin",
                "offers": {"price": "12.50", "priceCurrency": "USD", "availability": "InStock"},
                "hasVariant": [{"name": "Small", "sku": "S1"}, {"name": "Large", "sku": "S2"}],
            },
            {
                "@type": "Product",
                "productID": "p1",
                "name": "Cleanser",
                "url": "/products/cleanser?variant=2",
                "image": "http://127.0.0.1/image",
            },
            {"@type": "Product", "name": "Unsafe", "url": "http://127.0.0.1/products/x"},
            {"@type": "Product", "name": "Outside", "url": "https://outside.example/products/x"},
            {"@type": "Product", "name": "Article", "url": "/blog/story"},
        ]
    }
    bundle = html_bundle(
        '<h1>Skin products</h1><script type="application/ld+json">' + json.dumps(raw) + "</script>"
    )
    products, _, _ = discover(bundle, URL)
    assert len(products) == 1
    p = next(iter(products.values()))
    assert (
        p["url"] == URL + "products/cleanser"
        and p["image_url"] == "https://cdn.example/cleanser.png"
    )
    assert p["price"] == "12.50" and p["currency"] == "USD"
    assert p["variants"] == [{"name": "Small", "sku": "S1"}, {"name": "Large", "sku": "S2"}]


def test_public_products_broaden_catalog_without_llm_and_observe_prices():
    # Explicit synthetic public-response fixture, not claimed Minimalist inventory.
    records = [
        {
            "id": i,
            "title": f"Example product {i}",
            "handle": f"product-{i}",
            "product_type": "Skin",
            "vendor": "Example",
            "images": [{"src": f"https://cdn.example/{i}.png"}],
            "variants": [
                {
                    "id": str(i),
                    "title": "30ml",
                    "sku": f"S{i}",
                    "price": "9",
                    "compare_at_price": "12",
                    "available": True,
                }
            ],
        }
        for i in range(12)
    ]
    bundle = SourceBundle(
        [
            Document(
                URL + "products.json?limit=250&page=1",
                "Public catalog",
                now(),
                source_type="catalog",
                metadata={"public_products": records},
            )
        ]
    )
    products, _, coverage = discover(bundle, URL)
    assert coverage["product_count"] == 12 and len(products) > 7
    assert all(p["compare_at_price"] == "12" and p["currency"] is None for p in products.values())
    assert all(p["image_url"] and p["availability"] is True for p in products.values())
    assert len(discover(bundle, URL, limit=5)[0]) == 5
    assert discover(bundle, URL, limit=5)[2]["catalog_limit_reached"]


def test_missing_structured_data_html_fallback_and_exclusion():
    bundle = html_bundle(
        '<h1>Products</h1><a href="/products/lotion"><img src="/lotion.png" alt="Lotion"></a>'
        '<a href="/tools/calculator">Calculator</a><a href="/pricing/pro">Pro plan</a>'
        '<a href="/blog/story">Story</a>'
    )
    products, _, _ = discover(bundle, URL)
    assert len(products) == 1 and next(iter(products.values()))["name"] == "Lotion"
    assert next(iter(products.values()))["price"] is None
    assert next(iter(products.values()))["image_url"] == URL + "lotion.png"
    empty = html_bundle("<h1>Data Science Program</h1><p>Educational programs and courses.</p>")
    assert discover(empty, URL)[1] == {}  # Program/category labels aren't customer pains.


@pytest.mark.parametrize("brand", ["fastlane", "newton", "whole_truth"])
def test_existing_snapshots_are_bounded_and_do_not_invent_audiences(brand):
    bundle = load_bundle(ROOT / f"evaluations/snapshots/{brand}.json")
    products, concerns, coverage = discover(bundle, bundle.documents[0].url)
    assert all("/pricing/" not in (p["url"] or "") for p in products.values())
    assert all(
        not {"age", "income", "percentage", "demographics"} & c.keys() for c in concerns.values()
    )
    assert coverage["source_pages"] == len(bundle.documents)


@pytest.mark.parametrize("query", ["?limit=250&page=2", "?page=3&limit=20"])
async def test_public_catalog_pagination_retains_safe_query(settings, monkeypatch, query):
    monkeypatch.setattr("app.crawling.http.resolve_public", AsyncMock(return_value="93.184.216.34"))

    async def handler(request):
        assert request.url.query and request.headers["host"] == "shop.example"
        return httpx.Response(200, json={"products": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await PublicFetcher(settings, client).fetch(
            URL + "products.json" + query, {"shop.example"}
        )
    assert result.content_type == "application/json"
    assert result.url.endswith(query)


@pytest.mark.parametrize(
    "query", ["?page=99", "?limit=999", "?redirect=http://127.0.0.1", "?page=1&page=2"]
)
async def test_catalog_query_rejects_unsafe_or_unbounded_values(settings, query):
    from app.errors import DomainError

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("No HTTP allowed"))
    ) as client:
        with pytest.raises(DomainError):
            await PublicFetcher(settings, client).fetch(
                URL + "products.json" + query, {"shop.example"}
            )


@pytest.mark.parametrize("allowed", [True, False])
async def test_shopify_collection_uses_robots_and_shared_budgets(settings, monkeypatch, allowed):
    settings.catalog_max_pages = 2
    settings.crawl_max_sitemaps = 0
    settings.crawl_max_pages = 2
    calls = []

    async def fetch(self, url, hosts, authorize=None):
        calls.append(url)
        if authorize:
            await authorize(url)
        if url.endswith("/robots.txt"):
            return FetchedPage(
                url,
                "User-agent: *\n" + ("Allow: /" if allowed else "Disallow: /products.json"),
                "text/plain",
            )
        if "products.json" in url:
            return FetchedPage(
                url,
                json.dumps(
                    {
                        "products": [
                            {"id": i, "title": f"Item {i}", "handle": f"item-{i}"}
                            for i in range(10)
                        ]
                    }
                ),
                "application/json",
            )
        return FetchedPage(
            url,
            "<h1>Useful public products</h1><p>A skincare catalog with public product records.</p>"
            '<script>Shopify.shop="shop.example";</script>',
            "text/html",
        )

    monkeypatch.setattr("app.crawling.website.PublicFetcher.fetch", fetch)
    bundle = await WebsiteSource(settings).collect(URL)
    products, _, _ = discover(bundle, URL)
    assert len(calls) <= settings.crawl_max_requests
    if allowed:
        assert len(products) == 10
        assert any(d.source_type == "catalog" for d in bundle.documents)
    else:
        assert not products
        assert any("ROBOTS_DENIED" in w for w in bundle.warnings)


def test_product_group_variant_relationships_do_not_inflate_counts():
    raw = {
        "@type": "ProductGroup",
        "name": "Serum",
        "url": "/products/serum",
        "hasVariant": [
            {"@type": "Product", "name": "Serum 30ml", "url": "/products/serum-small", "sku": "S"},
            {"@type": "Product", "name": "Serum 60ml", "url": "/products/serum-large", "sku": "L"},
        ],
    }
    products, _, _ = discover(
        html_bundle(
            '<script type="application/ld+json">' + json.dumps(raw) + "</script><h1>Serum</h1>"
        ),
        URL,
    )
    assert len(products) == 1
    assert next(iter(products.values()))["url"] == URL + "products/serum"
    assert len(next(iter(products.values()))["variants"]) == 2
