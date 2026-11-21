# Phase 1B measured quality check — 12 November 2026

The comparison uses actual saved Phase 1 Fastlane/Whole Truth profiles, frozen public crawls,
and recorded DeepSeek Flash responses. Final validation changes were tested by offline
replay. No new paid calls were made after the user requested a testing cost limit. The
remaining live process was terminated and the evaluator's batch live-call path was removed.

## Coverage and retained data

These are counts, not an assertion of universal semantic precision or exhaustive catalogs.
The historical and refreshed crawls differ, so changes include source and pipeline effects.
Audience replay uses only previously retained brand-level stage-2 fields; raw rejected
marketing output was not saved and cannot be recovered without another paid request.

| Case | Historical offerings | Retained Phase 1B offerings | Classified | Brand audience/creative leaves, before → after | Evidence missing from retained leaves |
|---|---:|---:|---:|---:|---:|
| Fastlane | 4 subscription tiers incorrectly represented as offerings | 2 (platform + warmed accounts) | 1/2 | 12 → 13 | 0 |
| The Whole Truth | 4 | 9 (8 food families/products + nutrition course) | 9/9 | 0 → 29 | 0 |
| Newton School | No historical baseline | 7 | 6/7 | No baseline → 0 | 0 |

Fastlane Starter/Growth/Pro are retained as tiers under Fastlane, rather than independent
products. Enterprise was not retained from this model response; the test fixture verifies
that subscription-plan evidence is handled separately. Supported platforms do not become
brand marketing preferences. Warmed accounts remains unclassified rather than guessing.

Whole Truth retains Protein Powder, Plant Protein, Protein Bars, Protein Bars Pro, Mini
Protein Bars, Nut Spreads, Muesli, The Next Gen Range and The Whole Truth Academy. A newsletter
and an ingredient lookup resource were removed from the marketable catalog. This is still
not exhaustive: for example, other families visible on the homepage can remain absent when
the model omits them. Retained classifications are source-grounded PRODUCT/SERVICE labels,
not literal word matching. Variants and family relationships remain reviewable optional data.

Newton's seven course/program names were independently verified literally in collected
sources. Six have sufficient context for a SERVICE classification. Audience output did not
provide usable field-level evidence, so no audience intelligence is retained in this sample.
This is an explicit remaining quality gap, not fabricated personas or a passed quality score.

Refreshed source coverage:

| Case | Pages | Useful categories | Limits/gaps |
|---|---:|---|---|
| Fastlane | 19 | Homepage, pricing, 15 use-case/resource pages | 140,000-character budget reached; story/product/customer-story/FAQ page categories missing |
| Whole Truth | 20 | Homepage, story, 9 category pages, 8 product details | Page limit reached; no dedicated use-case/customer-story/FAQ/pricing pages |
| Newton | 9 | Homepage, story, 7 course/program pages | Sitemap unavailable; missing dedicated category/use-case/customer-story/FAQ/pricing pages |

A missing page category does not mean that related content is absent from all collected
pages; inline FAQs/product JSON-LD are stored separately in source metadata. Soft-404s and
comparison/review/legal pages are excluded from relevant-page coverage.

## Precision and usefulness checks

The deterministic suite uses explicitly constructed supported examples for:

- Fastlane: tiers grouped under a product; integrations are not owned marketing channels.
- Whole Truth: more than four offerings; classification need not occur verbatim; facility
  and newsletter scopes remain distinct; inflated numeric claims are removed.
- Newton: a software development course remains a service even when its text says software.
- Citation safety: malformed paths cannot crash extraction, grouped evidence supports only
  literal catalog values, invented descriptions remain absent, and collection indexes stay aligned.

For the final recorded replay, all 18 retained offering names (2 + 9 + 7) occur literally in
captured sources. This is a bounded name-presence audit, **not** proof that every offering
relationship or other factual field is semantically correct. Classification coverage is
16/18; missing labels remain null. Among the 15 retained Whole Truth/Newton labels, the
source-supported food/course distinctions match the annotated expected PRODUCT/SERVICE
classes. General factual precision has not been independently scored and is not reported
as 100%. Targeted consistency tests and human review complement exact citation checking.

Examples of retained, explicitly INFERRED marketing insights:

- Whole Truth: label-reading in the store aisle; a small sampler before a larger protein
  purchase; a creative contrast between tiny ingredient text and a visible complete list.
- Fastlane: a launch with no visitors; creator samples arriving after a trend has passed;
  creative fatigue and the need to test formats at volume.

These are grounded hypotheses, not established customer behavior or advertising proof.
They are more specific than generic quality/value personas, but usefulness is qualitative,
not an invented numerical score. Some persona labels, cultural context and optional fields
remain empty because individual evidence did not validate.

## Recorded token, latency and cost comparison

Both stages used `deepseek-flash`. The factual stage used effort `none`; the comparison
changed marketing effort. These are original model pipeline times, excluding website
crawling, not offline replay times. Figures include attempts recorded in each selected run.

| Case/config | Input tokens | Output tokens | Seconds | Estimated USD | Result |
|---|---:|---:|---:|---:|---|
| Fastlane, marketing none | 68,528 | 10,377 | 40.193 | 0.033011 | Both stages completed |
| Whole Truth, marketing none | 45,659 | 17,258 | 58.930 | 0.032338 | Both stages completed |
| Newton, marketing none | 89,159 | 12,955 | 59.657 | 0.031644 | Completed, marketing evidence unusable |
| Fastlane, marketing low | 102,775 | 30,025 | 140.278 | 0.046767 | Optional marketing stage failed; facts retained |

No complete low-effort recording exists for Whole Truth or Newton; that comparison was
stopped for cost control. A partial factual Whole Truth response is retained, not counted
as a completed evaluation. This sample does **not** establish that reasoning is better;
low effort cost more and failed on Fastlane. Production stage settings remain independent,
with DeepSeek Flash / effort none for both stages as the economical default. Low/high
reasoning remain explicit per-stage options.

Estimates use explicit conservative peak Flash assumptions: input $0.30, cached input $0.006,
output $1.20 per million tokens, based on the [DeepSeek pricing documentation](https://api-docs.deepseek.com/quick_start/pricing/)
checked on 12 November 2026. Off-peak actual rates may differ. These are **not total billed
session costs**: earlier diagnostic runs and a terminated in-flight call may also be billed,
and older SDK parsing failures did not capture usage. Missing usage/cost is reported as
unavailable, never zero. The final transport captures usage before local JSON validation.

## Reproduce without spending credits

```bash
pytest -q                              # Unit + isolated PostgreSQL; never calls a paid model
python -m evaluations.phase1b --replay-saved --effort none --output evaluations/replays/phase1b-none.json
python -m evaluations.phase1b --replay-saved --effort low --output evaluations/replays/phase1b-low.json
```

Set `TEST_DATABASE_URL` to a disposable PostgreSQL database ending in `_test`; see the README.
The `--live` option no longer exists. Future paid smoke testing should be limited to one
small test using the least expensive suitable model. Provider configuration and both stage
request formats are tested through mocked HTTP responses at no model cost.
