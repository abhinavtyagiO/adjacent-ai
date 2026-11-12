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

Facts `SUCCEEDED` continues to mean that the factual draft was validated/saved; it does not
promise a complete catalog or high retention ratio. Fact diagnostics and readiness make
missing content visible. Optional marketing failures or insufficient evidence never discard
a validated factual draft. The overall extraction job can be `SUCCEEDED` because a DRAFT
was stored, while its marketing stage is `FAILED` or `INSUFFICIENT_EVIDENCE`.

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
