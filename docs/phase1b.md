# Phase 1B: extraction and marketing intelligence

The durable PostgreSQL worker now runs two primary LLM operations using the selected
provider strategy. API requests still enqueue work immediately. OpenAI and DeepSeek share
schema validation and transport behavior; business rules contain no provider-specific calls.

1. Crawl public sources, persist immutable text snapshots, structured source metadata and
   timestamps. Rank relevant links and same-host sitemap URLs for category diversity rather
   than taking the first links. Comparisons, reviews of other products, legal and checkout
   pages are excluded. Common paths are used only when discovery yields little. Soft-404s
   are rejected. Robots, public DNS pinning and redirect host restrictions remain enforced.
2. Extract factual company/catalog information with a narrower `FactualResult` schema.
   Group SKU variants, retain family relationships, separate subscription tiers and supported
   platforms, and preserve claim scope. There is no four-product cap. Free calculators and
   templates are supporting resources unless explicitly sold as independent offerings.
3. Validate citations against saved sources, prune unsupported optional leaves, reject known
   numeric/scope contradictions, and assign explainable product/service labels and stable
   offering keys. No unsupported company details are supplied to the second stage.
4. Generate audience and creative hypotheses with `MarketingResult`. Every retained insight
   needs a cited source and concise rationale. This schema cannot replace factual identity,
   catalog, pricing or proof. Offering-specific insights reference verified offering keys.
5. Save a DRAFT with all twelve sections, provenance, source coverage, readiness questions
   and per-call telemetry. User correction, revision fencing and confirmation remain intact.

## Configuration

Set secrets only in ignored `.env.local`. Stage models inherit the selected strategy's model
when blank. The initial factual strategy is DeepSeek Flash. Blank effort uses `none` for
both DeepSeek stages; OpenAI omits effort by default. When
switching OpenAI to a reasoning-capable model, choose an effort supported by that model.

| Setting | Default | Purpose |
|---|---|---|
| `FACTUAL_MODEL`, `MARKETING_MODEL` | Inherit provider model | Independently select stage models |
| `FACTUAL_REASONING_EFFORT`, `MARKETING_REASONING_EFFORT` | Provider defaults above | Independent reasoning configuration |
| `FACTUAL_MAX_OUTPUT_TOKENS` | 24000 | Factual response allowance |
| `MARKETING_MAX_OUTPUT_TOKENS` | 12000 | Audience/creative response allowance |
| `LLM_MAX_INPUT_TOKENS` | 50000 | Conservative character-based input estimate including schema/context |
| `LLM_MAX_TOTAL_TOKENS` | 160000 | Per-attempt pipeline token budget, including retries |
| `CRAWL_MAX_PAGES` | 20 | Collected content documents |
| `CRAWL_MAX_REQUESTS` | 40 | Fetch operations including robots/sitemaps/content; redirects separately capped |
| `CRAWL_MAX_TOTAL_CHARS` | 140000 | Total readable source text |
| `CRAWL_MAX_SITEMAPS` | 4 | Same-host XML sitemap fetch limit |
| `CRAWL_MAX_LINKS` | 2000 | Discovered candidate limit |
| `CRAWL_MAX_ELAPSED_SECONDS` | 90 | Collection elapsed budget, checked between fetches |
| `LLM_*_USD_PER_MILLION` | Unset | Input/output/cached-input rate assumptions for estimated cost |

Input estimation is conservative, not a tokenizer guarantee or dollar spending cap. Before
an attempt, reserve estimated input plus maximum output; replace that reservation with
reported usage afterward. An attempt with no usage retains its full reservation. Retries
and whole-job retries can incur additional charges. Rate assumptions must match both stage
models; use identical models for a single configured cost estimate, otherwise leave rates
unset and calculate separately from the model-tagged call records. Never log prompts,
website payloads, credentials or private model reasoning.

## Review contract and failures

`GET /v1/profile-schema` returns schema version 2. Existing v1 rows read with defaults for
new optional fields; migration 0002 adds empty JSONB metadata without replacing existing
profiles. New extraction writes v2. `source_coverage` reports actual page categories, missing
categories and budget limitations; missing sources mean unknown coverage, not that a brand
has no corresponding content. Inline JSON-LD is retained in source metadata. Crawling does
not execute JavaScript and cannot guarantee exhaustive catalogs.

Provenance origins are `WEBSITE`, `CLASSIFIED`, `INFERRED` and `USER`. Confirmation adds
`confirmed_by_user` and `confirmation_revision`, retaining origin. Persona/segment names may
be descriptive hypotheses; demographics, real identities and company facts cannot be invented.
The model boundary normalizes equivalent dotted/indexed evidence paths to JSON pointers,
a single JSON code fence, and null optional objects to empty objects. It never repairs
malformed JSON or invents field values. Citation, name, testimonial and pricing checks remain.
If a model groups catalog citations, literal child values can be recovered only when they
occur in that cited passage. Missing offering-name evidence on a partially cited catalog entry can be recovered
from an exact name match in the collected text, with a fresh verbatim citation. No descriptive
paraphrase, classification label or audience hypothesis is filled this way.
Targeted semantic checks catch tier/product confusion, supported-platform/channel confusion,
course/product context, numeric inflation and facility/newsletter/customer claim scope. They
are not a general entailment engine: user review remains necessary.

Facts-stage credential, network, timeout or invalid JSON failures fail/retry the job safely.
Optional unsupported leaves are pruned with warnings. Marketing-stage provider/schema/budget
failures or its stage timeout retain the factual draft with a warning. An overall worker
shutdown/lease loss still uses the durable retry mechanism. Job metrics contain attempt usage
on failure as well as success. Unknown token usage/cost remains explicitly unavailable.
Malformed/truncated responses are retried within both attempt and token budgets; raw upstream
errors are never returned to API users.

`creative_readiness` lists missing-information reasons and suggested customer questions.
It is recomputed after edits. Without a selected offering its scope is BRAND and it flags
that product-specific targeting is still needed. To target an offering during deliberate
re-extraction, pass its stable key:

```json
{"replace_existing": true, "offering_key": "protein-bars"}
```

An unresolved key leaves brand-level readiness with a review question. Readiness never
blocks saving a draft or confirming it. Confirming acknowledges review; it does not establish
that a marketing hypothesis is true. A replacement remains opt-in and revision-fenced.

## Evaluation

```bash
# Capture real public sources; does not call a model.
python -m evaluations.phase1b --capture
# Replay recorded responses; NEVER calls a model or a website.
python -m evaluations.phase1b --replay-saved --effort none --output evaluations/replays/phase1b-none.json
python -m evaluations.phase1b --replay-saved --effort low --output evaluations/replays/phase1b-low.json
```

The evaluator writes revalidated profiles/evidence, coverage and recorded per-stage model/
usage/latency/cost metrics. Batch live evaluation is disabled to control testing costs. No
additional paid tests were run after the cost constraint; any future model smoke test should
be one small test on the lowest-cost suitable model, without retry or batch loops. Actual Phase 1 Fastlane/Whole Truth baselines are frozen in
`tests/fixtures/phase1b/phase1_snapshots.json`. Newton has no historical profile baseline.
Constructed supported-example fixtures are explicitly labeled and exercise targeted semantic
checks deterministically. Recorded model results were nondeterministic at generation time. Revalidation of frozen data is deterministic. See
`evaluations/REPORT.md` for measured results and precision/usefulness limitations. Offline
unit and PostgreSQL tests never need a model key or paid request.

## Evidence-quality follow-up

The [evidence-quality guide](evidence-quality.md) defines `INSUFFICIENT_EVIDENCE`, additive
profile stage/audit fields, count semantics and stricter explicit-offering readiness. These
outcomes describe extraction history, while readiness reflects the current edited profile.
Zero retained marketing insights are never reported as a fully successful marketing stage.
