# Delivery verification

Latest verification: 14 November 2026, Python 3.12 and local PostgreSQL 16.

- Full offline suite: **227 passed**, including PostgreSQL/API persistence and mocked OAuth/SSE.
- Existing Fastlane, Whole Truth and Newton saved-response replays completed without network
  crawling or inference. Newton's saved marketing remains honestly insufficient.
- Ruff lint/format and `git diff --check` passed; Alembic detected no schema changes.
- The 179-uncited-fact regression rejects all 179, saves a sparse DRAFT, reports factual
  `INSUFFICIENT_EVIDENCE`, skips marketing, and never retries inference. The raw scalar
  count is 180 including the user-provided website URL anchor.
- Compact citation expansion rejects unknown source IDs, invented excerpts and unsupported
  exact names. Existing ingredient/product/price/certification/claim checks remain in place.
- Separate mocked deadline, disconnection and incomplete-event paths passed.
- No live LLM calls were made. One existing Starlette/httpx deprecation warning remains.

A later, explicitly authorized single live Minimalist test would measure real model citation
compliance and latency. Offline tests prove the control flow and validation behavior, not
real-model quality or speed. See [evidence quality](docs/evidence-quality.md) for details.

## Original delivery record

Verified on 11 November 2026 using Python 3.12.14 and an isolated PostgreSQL 15 server.
All test data and the temporary API used for smoke checks were local to this task.

| Check | Result |
|---|---|
| Unit + PostgreSQL integration suite | **57 passed** |
| Ruff lint | Passed |
| Ruff format check | Passed |
| Installed dependency consistency (`pip check`) | Passed |
| Alembic upgrade → check → downgrade → upgrade → check | Passed; no schema drift |
| Real Uvicorn HTTP startup | Passed |
| HTTP readiness, brand listing, BIP schema, OpenAPI | All returned 200 |
| Live public website crawling | Homepage and about page collected from python.org; no warnings |
| Real OpenAI SDK strict-schema serialization and parsing | Passed using mocked HTTP transport |
| Compose configuration | YAML parsed; service topology and migration dependencies checked |

The suite includes database-level active-job uniqueness, JSONB storage, committed source
snapshots, separate offering rows and ID preservation, concurrent `SKIP LOCKED` claims,
expired-lease recovery, stale completion rejection, revisions, confirmation and preservation
of confirmed profiles after failed extraction. One upstream Starlette/httpx deprecation
warning is emitted by the test client; tests succeed.

Not executed in this environment:

* A paid/live OpenAI request: no `OPENAI_API_KEY` was available. Configure the key and a
  structured-output capable model to perform a real company extraction.
* Docker image builds or Compose execution: Docker's daemon was stopped and the Compose
  plugin was not installed. Persistence and migrations were tested directly on PostgreSQL 15;
  Compose and CI target PostgreSQL 16. The supplied CI workflow runs the suite on PostgreSQL 16
  when this project is placed at a repository root and pushed to GitHub.

No external company profile, invented company facts, API credential or production data is
included in the deliverable. Test fixtures are explicitly synthetic.

## DeepSeek strategy update — 12 November 2026

* **75 tests passed** on Python 3.12 and PostgreSQL 15, including both configured providers
  through async extraction, PostgreSQL draft/source/offerings storage, editing and confirmation.
* The real SDK is exercised with a mocked HTTP transport for both providers: endpoint,
  credential, model, strict JSON schema, refusal, truncation and schema validation.
* Both strategies pass rate-limit retry, bounded timeout and safe authentication-error tests.
* Environment precedence and the credential-free tracked `.env` are covered by tests.
* Ruff lint and formatting checks passed. No database schema changes are required.
* CI now validates Compose configuration as well as running PostgreSQL-backed tests.

DeepSeek uses its documented Responses API schema mode and defaults to `deepseek-flash`.
A live DeepSeek call was not made because no API key was supplied. Put `DEEPSEEK_API_KEY`
in ignored `.env.local` to run a real extraction; keep the tracked `.env` credential-free.

## Optional enrichment update — 12 November 2026

107 tests passed against isolated PostgreSQL 16. Regression tests cover all enrichment
sections, unsupported testimonial removal, preserving supported quotes, clearing inconsistent
pricing, dropping unnamed offerings and rebuilding nested provenance indexes after filtering.
Draft persistence and confirmation are verified with missing testimonials, pricing, personas
and preferences. A missing company name permits a draft but still prevents confirmation.
Ruff lint/format and diff checks passed. The BIP schema remains intact; no migration is needed.
Provider tests also verify bounded malformed/truncated-response retries and that diagnostics
log field locations/types without raw response values.

## Phase 1B — 12 November 2026

- **138 tests passed** against isolated PostgreSQL 16, including two-stage extraction,
  optional marketing failure preserving factual drafts, both provider HTTP boundaries,
  scoped/numeric claims, catalog classification, literal-only grouped citation recovery,
  code-fenced JSON, model/effort overrides, malformed-output usage accounting, JSONB
  metadata, review corrections, revision fencing and confirmation provenance.
- Ruff lint/format and diff whitespace checks passed.
- Alembic 0002 downgrade → upgrade → check passed on the disposable test database;
  no schema drift was detected.
- Docker API/worker images built and migration 0002 applied to the local Docker database.
  Existing profile section checksums, statuses and confirmation timestamps were unchanged.
- Local HTTP readiness, brand listing, schema v2 and legacy-profile reads returned 200.
- Actual public-source snapshots and selected model responses are frozen for Fastlane,
  Whole Truth and Newton; see `evaluations/REPORT.md` for bounded quality measurements,
  estimated cost assumptions and remaining gaps.
- Paid comparison calls were stopped at the user's cost constraint. Final fixes were
  verified with frozen responses and mocked providers only. Batch live evaluation was
  removed; ordinary tests and recorded replays make no paid model requests.

The default remains DeepSeek Flash, now using effort none for both stages to control cost.
Independent stage reasoning/model overrides and the retained OpenAI strategy are covered
by free mocked transport tests. The low-effort comparison is incomplete and is not presented
as evidence that reasoning improves quality. Newton marketing evidence remains a documented
limitation of the recorded sample. No further paid call is needed to reproduce the checks.

## Evidence-quality review — offline only

- **173 tests passed** against isolated PostgreSQL 16 (35 additional quality/API cases).
- Marketing outcomes distinguish parsed-but-empty/mostly-rejected responses with
  `INSUFFICIENT_EVIDENCE`; provider/schema/timeouts remain `FAILED`. Both preserve facts.
- Profile API responses expose stage outcomes and per-stage/per-section validation counts,
  including rejection reasons and safe defaults for unaudited historical profiles.
- Product readiness checks benefits, detailed audience, motivations and properly scoped
  source-backed or explicitly user-confirmed claims; null keys never imply selection.
- Existing Newton/Fastlane/Whole Truth fixtures were replayed without live crawling or
  model inference. Newton's empty retained marketing projection is insufficient evidence.
- Ruff lint/format, Alembic check, dependency consistency and diff checks passed. No BIP
  schema change or database migration is required. One existing Starlette/httpx test-client
  deprecation warning remains.

No paid inference calls were made during this review. Original raw marketing responses were
not archived, so original rejected counts cannot be reconstructed from retained-only replays.
See `evaluations/PHASE1B_REVIEW.md` for detailed counts and remaining limitations.

## Sign in with ChatGPT — 14 November 2026

- **216 offline tests passed** with PostgreSQL 16; OAuth/JWKS/model/SSE calls use
  MockTransport and generated RSA test keys, never a real login or inference request.
- Coverage includes PKCE/state/nonce/signature/issuer/audience/expiry, account/workspace
  binding, missing consent, encrypted owner-only storage, concurrent rotating refresh,
  revoked/expired sessions, account-specific models, late stream failures/usage limits,
  no automatic retries/billing fallback, browser-origin protection, PostgreSQL job binding,
  optional marketing failures preserving factual drafts and encrypted VM session handoff.
- DeepSeek is blocked in the application factory even if explicitly configured. ChatGPT
  OAuth never reads an API key; the retained API-key strategy requires explicit billing
  opt-in and fixes the endpoint to OpenAI. Public/local defaults use plan mode.
- Alembic 0003 downgrade → upgrade → check passed against the disposable PostgreSQL DB;
  no schema drift. The BIP v2 contract remains unchanged.
- Ruff lint/format, dependency consistency, Compose validation, connection-page JavaScript
  syntax and Docker image builds passed. The API/worker remain stopped.

Actual account eligibility, real OAuth admission and model availability await the user's
sign-in; they have not been claimed verified. Hosted commercial usage is deliberately blocked
pending OpenAI preview approval. This is a protected single-workspace local/self-hosted
installation, not multi-tenant SaaS. Existing test-client deprecation warning remains.
See `docs/chatgpt-plan.md` for setup, official documentation and preview limitations.
The local Docker database was upgraded to 0003 with API/worker stopped; existing profile
section checksums, statuses and confirmation timestamps were unchanged. Stopped service
containers were recreated with the new configuration to prevent a later start from reusing
old DeepSeek settings. No sign-in, model listing or inference was performed against a live
account. Model/eligibility checks remain a user-driven setup step.
