"""Deterministic catalog observations and website-derived problem/need signals.

This module never crawls or calls a model. It consumes bounded saved documents and their
structured metadata. Counts describe available observations, not the entire company's SKU
inventory, and never treat category labels as measured consumer demographics.
"""

import hashlib
import re
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit

from sqlalchemy import select

from app.crawling.urls import normalize_url
from app.models import CatalogProduct, CustomerConcern, Offering, now


def safe_url(base, value, same_origin=False):
    if not isinstance(value, str) or not value:
        return None
    try:
        result = normalize_url(urljoin(base, value))
    except ValueError:
        return None
    if same_origin and urlsplit(result).hostname.removeprefix("www.") != urlsplit(
        base
    ).hostname.removeprefix("www."):
        return None
    return result


def plain(value):
    if isinstance(value, str):
        return value.strip()[:2000] or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def money(value):
    value = plain(value)
    try:
        return value if value and Decimal(value).is_finite() and Decimal(value) >= 0 else None
    except InvalidOperation:
        return None


def product_path(url):
    path = urlsplit(url).path
    return bool(
        re.search(r"/(?:products|product|p|courses|programs)/[^/]+", path)
    ) and not re.search(r"/(?:blogs?|tools|apps|calculator|templates|pricing)/", path)


def product_records(doc):
    """Prefer structured products; normalize public Shopify responses into the same shape."""
    for value in doc.metadata.get("products", [])[:1000]:
        if isinstance(value, dict):
            yield value, "JSON_LD"
    for value in doc.metadata.get("public_products", [])[:1000]:
        if not isinstance(value, dict):
            continue
        variants = value.get("variants") if isinstance(value.get("variants"), list) else []
        first = next((v for v in variants if isinstance(v, dict)), {})
        images = value.get("images") if isinstance(value.get("images"), list) else []
        first_image = images[0] if images else value.get("image")
        yield (
            {
                "name": value.get("title"),
                "url": ("/products/" + value["handle"]) if value.get("handle") else None,
                "image": first_image,
                "category": value.get("product_type"),
                "brand": value.get("vendor"),
                "productID": value.get("id"),
                "sku": first.get("sku"),
                "variants": variants,
                "offers": {
                    "price": first.get("price"),
                    "priceCurrency": value.get("currency") or doc.metadata.get("currency"),
                    "availability": first.get("available"),
                    "compare_at_price": first.get("compare_at_price"),
                },
            },
            "PUBLIC_PRODUCT_DATA",
        )
    for value in doc.metadata.get("catalog_links", [])[:1000]:
        if isinstance(value, dict):
            yield value, "HTML_PRODUCT_LINK"
    fallback = doc.metadata.get("html_product")
    if isinstance(fallback, dict):
        yield fallback, "HTML_METADATA"


def discover(bundle, website_url, limit=1000):
    products, concerns = {}, {}
    discovered_at = now().isoformat()
    # Product output precedence is fixed, regardless of crawl-page order.
    candidates = [
        (method, doc, value) for doc in bundle.documents for value, method in product_records(doc)
    ]
    priority = {"JSON_LD": 0, "PUBLIC_PRODUCT_DATA": 1, "HTML_PRODUCT_LINK": 2, "HTML_METADATA": 3}
    candidates.sort(key=lambda row: priority[row[0]])
    for method, doc, value in candidates:
        offers = value.get("offers") or {}
        offers = (
            next((o for o in offers if isinstance(o, dict)), {})
            if isinstance(offers, list)
            else offers
        )
        if not isinstance(offers, dict):
            offers = {}
        name = plain(value.get("name"))
        if not name:
            continue
        raw_url = value.get("url") or offers.get("url")
        url = safe_url(website_url, raw_url, True)
        if raw_url and not url:
            continue  # Unsafe/offsite URLs cannot become URL-less catalog observations.
        if not url and product_path(doc.url) and (name.casefold() in (doc.title or "").casefold()):
            url = safe_url(website_url, doc.url, True)
        if method in {"HTML_PRODUCT_LINK", "HTML_METADATA"} and (not url or not product_path(url)):
            continue
        if url and re.search(
            r"/(?:blogs?|tools|apps|calculator|templates|pricing)(?:/|$)", urlsplit(url).path
        ):
            continue
        if url:
            url = re.sub(r"/collections/[^/]+/products/", "/products/", url)
        parent = value.get("isVariantOf")
        parent_url = safe_url(
            website_url, parent.get("url") if isinstance(parent, dict) else parent, True
        )
        if parent_url and parent_url in products:
            record = products[parent_url]
            variant = {k: v for k, v in {"name": name, "sku": plain(value.get("sku"))}.items() if v}
            if variant not in record["variants"]:
                record["variants"].append(variant)
            continue
        if parent_url:
            url = parent_url
        external = plain(value.get("productID") or value.get("@id"))
        matching = next(
            (
                k
                for k, p in products.items()
                if (external and p.get("external_id") == external)
                or (p["name"].casefold() == name.casefold() and (not url or not p.get("url")))
            ),
            None,
        )
        key = (
            matching
            or url
            or "record:" + hashlib.sha256((external or name.casefold()).encode()).hexdigest()
        )
        if key not in products and len(products) >= limit:
            continue
        image = value.get("image")
        image = image[0] if isinstance(image, list) and image else image
        image = image.get("url") or image.get("src") if isinstance(image, dict) else image
        brand = value.get("brand")
        brand = brand.get("name") if isinstance(brand, dict) else brand
        category = value.get("category")
        category = category if isinstance(category, list) else [category]
        if doc.metadata.get("category") == "category" and doc.metadata.get("heading"):
            category = [*category, doc.metadata["heading"]]
        variants = value.get("variants") or value.get("hasVariant") or []
        variants = [variants] if isinstance(variants, dict) else variants
        normalized_variants = []
        if isinstance(variants, list):
            for v in variants[:250]:
                if isinstance(v, dict):
                    normalized_variants.append(
                        {
                            k: plain(v.get(k))
                            for k in ("id", "title", "name", "sku")
                            if plain(v.get(k))
                        }
                    )
                elif isinstance(v, str):
                    normalized_variants.append({"name": v[:300]})
        record = {
            "name": name[:300],
            "url": url,
            "image_url": safe_url(doc.url, image),
            "categories": [str(c)[:300] for c in category if c],
            "brand": plain(brand),
            "sku": plain(value.get("sku")),
            "external_id": plain(value.get("productID") or value.get("@id")),
            "price": money(offers.get("price")),
            "currency": plain(offers.get("priceCurrency")),
            "compare_at_price": money(offers.get("compare_at_price")),
            "availability": offers.get("availability"),
            "variants": normalized_variants,
            "source_urls": [doc.url],
            "discovered_at": discovered_at,
            "observed_at": doc.fetched_at.isoformat(),
            "price_observed_at": doc.fetched_at.isoformat() if money(offers.get("price")) else None,
            "discovery_method": method,
            "price_is_observed": True,
            "observation_basis": "FIRST_LISTED_OFFER_OR_VARIANT",
        }
        if key in products:
            existing = products[key]
            existing["source_urls"] = list(
                dict.fromkeys(existing["source_urls"] + record["source_urls"])
            )
            existing["categories"] = list(
                dict.fromkeys(existing["categories"] + record["categories"])
            )
            for field, val in record.items():
                if existing.get(field) in (None, [], "") and val not in (None, [], ""):
                    existing[field] = val
        else:
            products[key] = record
    # Synonym keys deduplicate signals. Display names are literal matches from sources,
    # not a hardcoded brand-specific list of assumed audiences.
    patterns = [
        r"acne|breakouts?",
        r"oily skin|oiliness|excess oil",
        r"dark spots?|pigmentation|uneven skin tone",
        r"dryness|dehydration|dry skin",
        r"hair fall|hair loss|scalp irritation|dandruff",
        r"wrinkles?|signs of aging|signs of ageing",
        r"sensitive skin|skin irritation",
        r"hidden ingredients|added sugar",
        r"manual work|time.consuming tasks?",
        r"career transition|skills? gap|lack of experience",
    ]
    for doc in bundle.documents:
        if doc.source_type == "catalog":
            contexts = [
                (p.get("name", ""), "PRODUCT_METADATA", p.get("url"))
                for _, d, p in candidates
                if d is doc
            ]
        else:
            contexts = [(doc.text, "PAGE_PROBLEM_SIGNAL", doc.url)]
            contexts += [
                (link.get("label", ""), "NAVIGATION_OR_CATEGORY", link.get("url"))
                for link in doc.metadata.get("navigation", [])
                if isinstance(link, dict)
            ]
        for text, method, related_url in contexts:
            for index, pattern in enumerate(patterns):
                match = re.search(r"\b(?:" + pattern + r")\b", str(text), re.I)
                if not match:
                    continue
                key = f"problem-{index}"
                description = next(
                    (
                        part.strip(" #-\t")
                        for part in re.split(r"[\n.!?]", str(text))
                        if re.search(r"\b(?:" + pattern + r")\b", part, re.I)
                        and len(part) < 300
                        and "%" not in part
                    ),
                    None,
                )
                item = concerns.setdefault(
                    key,
                    {
                        "name": match.group(0),
                        "description": description,
                        "source_urls": [],
                        "product_urls": [],
                        "discovery_methods": [],
                        "offering_keys": [],
                        "interpretation": "WEBSITE_DERIVED_CONCERN_NOT_MEASURED_BEHAVIOR",
                        "discovered_at": discovered_at,
                        "observed_at": doc.fetched_at.isoformat(),
                    },
                )
                item["source_urls"] = list(dict.fromkeys(item["source_urls"] + [doc.url]))
                item["discovery_methods"] = list(
                    dict.fromkeys(item["discovery_methods"] + [method])
                )
                canonical = safe_url(website_url, related_url, True)
                if canonical in products:
                    item["product_urls"] = list(dict.fromkeys(item["product_urls"] + [canonical]))
    coverage = {
        "product_count": len(products),
        "concern_count": len(concerns),
        "source_pages": len(bundle.documents),
        "catalog_limit_reached": len(products) >= limit,
        "crawl_budget_limited": bundle.coverage.get("budget_limited", False),
        "limitations": [
            "Observed public records only; not a complete SKU inventory.",
            "Missing prices/images stay null; prices are timestamped observations.",
            "Website concern labels are potential needs, not measured customers or personas.",
        ],
        "categories": sorted({c for p in products.values() for c in p["categories"]}),
    }
    return products, concerns, coverage


def persist_discovery(session, brand_id, job_id, bundle, website_url, limit=1000):
    """Upsert observations with stable catalog IDs; never delete history on partial crawls."""
    products, concerns, coverage = discover(bundle, website_url, limit)
    existing = {
        p.product_key: p
        for p in session.scalars(select(CatalogProduct).where(CatalogProduct.brand_id == brand_id))
    }
    by_external = {
        p.details.get("external_id"): p for p in existing.values() if p.details.get("external_id")
    }
    for key, record in products.items():
        row = existing.get(key) or by_external.get(record.get("external_id"))
        if row is None:
            row = CatalogProduct(
                id=uuid.uuid4(),
                brand_id=brand_id,
                product_key=key,
                source_job_id=job_id,
                details=record,
            )
            session.add(row)
            existing[key] = row
        elif datetime.fromisoformat(record["observed_at"]) >= datetime.fromisoformat(
            row.details.get("observed_at", row.details["discovered_at"])
        ):
            record["discovered_at"] = row.details["discovered_at"]
            record["external_id"] = record.get("external_id") or row.details.get("external_id")
            existing.pop(row.product_key, None)
            row.product_key = key
            existing[key] = row
            row.source_job_id, row.details = job_id, record
    ids = {p.details["url"]: str(p.id) for p in existing.values() if p.details.get("url")}
    previous = {
        c.concern_key: c
        for c in session.scalars(
            select(CustomerConcern).where(CustomerConcern.brand_id == brand_id)
        )
    }
    for key, record in concerns.items():
        record["catalog_product_ids"] = [ids[u] for u in record.pop("product_urls") if u in ids]
        row = previous.get(key)
        if row is None:
            session.add(
                CustomerConcern(
                    id=uuid.uuid4(),
                    brand_id=brand_id,
                    concern_key=key,
                    source_job_id=job_id,
                    details=record,
                )
            )
        elif datetime.fromisoformat(record["observed_at"]) >= datetime.fromisoformat(
            row.details.get("observed_at", row.details["discovered_at"])
        ):
            record["discovered_at"] = row.details["discovered_at"]
            row.source_job_id, row.details = job_id, record
    bundle.discovery = {"coverage": coverage, "concerns": list(concerns.values())}
    return coverage


def link_offerings(session, brand_id):
    offerings = list(session.scalars(select(Offering).where(Offering.brand_id == brand_id)))
    products = list(
        session.scalars(select(CatalogProduct).where(CatalogProduct.brand_id == brand_id))
    )
    for product in products:
        matches = [
            o
            for o in offerings
            if (product.details.get("url") and product.details.get("url") == o.details.get("url"))
            or product.details.get("name", "").casefold() == o.name.casefold()
        ]
        product.offering_id = matches[0].id if len(matches) == 1 else None
    by_id = {str(p.id): p for p in products}
    for concern in session.scalars(
        select(CustomerConcern).where(CustomerConcern.brand_id == brand_id)
    ):
        ids = concern.details.get("catalog_product_ids", [])
        keys = {
            o.details.get("key")
            for o in offerings
            for i in ids
            if i in by_id and by_id[i].offering_id == o.id
        }
        concern.details = {**concern.details, "offering_keys": sorted(k for k in keys if k)}
