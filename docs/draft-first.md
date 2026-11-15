# MVP draft-first brand intelligence

`EXTRACTION_MODE=draft_first` is the default in Settings and the tracked public environment
files. `strict` explicitly selects the original citation-based pipeline. No mode changes the
provider, account, model, reasoning effort, retry count or deadline. The application still
uses PostgreSQL, Alembic, a durable extraction job and its revision/lease fencing. There is
no new service or queue, and no database migration is required: diagnostics use existing JSONB.

## Two small output contracts

Call 1 requests `DraftFactualResult`: `profile` and `warnings`. It contains company identity,
positioning, the existing factual sections, catalog families/products/variants, pricing tiers,
optional ingredients, observed voice and existing content/assets. It excludes audience and
creative hypotheses, evidence arrays, quotations and JSON pointers. Unknown values stay
null/empty. `ingredients` is an additive optional offering field in the existing BIP-v2 schema;
the twelve roots and all existing fields remain compatible.

Call 2 requests `DraftMarketingResult`: audiences, psychology, voice, creative context,
keyed offering insights, warnings and optional brief explanations. No evidence is requested.
The prompt connects hypotheses to product capabilities/use cases and prohibits invented
identities, demographics, guarantees, health outcomes and quantified advertising assertions.
Existing assets and content formats from call 1 are not overwritten by creative hypotheses.

Providers still return the existing internal factual envelope to the worker; its empty
`evidence` array is an adapter detail, not part of either default LLM response schema.
The separate strict contracts and verification functions are preserved and are never run
in draft-first mode. Existing strict tests explicitly opt into strict settings; new tests
exercise production's default, citation-free provider schemas and the streamed OAuth path.

## Basic checks and retention

`app/draft.py` owns the draft-only orchestration and small deterministic checks:

- Schema validation rejects malformed output. Uncited names/offerings/insights are retained.
- Duplicate strings/records are merged. Conflicting scalar values keep the first value and
  generate review warnings rather than guessed combinations.
- Recognizable SaaS plans move beneath an unambiguous software offering. Ambiguous plans
  stay provisional OTHER records and do not make marketing eligible by themselves.
- Obvious supporting resources move to existing assets; course/program context is SERVICE.
  Supported platforms stay distinct from marketing preferences; no channels are auto-added.
- Existing keys are preserved, new keys derive from names, and dangling/cyclic parent
  relationships are cleared with warnings. Family/product/variant distinctions stay editable.
- Unsafe/invalid generated URL fields, negative exact prices, impossible ingredient
  concentrations and impossible founding years are omitted with warnings. These are small
  logical checks, not citation validation or a semantic truth classifier.

Facts succeed only when at least one usable offering remains: a name with a description,
features, benefits, ingredients or use cases, and not an OTHER supporting record. Otherwise,
the partial DRAFT is saved with facts `INSUFFICIENT_DATA` and marketing `SKIPPED`
(`NO_USABLE_OFFERING`). No automatic retry follows this data-quality outcome. Marketing valid
but empty insight output is `INSUFFICIENT_DATA`; a provider/schema/deadline failure is `FAILED`
and retains the entire factual draft. Optional-stage changes are committed together, so
invalid marketing does not partially overwrite the factual profile.

`SUCCEEDED` on an extraction job means a draft was saved, not that both stages produced a
complete campaign brief. `creative_readiness` checks an explicit offering, useful benefits,
audiences/motivations, voice and creative direction. It remains nonblocking. Ordinary
creative briefs no longer require a sourced advertising assertion merely to be useful.
`ready_for_campaign` stays false; campaign generation and claim-approval workflows are out
of scope. Insufficient or missing information is exposed for customer review.

## Honest provenance, claim review and confirmation

Each retained scalar has backend-generated provenance:

- `AI_EXTRACTED`: provisional website-based extraction, not independently verified.
- `INFERRED`: tentative audience/creative interpretation, optionally with a short explanation.
- `USER`: submitted URL/name or customer correction.
- Historical `WEBSITE`/`CLASSIFIED` entries keep their source-validation meanings in old
  profiles and strict mode. They are not relabeled when read, edited or confirmed.

Draft-first citations are empty. The saved crawl still provides document URLs, timestamps,
coverage and traceability at the extraction level. Individual fields are not claimed to
have supporting quotations. Confirmation sets review metadata and preserves origins; it
is not independent scientific verification and does not authorize public advertising claims.

`claim_review` reports paths and fixed risk reasons for prices/promotions, concentrations,
health/efficacy statements, certifications, guarantees and advertising assertions. Draft
values are retained. Customer-confirmed entries become `USER_REVIEWED`, while
`approved_for_advertising` remains false and `requires_advertising_approval` true. A later
ad-generation workflow must perform suitable approval checks. Source-backed historical
entries can report `source_verified`; no record is labeled independently scientifically
verified. This conservative keyword/path flagger can overflag ordinary text or miss unusual
wording; it is intentionally not a regulatory-compliance system.

## API and persistence

No existing route or request body changes. Profile GET, PATCH and confirm responses add:

```json
{
  "extraction_mode": "draft_first",
  "stages": {"facts": "SUCCEEDED", "marketing": "SUCCEEDED"},
  "evidence_validation": {
    "facts": {"status": "NOT_APPLICABLE", "available": false, "accepted": null},
    "marketing": {"status": "NOT_APPLICABLE", "available": false, "accepted": null}
  },
  "completeness": {"offering_count": 3, "usable_offering_count": 3, "requires_human_review": true}
}
```

This is abbreviated; full responses include counts of populated fields/sections, missing
information, readiness, claim-review paths, existing stage errors and extraction metrics.
Evidence acceptance/accuracy is not measured as zero when no evidence was requested.
Completeness, readiness and risk flags are recomputed after edits. Stored extraction audits
and stage outcomes describe the extraction, not a new LLM assessment of edited content.
Historical metadata/audits are preserved; profiles without mode metadata report `legacy`.
When replacing a strict profile, `evidence_validation_history` retains its original audit,
provenance, revision and extraction timestamp, without recursively nesting previous history.
The active profile contains the replacement draft; this is an audit history, not full profile
versioning or a rollback feature.
Relational offering IDs are reused by the existing persistence code for unchanged names.

## Efficiency, unchanged limits and expected impact

Draft-first factual context is distributed across collected documents within
`FACTUAL_CONTEXT_MAX_CHARS=60000`; it also respects the existing input/schema token budget.
Marketing uses deterministic offering-name/URL relevance over bounded source passages
(`MARKETING_CONTEXT_MAX_CHARS=18000`), plus the provisional profile. It never recrawls or
requests a citation per hypothesis. Empty schema sections remain in storage for compatibility.

Expected, **not measured live**: output should spend fewer tokens and less generation time
on repetitive citations, explanations and pointers. Holding profile content fixed, citation
output overhead is eliminated. No reliable percentage reduction or completion-time estimate
is claimed: retaining more product families can increase useful output, and model reasoning,
queueing, networking and OAuth/stream handling still affect elapsed time. Context bounds are
configuration limits, not observed speedups. Timeouts remain 120 seconds factual / 60 seconds
marketing in the shipped environment, with the existing 420-second job deadline and one
attempt. Independently configured models/reasoning and no-billing-fallback behavior remain.

## Offline verification and live-test readiness

Tests cover Minimalist (a bounded real saved snapshot and constructed uncited response),
Whole Truth, Fastlane and Newton (existing snapshots plus explicitly constructed provider
responses), valid/empty citations in internal adapters, partial/empty profiles, multiple
families and variants, duplicates/conflicts, unsafe URLs, numeric issues, malformed outputs,
claim review, marketing failures, historical provenance, PostgreSQL editing/confirmation and
mocked ChatGPT OAuth/account binding/stream completion. These fixtures demonstrate control
flow and retention, not measured model usefulness or factual precision.

No live inference, crawl, account/model discovery or real extraction job was triggered during
this implementation. The local API and worker were left stopped. The backend is ready for
one separately authorized Minimalist live test after restarting/rebuilding the local services.
There is no known code/configuration blocker; a still-authorized ChatGPT session/model must
be available then. Real model compliance, catalog quality, latency and semantic factual
correctness remain unmeasured, and a 60-second marketing timeout remains possible.
