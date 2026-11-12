# Delivery verification

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
