"""Explicit customer quotations and visual declarations from static public HTML/CSS.

No rendering, image-color guessing or arbitrary script execution. Signals are observations,
not independently verified testimonials, efficacy claims or official brand guidelines.
"""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.crawling.policy import asset_label
from app.crawling.urls import normalize_url


def public_link(base, value):
    try:
        return normalize_url(urljoin(base, str(value)))
    except ValueError:
        return None


def css_colors(css, source_url):
    colors = []
    for name, value in re.findall(
        r"(--[\w-]*(?:brand|primary|secondary|accent)[\w-]*)\s*:\s*(#[\da-fA-F]{3,8})(?=\s*[;}]|\s*$)",
        css[:200000],
    ):
        if len(value) not in (4, 5, 7, 9):
            continue
        signal = {
            "name": name,
            "value": value.lower(),
            "kind": "NAMED_CSS_COLOR",
            "scope": "OBSERVED_WEBSITE_STYLE",
            "source_url": source_url,
        }
        if signal not in colors:
            colors.append(signal)
    return colors[:16]


def extract_signals(soup, url, structured_reviews=()):
    testimonials = []

    def add(quote, attribution=None, method="HTML_QUOTATION"):
        text = " ".join(BeautifulSoup(str(quote), "html.parser").get_text(" ", strip=True).split())
        if not 12 <= len(text) <= 1600 or any(t["quote"] == text for t in testimonials):
            return
        testimonials.append(
            {
                "quote": text,
                "attribution": attribution or None,
                "method": method,
                "source_url": url,
                "interpretation": "PUBLISHED_QUOTATION_NOT_VERIFIED_RESULT",
            }
        )

    for review in structured_reviews:
        if isinstance(review, dict) and review.get("quote"):
            add(review["quote"], review.get("attribution"), "JSON_LD_REVIEW")
    selectors = (
        'blockquote,[itemprop="reviewBody"],[data-testimonial-quote],'
        ".testimonial-quote,.testimonial_quote,"
        '[class*="testimonial"][class*="quote"],'
        '[class*="testimonial"][class*="text"],[class*="review"][class*="text"]'
    )
    for tag in soup.select(selectors)[:40]:
        cite = tag.find("cite")
        # A cite is attribution, not part of the actual quote.
        clone = BeautifulSoup(str(tag), "html.parser")
        for c in clone.select("cite"):
            c.decompose()
        add(clone.get_text(" ", strip=True), cite.get_text(" ", strip=True) if cite else None)
    visual = []
    for tag in soup.select("style")[:20]:
        visual.extend(css_colors(tag.get_text(), url))
    inline = "\n".join(str(tag.get("style", "")) for tag in soup.select("[style]")[:300])
    visual.extend(css_colors(inline, url))
    for tag in soup.select("[data-brand-color],[data-color-hex]")[:20]:
        value = str(tag.get("data-brand-color") or tag.get("data-color-hex") or "").strip()
        if re.fullmatch(r"#[\da-fA-F]{3}(?:[\da-fA-F]{3})?", value):
            visual.append(
                {
                    "name": str(
                        tag.get("data-color-name") or tag.get("aria-label") or "Declared color"
                    )[:100],
                    "value": value.lower(),
                    "kind": "EXPLICIT_PALETTE_LABEL",
                    "scope": "SITE_DECLARED_PALETTE",
                    "source_url": url,
                }
            )
    stylesheets = []
    for tag in soup.select('link[rel~="stylesheet"][href]')[:10]:
        target = public_link(url, tag["href"])
        if target and target not in stylesheets:
            stylesheets.append(target)
    kits = []
    for tag in soup.select("a[href]")[:4000]:
        label = tag.get_text(" ", strip=True)
        if asset_label(label):
            target = public_link(url, tag["href"])
            if target:
                kits.append({"url": target, "label": label[:200]})
    alternatives = []
    for tag in soup.select('link[rel~="alternate"][hreflang][href],a[hreflang][href]')[:30]:
        target = public_link(url, tag["href"])
        lang = str(tag["hreflang"]).lower()
        if target and re.fullmatch(r"[a-z]{2,3}(?:-[a-z]{2})?", lang):
            alternatives.append({"url": target, "language": lang})
    unique = []
    for v in visual:
        if v not in unique:
            unique.append(v)
    return {
        "testimonials": testimonials[:12],
        "visual_signals": unique[:20],
        "stylesheets": stylesheets,
        "brand_asset_links": kits[:10],
        "language": soup.html.get("lang") if soup.html else None,
        "alternate_locales": alternatives,
    }


def signal_text(signals):
    parts = []
    for t in signals.get("testimonials", [])[:5]:
        parts.append(
            "Website quotation (not independently verified performance): "
            + t["quote"]
            + (" — " + str(t["attribution"]) if t.get("attribution") else "")
        )
    for v in signals.get("visual_signals", [])[:12]:
        parts.append(f"Observed visual declaration [{v['scope']}]: {v['name']} = {v['value']}")
    for link in signals.get("brand_asset_links", [])[:4]:
        parts.append("Site-linked brand/press resource (contents may be unfetched): " + link["url"])
    return "\n".join(parts)
