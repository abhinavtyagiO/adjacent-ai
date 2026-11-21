"""Content-oriented page classification and bounded crawl allocation."""

import re
from urllib.parse import urlsplit

CATEGORIES = (
    "homepage",
    "brand_story",
    "use_case",
    "customer_story",
    "brand_assets",
    "education",
    "audience_content",
    "faq",
    "category",
    "product",
    "pricing",
)
SCORES = {
    "homepage": 1000,
    "customer_story": 150,
    "brand_assets": 145,
    "use_case": 140,
    "brand_story": 130,
    "faq": 120,
    "education": 115,
    "audience_content": 110,
    "category": 95,
    "product": 65,
    "pricing": 55,
    "other": 0,
    "comparison": -1,
}
DEFAULT_LIMITS = {
    "homepage": 1,
    "customer_story": 3,
    "brand_assets": 2,
    "use_case": 3,
    "brand_story": 2,
    "faq": 2,
    "education": 2,
    "audience_content": 2,
    "category": 4,
    "product": 3,
    "pricing": 1,
    "other": 0,
    "comparison": 0,
}
CONTEXT_WEIGHTS = {
    "homepage": 2,
    "customer_story": 2,
    "brand_assets": 1.5,
    "use_case": 1.8,
    "brand_story": 1.5,
    "faq": 1.5,
    "education": 1.3,
    "audience_content": 1.5,
    "category": 1,
    "product": 0.8,
    "pricing": 0.6,
}
MARKETING_SCORES = {
    "customer_story": 10,
    "use_case": 8,
    "brand_story": 7,
    "faq": 7,
    "education": 6,
    "audience_content": 7,
    "homepage": 5,
    "category": 3,
    "product": 2,
    "pricing": 1,
    "brand_assets": 0,
}


def asset_label(label):
    return bool(
        re.search(
            r"\b(?:brand[ -]?(?:kit|assets?|guidelines?|identity|resources?)"
            r"|press[ -]?kit|media[ -]?kit|visual identity)\b",
            label,
            re.I,
        )
    )


def page_category(url: str, label: str = "") -> str:
    path = urlsplit(url).path.lower().rstrip("/")
    context = path + " " + label.lower()
    if re.search(
        r"^/(?:review/[^/]+|compare(?:/|$)|comparison(?:/|$)|alternatives(?:/|$))", path
    ) or re.search(r"\bvs\b|compar(?:e|ison)|alternatives", context):
        return "comparison"
    if re.search(r"^/(?:terms|privacy|search|cart|checkout|login|sign-in)(?:/|$)", path):
        return "comparison"
    if path.startswith("/pricing/") and path.rsplit("/", 1)[-1] not in {
        "starter",
        "growth",
        "pro",
        "enterprise",
        "plans",
    }:
        return "comparison"
    if re.search(
        r"/(?:brand[/-]?(?:kit|assets?|guidelines?)|press(?:-kit)?|media-kit)(?:[/.]|$)", path
    ):
        return "brand_assets"
    if re.search(
        r"/(?:reviews?(?:-\d+)?|testimonials?|customers|customer-stor(?:y|ies)|case-stud(?:y|ies)|success-stor(?:y|ies)|placement-stor(?:y|ies))(?:/|$)",
        path,
    ):
        return "customer_story"
    # Product names/anchor labels may contain "testimonial" or "kit". Their
    # product URL is still a product detail, not a site-wide story or brand kit.
    if re.search(
        r"/products?/[^/]+|/courses?/[^/]+|/programs?/[^/]+|/p/[^/]+", path
    ) or path.endswith(("-program", "-course")):
        return "product"
    if not path:
        return "brand_assets" if asset_label(label) else "homepage"
    if re.search(r"/(?:faqs?|frequently-asked[^/]*|questions)(?:/|$)", path):
        return "faq"
    if re.search(r"/(?:our-story|brand-story|about[^/]*|our-mission|our-values)(?:/|$)", path):
        return "brand_story"
    if re.search(r"/(?:pricing|plans)(?:/|$)", path):
        return "pricing"
    if re.search(r"/(?:use-cases?|solutions|who-we-help|for|tools)(?:/|$)", path):
        return "use_case"
    if re.search(
        r"/(?:ingredients?|our-ingredients|ingredient-glossary|our-science|science|quality|our-standards|test-reports?|lab-results?)(?:/|$)",
        path,
    ):
        return "education"
    if path.startswith(("/blog/", "/learn/", "/articles/")):
        return (
            "audience_content"
            if re.search(r"/(?:why-|how-to-|guide-to-|routine-for-)", path)
            else "other"
        )
    if re.search(
        r"/(?:collections?|categories|category|shop|products?|services?|courses?|programs?|platform)(?:/|$)",
        path,
    ):
        return "category"
    if asset_label(label):
        return "brand_assets"
    if re.search(
        r"\b(?:testimonials?|customer stories|case studies|customer reviews|reviews)\b", label, re.I
    ):
        return "customer_story"
    if re.search(r"\b(?:who.*for|use cases|how it works|solutions)\b", label, re.I):
        return "use_case"
    return "other"
