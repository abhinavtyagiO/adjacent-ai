# Content-first crawling

The goal is enough positioning, customer language and product-family understanding to
suggest useful audiences and creative angles. Discovering a URL does not guarantee a
visit: category caps, language duplication, host policy, robots and shared budgets apply.
`source_coverage` in the profile API now makes those reasons visible.

## Allocation without raising budgets

Global defaults remain **20 documents, 40 fetch operations, 90 seconds, 140,000 stored
HTML-text characters**, at most four sitemaps and three public catalog pages. Catalog
pages count toward the document limit. Robots, catalogs, sitemaps and stylesheets share
the operation/time/response-size ceilings. Fetch operations count calls to the transport;
redirect hops and bounded transport retries may issue additional HTTP requests.

| Priority | Content category | Maximum attempted content URLs |
|---|---|---:|
| Starting point | Homepage | 1 |
| High | Customer stories, own-site reviews and testimonials | 3 |
| High | Brand/press assets | 2 |
| High | Use cases / solutions | 3 |
| High | Story, positioning, mission | 2 |
| Supporting | FAQs | 2 |
| Supporting | Ingredient, science, quality and test-report education | 2 |
| Supporting | Selective how-to / why audience articles | 2 |
| Broad product coverage | Collection/category pages | 4 |
| Representative depth | Product detail pages | 3 |
| Lower | Pricing | 1 |

Caps count attempts, including failed/robots-blocked URLs, so broken sections cannot
consume the entire crawl. These are ceilings, not guaranteed slots. Common paths remain
a fallback; unlinked guessed testimonials are not assumed to exist. Navigation labels
help identify opaque URLs such as `/resources/42` labeled Brand Kit. An own-site
`/pages/reviews-1` is customer proof; `/review/another-company` remains excluded as a
competitor comparison. Product links keep their product category even when their anchor
labels contain “testimonial.” A small how-to/why selection is permitted, not a whole SEO
archive.

Sitemaps run after five collected documents, or earlier when eligible navigation runs
out. This avoids spending early operations on sitemap indexes before useful footer links.
Declared `hreflang` alternatives, with the current HTML language, exclude translated
paths; arbitrary two-letter paths are not presumed languages. Disable with
`CRAWL_SKIP_ALTERNATE_LOCALES=false`. Shopify's collection/product aliases canonicalize
to the same product detail URL after the platform is detected.

Public catalog metadata and product links still feed deterministic discovery (up to
1,000 observed products by default), independently of the three deep product visits.
This improves breadth without promising complete per-SKU ingredients, variants or prices.
Absent details stay absent. No additional LLM requests are introduced.

## Customer language and visual assets

Static ingestion retains JSON-LD Review bodies, explicit testimonial/review text and
blockquotes, including only published attribution. Quotes are **published quotations,
not independently verified results**, typical outcomes or approved claims. They can inform
audience hypotheses and creative angles. Do not invent customer identities or infer a
clinical result from a testimonial.

Color signals come from exact named CSS variables (brand, primary, secondary, accent)
and explicitly labeled palette attributes. CSS signals carry `OBSERVED_WEBSITE_STYLE`;
explicit palette attributes carry `SITE_DECLARED_PALETTE`. Both retain source URLs.
At most two same-origin stylesheets are fetched, with one slot reserved for a later brand
kit when ordinary pages supply CSS. Their fetches use the same robots, pinned public DNS,
redirect, byte and time checks. External CDN CSS is skipped. Browser `theme-color` remains
a UI hint; arbitrary CSS colors and image pixels are not promoted to official brand colors.
Compact observations are included near the start of each source's text, with their scope.

External kits require a per-company mapping plus an explicit brand/press link from the
company site. The committed development configuration contains the actual link found in
saved Opal navigation:

```dotenv
CRAWL_BRAND_ASSET_HOSTS={"opalapp.com":["brandkit.opal.so"]}
```

A mapping alone does not start a fetch. Arbitrary links to that host do not grant access.
Once reached, only recognized brand/press pages within that same exact host are eligible;
redirects cannot escape it. Each asset origin has its own robots policy. Public DNS checks
still run even for mapped hosts. No wildcard, private IP, userinfo, URL path or port is
accepted in the mapping. Unmapped brand resources are recorded as discovered/unfetched,
not represented as read guidelines. A commercial cloud document, JS-only kit, PDF or
bot-protected page may still need manual input; no login or browser renderer is added.

## What “claim approval” means

It means a person should review proposed advertising statements before publishing them,
especially efficacy, health, numerical or testimonial-based claims. It does **not** block
crawling testimonials, reviews or brand kits. Profile confirmation and successful extraction
do not grant advertising approval. The existing conservative claim-review behavior and
strict evidence verifier are unchanged.

## Model context and diagnostics

Draft factual context uses weighted, bounded allocation: customer stories, homepage and
use cases receive more space than repeated product/pricing details. Short documents
return unused space to the remaining pages. Marketing passages prioritize audience/proof
context and the selected offering; the compact brief also retains marketing guardrails.
The existing 60,000 factual / 18,000 marketing character budgets, model selection,
reasoning settings, stage deadlines and one-attempt settings remain unchanged.

`source_coverage` adds:

- `policy`, `category_limits`, `category_attempts`, and `category_capped_urls`;
- `skipped` counts for observed locale/duplicate-product/candidate/style exclusions;
- `alternate_locale_urls_skipped`, `unfetched_external_brand_assets`, and `stylesheets_checked`;
- `pending_eligible_urls`, `budget_limited`, and `stop_reason` (`GLOBAL_BUDGET`,
  `CATEGORY_LIMITS`, or `NO_ELIGIBLE_URLS`).

Counters describe eligible candidates known to this bounded crawl, not the whole website.
Locale discovery skips and pending locale exclusions are separate counters and must not
be summed as distinct URL totals. Warnings retain safe URL/error codes, with no raw model
reasoning. Category attempts include failures; category document counts include only
collected content. Existing profile schemas and API routes are preserved; no migration.

## Offline validation and limits

`tests/fixtures/content-navigation.json` copies actual saved homepage navigation from
Opal and Minimalist. Tests reconstruct navigation with synthetic page responses: Opal's
brand kit and Minimalist's reviews are now visited, arbitrary external links are excluded,
and the total budgets/caps still apply. No actual kit contents or palette were available
in the saved snapshots, so test colors/testimonials are explicitly synthetic.

Tests also cover product breadth versus duplicate depth, sitemap ordering, declared locales,
CSS/robots/request budgets, exact quotations and context prioritization. This work makes
no live crawl or LLM request. Selection improvements are verified offline; actual Opal kit
accessibility, brand-palette fidelity and improved live audience/creative outputs remain
unmeasured. Existing drafts are unchanged; new crawling requires a separately requested
extraction. Marketing-only enrichment reuses saved sources and cannot recover previously
unfetched pages.
