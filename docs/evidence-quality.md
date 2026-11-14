# Evidence quality outcomes and offering readiness

The Phase 1B review identified three correctness gaps: a parsed marketing response was
always marked successful even if every insight was pruned; the profile API hid stored stage
outcomes; and selecting any offering could bypass product-specific creative checks. These
are now addressed without changing the twelve-section BIP, provider interface, job lifecycle
or PostgreSQL schema. Diagnostics use the existing JSONB pipeline metadata (no migration).

## Additive profile response fields

`GET`, `PATCH` and confirm-profile responses now include:

```json
{
  "stages": {"facts": "SUCCEEDED", "marketing": "INSUFFICIENT_EVIDENCE"},
  "evidence_validation": {
    "marketing": {
      "available": true,
      "generated": 3,
      "accepted": 0,
      "rejected": 3,
      "acceptance_rate": 0.0,
      "rejection_reasons": {"MISSING_EVIDENCE": 3},
      "by_section": {
        "creative_context": {
          "generated": 3, "accepted": 0, "rejected": 3,
          "rejection_reasons": {"MISSING_EVIDENCE": 3}
        }
      },
      "ignored_evidence_entries": 0
    }
  }
}
```

This is an abbreviated example: available audits include all twelve sections, including
zero-count sections, and both facts/marketing reports. Existing response fields remain.

Marketing `SUCCEEDED` requires **at least one accepted generated field and at least 50%
acceptance**. A valid response with no generated insights, zero accepted insights, or more
than half its fields rejected returns `INSUFFICIENT_EVIDENCE`. The 50% rule measures evidence
retention, not insight usefulness or semantic precision. Valid partial insights are still
saved and remain reviewable. Insufficient evidence never triggers extra model calls automatically.
`FAILED` denotes a provider, timeout or schema failure rather
than a valid response whose evidence was insufficient.

Facts `SUCCEEDED` now requires a retained, evidence-backed offering or meaningful business
information (positioning, value propositions or company description). Identity, website URL,
legal details and visual/preferences fields alone do not pass. Zero accepted generated facts
always yields factual `INSUFFICIENT_EVIDENCE`; marketing is `SKIPPED` with
`stage_errors.marketing.code = FACTUAL_QUALITY_GATE`. The sparse draft, sources, warnings and
diagnostics are saved for review/editing. There is no automatic inference retry for this
quality outcome. This gate is a minimum usable-context check, not a completeness score.

Optional marketing failures or insufficient evidence never discard validated facts. The
job can be `SUCCEEDED` because a DRAFT was stored, while factual/marketing stage statuses
show an evidence gap or skipped inference. Inspect `stages` to assess extraction quality.
Historical stage records are not retroactively rewritten by this pipeline change.

Legacy profiles without stage metadata return `UNKNOWN`. Older marketing `SUCCEEDED`
metadata without an evidence audit is exposed as `UNKNOWN`, since the original success only
measured parsing. Historical known failures remain `FAILED`. No historical generated/rejected
counts are guessed: missing audits remain `{}`. A new provider failure before a parsed
response returns `available: false` and null counts, distinct from an observed empty valid
response with available zero counts.

## Counting and rejection reasons

Counts refer to populated scalar leaves in the parsed generated output, not numbers of
personas/products or citation entries. They exclude supplied URL/name overrides, offering
selectors, factual anchors used by stage 2, warnings/evidence records and backend-derived
keys/classifications. Lists are counted per populated leaf. Empty/null members do not count.

A generated field is counted once. Aliases track plans moved into pricing tiers, scoped
proof moved into scoped claims, and offering insights mapped into the BIP. Structural pruning
counts otherwise valid children as rejected if their unnamed offering/claim/testimonial or
inconsistent pricing object is removed. Section counts use original BIP ownership, mapping
`offering_insights` to `offerings`. Counts obey `generated = accepted + rejected`, including
per-section totals. Backend-derived replacement classifications are not counted as accepted
model labels when the model's own label failed validation.

Reason codes include `MISSING_EVIDENCE`, `MISSING_CITATIONS`, `UNKNOWN_SOURCE`,
`QUOTE_NOT_FOUND`, `EXACT_VALUE_MISMATCH`, `DUPLICATE_EVIDENCE`, `MISSING_RATIONALE`,
`INFERENCE_NOT_ALLOWED`, `CLASSIFICATION_NOT_ALLOWED`, structural-pruning codes, and targeted
semantic codes such as `NUMERIC_CLAIM_MISMATCH` or `CLAIM_SCOPE_MISMATCH`. Unknown/duplicate
offering insight references have their own codes. Ignored evidence for empty/unknown paths
is counted separately rather than inflating rejected field counts.

The audit stores only counts, fixed reason codes and section names. Logs contain aggregate
counts/reason codes and schema field paths, never raw responses, quotes, private reasoning,
field values or unrecognized model-provided paths. Evidence provenance still retains the
concise review rationale and source quotes as before; those are not copied into diagnostics.

Stage outcomes and audits describe the **extraction that saved this profile**, even after a user edits or
confirms the profile. They are not rewritten as successful LLM results by user corrections.
A failed replacement before factual validation leaves the prior profile and its audit intact;
its failure is available through the extraction job API. Readiness, in contrast, is recomputed from the current profile/provenance on every read.

## Explicit offering readiness

A null key never implicitly selects an offering, including legacy offerings without keys.
An unknown requested key is retained as `requested_offering_key` and produces a review
question. With no resolved explicit selection, readiness remains BRAND scope.

For an explicit valid selection, `offering_assessment` checks four booleans:

- `benefits`: product benefits or offering value propositions are populated.
- `target_audience`: this offering has persona description/roles/goals/pains or segment
  description/needs; a label alone and brand-level audience data are insufficient.
- `motivations`: this offering's psychology contains motivations; brand-level motivations
  do not substitute for product-specific targeting.
- `supported_marketing_claims`: at least one OFFERING-scoped claim matches this offering
  and has supported text/scope/subject provenance. Source-grounded CLASSIFIED scope labels
  are allowed. Claim text must be WEBSITE-sourced or explicitly user-confirmed USER input.
  Confirming an INFERRED claim does not turn it into proof. Facility/newsletter/company-wide
  proof, customer anecdotes and claims about another product do not pass this check.

Missing checks add concrete reasons and customer questions. Existing brand identity, voice
and creative-context checks still apply. Human-confirmed USER claims are assertions approved
by the user, not an independent factual or legal certification. Readiness is a presence/scope/
provenance check, not an entailment engine, and always requires human review. It never blocks
saving, editing or confirming a valid draft.

## Offline verification and remaining gaps

See [the recorded review](../evaluations/PHASE1B_REVIEW.md). Tests use the existing Fastlane,
Whole Truth and Newton snapshots/responses and controlled constructed marketing responses.
No paid inference or live crawling is needed. Raw original stage-2 responses were not saved;
retained-only marketing replay reports carry an explicit `input_basis` label. Their rejected
counts describe the replay projection, not the original generation. New extractions audit
the complete parsed stage-2 response before pruning. Archived Newton marketing remains empty;
this change reports that gap honestly instead of inventing audience intelligence.


## Compact factual citations and pre-pruning accounting

New factual requests use `CitedFactualResult`; the twelve-section BIP, legacy
`FactualResult`/`ExtractionResult`, public profile and persisted provenance stay compatible.
Documents receive request-local IDs (`s0`, `s1`, …) mapped by the backend to saved URLs and
fetch timestamps. A factual result cites one short verbatim excerpt with multiple exact
scalar pointers, avoiding repeated URLs/quotes for adjacent facts:

```json
{
  "citations": [{
    "source_id": "s0",
    "quote": "Acme makes collaboration software.",
    "paths": ["/brand_identity/name", "/brand_identity/description"],
    "origin": "WEBSITE",
    "reasoning": null
  }]
}
```

`profile` and `warnings` accompany `citations` in the actual response. Never omit citations
to save space: omit unsupported facts instead. Each pointer expands to the existing
field-level verification contract. Unknown IDs, absent excerpts, unsupported exact values,
missing evidence, invalid classifications and conflicting duplicate pointers still fail
verification. Ingredients, prices, product facts, certifications and scoped advertising
claims retain all existing evidence and business-consistency checks. Quotes are never
synthesized by the backend.

`evidence_validation.facts.parsed_output` records `populated_fields`, `evidence_entries`
and `evidence_paths` immediately after structured parsing, before expansion/pruning.
`populated_fields` includes the authoritative website URL (and supplied name when present),
whereas `generated` excludes user anchors. Therefore the 179-fact regression records 180
raw populated leaves including the URL, 179 generated/rejected facts, and zero evidence
entries. A separate regression covers exactly 179 raw leaves including the URL anchor.
For historical fixture providers, entries mean original legacy evidence objects;
for the new compact wire format they mean shared citation objects. Logs contain only counts,
fixed rejection codes and stage labels, never model reasoning/content.

## Bounded marketing context and deadlines

Marketing receives the verified profile plus deduplicated verified citation excerpts,
prioritizing the explicitly selected offering. It does not receive the full crawl. Original
URLs/timestamps are retained; validation still checks the full saved sources. Excerpts are
bounded by `MARKETING_CONTEXT_MAX_CHARS` (18,000 default), and the serialized profile/schema
must fit the existing input budget. No fallback to a full-crawl request occurs when empty.
This can reduce discovery of creative ideas in uncited copy; it intentionally favors
traceable context over broader speculation.

`FACTUAL_TIMEOUT_SECONDS` and `MARKETING_TIMEOUT_SECONDS` override the backwards-compatible
`LLM_TIMEOUT_SECONDS` default. Shipped development configuration uses 120 seconds for facts
and 60 for the smaller marketing request; the 420-second job deadline remains authoritative.
ChatGPT transport errors are distinct: `CHATGPT_TIMEOUT` (local deadline/network timeout),
`CHATGPT_STREAM_DISCONNECTED` (transport error or EOF without completion), and
`CHATGPT_RESPONSE_INCOMPLETE` (explicit upstream incomplete event). Error records retain a
safe code; request IDs are captured where available. Interrupted inference is never retried
or switched to API-key billing. Optional marketing errors still preserve the factual draft.

## Verification of the Minimalist failure fix

Regression tests reconstruct the known failure shape using mocked structured output and a
saved website snapshot: 179 generated leaves, empty evidence, zero retained facts/offerings,
reviewable draft and no marketing call. The mocked ChatGPT SSE path additionally verifies
one request even with a higher configured retry allowance. PostgreSQL/API tests verify the
same outcome survives persistence. Existing Fastlane, Whole Truth and Newton fixtures are
replayed offline; no live inference or website capture is needed.

A single user-authorized live Minimalist test is warranted later to measure whether the
selected model follows the compact citation contract and whether the smaller marketing
request meets its deadline. It is **not** needed to verify the quality gate, and was not run
for this fix. Offline mocks cannot establish real model citation compliance, latency,
semantic entailment or completeness. The original Minimalist raw model JSON was not saved,
so the regression reconstructs its documented shape rather than claiming an exact replay.
