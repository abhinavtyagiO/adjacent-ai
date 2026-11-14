# Phase 1B evidence-quality review — offline only

Reviewed PR #1 at `bf221d0`, then corrected stage outcomes, API visibility, evidence accounting
and offering readiness. No paid inference or website crawling was performed during this review.

## Saved-response results

The following comes from replaying the existing frozen fixtures through the updated pipeline:

| Brand | Factual fields generated / accepted / rejected | Marketing fields in replay / accepted / rejected | Marketing outcome |
|---|---:|---:|---|
| Fastlane | 161 / 31 / 130 | 13 / 13 / 0 | SUCCEEDED for retained-only projection |
| Whole Truth | 228 / 36 / 192 | 29 / 29 / 0 | SUCCEEDED for retained-only projection |
| Newton School | 172 / 24 / 148 | 0 / 0 / 0 | INSUFFICIENT_EVIDENCE |

Fact counts use saved parsed model output. Generated classification labels that were rejected
and subsequently replaced by backend classifications remain rejected in these counts; derived
keys/labels do not inflate acceptance. Low factual retention is visible, not hidden by a
successful draft save. The core retained catalogs remain two Fastlane offerings with separate
tiers, nine Whole Truth offerings and seven Newton offerings.

**Historical marketing limitation:** only previously retained stage-2 fields and provenance
were archived, not raw original marketing responses. The Fastlane/Whole Truth replay therefore
cannot determine the original rejected count or original acceptance percentage. Its successful
outcome applies only to the explicitly labeled retained-only input projection. Newton's saved
projection is empty, so zero accepted insights cannot be called successful. Legacy profile API
responses use UNKNOWN for old marketing successes without audits; they do not invent counts.

Controlled schema-valid marketing fixtures additionally verify:

| Generated fields | Accepted | Rejected | Expected outcome |
|---:|---:|---:|---|
| 0 | 0 | 0 | INSUFFICIENT_EVIDENCE |
| 3 | 0 | 3 | INSUFFICIENT_EVIDENCE |
| 3 | 1 | 2 | INSUFFICIENT_EVIDENCE |
| 2 | 1 | 1 | SUCCEEDED |
| 3 | 3 | 0 | SUCCEEDED |

Tests verify reason codes and section totals, original-field accounting after relocation and
array pruning, unknown/duplicate offering references, safe logging, PostgreSQL JSONB round trips,
API edits/confirmation, legacy defaults and preservation of facts after provider/timeout/schema
failures. Readiness tests cover bare selections, brand-level targeting failing to substitute
for offering details, name-only personas, unsupported/inferred claims, wrong claim scopes,
matching global offering claims, classified scope labels and explicit user confirmation.

## Remaining known gaps

- There is no new live-model quality measurement. These changes make validation outcomes
  accurate; they do not improve or fabricate the archived Newton audience intelligence.
- Most rejected factual leaves have missing or non-verbatim evidence. Prompt/source
  improvements may still be needed before the model reliably provides complete leaf citations.
- Exact citations, targeted semantic checks and a 50% retention threshold do not prove
  semantic correctness or marketing usefulness. Readiness checks structured presence and
  provenance; human review remains necessary.
- Historical raw marketing output cannot be reconstructed. Original rejected-field counts
  remain unknown. Optional provider failures without parsed output also have null counts.
- Existing profiles are not silently re-extracted or relabeled with invented statistics.
  No database migration or BIP version bump is needed for additive response metadata.

## Reproduce for free

```bash
# TEST_DATABASE_URL must target an isolated PostgreSQL database ending in _test.
OPENAI_API_KEY= DEEPSEEK_API_KEY= pytest -q
ruff check .
ruff format --check .
python -m evaluations.phase1b --replay-saved --effort none --output /tmp/phase1b-review/offline-none.json
python -m evaluations.phase1b --replay-saved --effort low --output /tmp/phase1b-review/offline-low.json
```

The evaluator has no live-model path. Replay `input_basis` distinguishes saved factual output
from previously retained marketing fields; original recorded token/cost figures are historical,
not spending from this review. See [the API/counting guide](../docs/evidence-quality.md).

Verified results: **173 tests passed** against isolated PostgreSQL 16. Ruff lint/format,
Alembic schema-drift check, dependency consistency, Compose validation and BIP-v2 schema
compatibility checks passed. One existing test-client deprecation warning remains.
