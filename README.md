# Brand Intelligence Backend — Phase 1

A FastAPI backend for registering a company URL, crawling public pages, extracting an
evidence-aware Brand Intelligence Profile (BIP), reviewing/correcting it and confirming it.
PostgreSQL is the only supported database in development, tests and deployment.

## Quick start with Docker Compose

Requires Docker with Compose v2.24+ (for the optional private environment file).

```bash
cp .env.example .env.local
# Set DEEPSEEK_API_KEY in .env.local. Optionally change DEEPSEEK_MODEL.
docker compose up --build -d
docker compose logs -f api worker
```

The repository includes a credential-free `.env` with `LLM_PROVIDER=deepseek`. Private
overrides belong in the ignored `.env.local`, which Compose loads after `.env` for the
API, worker and migrations. Compose starts PostgreSQL 16, runs Alembic migrations once,
then starts the API and a separate extraction worker. API: <http://localhost:8000>, Swagger review/testing UI:
<http://localhost:8000/docs>. `GET /health/ready` checks the database and migration table.
Ports bind to localhost. Database data persists in the `postgres_data` volume.

```bash
docker compose down                  # Stop services, retain database volume
docker compose run --rm migrate      # Apply later migrations
```

## Local Python development

Use Python 3.12+ and a local PostgreSQL server, or let Compose run only the database.
No SQLite fallback exists.

```bash
cp .env.example .env.local
# Set DEEPSEEK_API_KEY in .env.local.
docker compose up -d db
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.lock
pip install --no-deps -e .
alembic upgrade head
uvicorn app.main:app --reload
```

In another terminal, activate the same environment and start the worker:

```bash
brand-worker
# Equivalent: python -m app.worker
```

The default `DATABASE_URL` targets the Compose database on `localhost:5432`. An existing
PostgreSQL server can be used by changing that variable. The API never creates tables at
startup: migrations are required. Run commands from this project directory so `.env` and
`.env.local` load. For Python processes, precedence is environment variables, then
`.env.local`, then `.env`, then application defaults. The committed defaults contain no keys.
The lock files pin the versions tested here; `pyproject.toml` declares compatible ranges.

## API walkthrough

Examples use curl and jq. Set `API_ACCESS_TOKEN` in `.env.local` if you want a shared access token,
and add `-H "Authorization: Bearer YOUR_TOKEN"` to `/v1` requests. No token is needed by
default for local development. This is a single-workspace Phase 1 service; account ownership
and tenant authorization are not implemented.

1. Register a website. A name is optional; a URL is sufficient to start.

```bash
curl -sS -X POST http://localhost:8000/v1/brands \
  -H 'Content-Type: application/json' \
  -d '{"website_url":"https://your-company.com"}'
```

Save the returned `id` as `BRAND_ID`. Registration returns `201`; it does not crawl yet.
Canonical URLs omit queries/fragments, lowercase the host and normalize the root slash.
The same canonical website cannot be registered twice (`409 BRAND_EXISTS`). The original
registered website remains immutable; create another brand for a different website.

2. Trigger extraction. This returns `202` with a durable job ID, status and `poll_url`.

```bash
curl -sS -X POST "http://localhost:8000/v1/brands/$BRAND_ID/extractions"
curl -sS "http://localhost:8000/v1/extractions/$JOB_ID"
```

Poll until `SUCCEEDED` or `FAILED`. `PENDING` means queued or waiting for retry; `RUNNING`
means leased by a worker. An error contains a safe `code` and `message`. Repeated triggers
reuse an active job. If jobs stay pending, check that `brand-worker` is running and using
the same database as the API. If the job fails, fix its error and POST another extraction.

3. Fetch the generated draft. Every section exists, with missing values null or empty.

```bash
curl -sS "http://localhost:8000/v1/brands/$BRAND_ID/profile" | jq .
```

The envelope includes `status`, `revision`, `profile`, field `provenance`, warnings,
extraction/update timestamps, provider and model. Review inferred fields and source
evidence before approval. A profile can remain a draft without a company name if the site
does not state one. The server never derives a company name from its domain.

4. Correct fields using the latest envelope `revision`.

```bash
curl -sS -X PATCH "http://localhost:8000/v1/brands/$BRAND_ID/profile" \
  -H 'Content-Type: application/json' \
  -d '{"expected_revision":1,"changes":{
    "brand_identity":{"name":"Your Company"},
    "brand_voice":{"tone":["Warm","Clear"]},
    "marketing_defaults":{"content_language":"English"}
  }}'
```

Object fields merge recursively; omitted fields remain intact. Arrays replace entirely:
send `"offerings": []` to remove offerings, or the complete array to replace/reorder them.
Each offering needs only its name; all other offering fields are optional. Offering row IDs
are reused for exact, unchanged names, including reorders. Renaming creates a new offering
identity. The BIP's offering array contains data; the list-offerings API also returns row IDs.
Send `null` to clear a nullable scalar. Section objects and collection fields cannot be
null; use their nullable members or empty arrays. Unknown fields are rejected.

Edits mark changed populated fields `USER`, clear confirmation and return the profile to
`DRAFT`. A no-op patch leaves its state unchanged. Every material edit increments revision.
Stale revisions return `409 REVISION_CONFLICT`; fetch and review the latest version.

5. Confirm the reviewed version using its current revision (the example edit returns 2).

```bash
curl -sS -X POST "http://localhost:8000/v1/brands/$BRAND_ID/profile/confirm" \
  -H 'Content-Type: application/json' -d '{"expected_revision":2}'
```

Only company name and the registered website are required to confirm. Personas, pricing,
brand guidelines and marketing preferences can remain unknown. Confirmation sets
`CONFIRMED` and `confirmed_at`, and increments revision. Repeating confirmation with the
current revision is a no-op. Future edits require review and confirmation again.

6. List brands, offerings and source metadata.

```bash
curl -sS 'http://localhost:8000/v1/brands?limit=20&offset=0'
curl -sS "http://localhost:8000/v1/brands/$BRAND_ID/offerings"
curl -sS "http://localhost:8000/v1/brands/$BRAND_ID/sources"
curl -sS http://localhost:8000/v1/profile-schema
```

To deliberately replace an existing profile from its website:

```bash
curl -sS -X POST "http://localhost:8000/v1/brands/$BRAND_ID/extractions" \
  -H 'Content-Type: application/json' -d '{"replace_existing":true}'
```

A successful replacement becomes a new draft and replaces existing profile fields,
including corrections. Failure preserves the existing profile and confirmation. If the
user edits **or confirms** while extraction runs, the worker discards its result with
`PROFILE_CHANGED`. There is one current profile per brand; full profile revision history
is outside Phase 1. Source snapshots and job history remain stored.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/brands` | Register URL and optional name |
| GET | `/v1/brands` | Paginated brands with offerings and profile status |
| GET | `/v1/brands/{id}` | Brand core information |
| POST | `/v1/brands/{id}/extractions` | Enqueue/reuse extraction job |
| GET | `/v1/extractions/{id}` | Poll job status and safe errors |
| GET | `/v1/brands/{id}/profile` | Read current draft/confirmed BIP |
| PATCH | `/v1/brands/{id}/profile` | Correct profile with optimistic concurrency |
| POST | `/v1/brands/{id}/profile/confirm` | Approve current revision |
| GET | `/v1/brands/{id}/offerings` | Relational offerings, including row IDs |
| GET | `/v1/brands/{id}/sources` | Source URLs, hashes and timestamps |
| GET | `/v1/profile-schema` | Complete v1 BIP JSON Schema |
| GET | `/health/live`, `/health/ready` | Process and database health |

`/docs` and `/openapi.json` expose the typed BIP response contract. Field-level request
validation returns `422`; missing resources return `404`; revision/duplicate conflicts
return `409`. Domain errors use `{"error":{"code":"...","message":"..."}}`.

## BIP schema and evidence

The brief supplies the twelve section names, but no earlier field-level schema file.
This implementation defines a documented **version 1** contract in `app/schemas.py` and
`schemas/bip-v1.schema.json`. Extend those models and version the contract as requirements
evolve. Optional section members do not become onboarding prerequisites.

| Section | Representative fields |
|---|---|
| `brand_identity` | Name, website, legal name, tagline, description, mission, values, locations |
| `business_positioning` | Industry, category, business model, positioning, differentiators |
| `offerings` | Separate products/services, features, pricing, per-offering audiences and psychology |
| `customer_personas_and_audience_segments` | Personas, goals, roles, segments and needs |
| `customer_psychology` | Pain points, motivations, objections, triggers and desired outcomes |
| `value_propositions` | Primary promise, benefits, differentiation and reasons to believe |
| `trust_and_proof` | Testimonials, customer names, case studies, certifications and metrics |
| `brand_voice` | Tone, personality, terms and example phrases |
| `visual_identity` | Logos, colors, fonts, imagery and guidelines |
| `creative_context` | Themes, storytelling angles, content formats, assets and CTAs |
| `marketing_guardrails` | Prohibited claims, legal disclaimers and approval requirements |
| `marketing_defaults` | Objectives, channels, language, regions, budget and video preferences |

Every populated scalar leaf has provenance at a JSON pointer such as `/offerings/0/name`:

* `WEBSITE`: direct extracted information, with a verbatim quote and collected source URL.
* `INFERRED`: a tentative, grounded interpretation, with explicit reasoning and citations.
* `USER`: the submitted URL/name or a review correction; no website citation is asserted.

The service checks quoted text against the actual captured document and verifies source
URLs, evidence paths and permitted inferences before retaining facts. It stamps citations
with server-recorded fetch times. Names, testimonial quotes and pricing amounts must appear in cited
quotes. Unsupported enrichment is omitted with review warnings, while verified content is
saved as a draft. Scalars become `null`, unsupported list entries are removed, testimonials
without verified quotes and offerings without verified names are dropped, and inconsistent
pricing objects are cleared. Provenance indexes follow the retained collection entries.
Only the registered website is required to start; a company name is required to confirm.
See [the onboarding field policy](docs/onboarding-field-policy.md) for the full classification.
Inferences are restricted to voice, psychology, creative context and audience
interpretation; company identity, pricing, proof, defaults and guardrails require direct
evidence. Persona names/demographics cannot be inferred. Prompts treat website content as
untrusted data and prohibit invented details. Citation checks establish traceability;
semantic correctness still requires the review/confirmation step.

Text-only crawling cannot determine complete design systems: unsupported colors, fonts and
guidelines stay empty. Logo URLs and theme-color metadata are extracted when present.
The default provider is DeepSeek, using its documented
[Responses API JSON-schema output](https://api-docs.deepseek.com/api/create-response/).
It uses `deepseek-flash` by default, with thinking disabled for bounded structured extraction.
The existing [OpenAI Responses provider](https://developers.openai.com/api/docs/guides/structured-outputs)
remains selectable. Both strategies share a Pydantic-derived JSON schema, the evidence prompt,
local validation, request timeouts and bounded retries. Refusals, truncated output and invalid
evidence are handled explicitly: malformed model output fails, while unsupported enrichment
is omitted with review warnings. There is no automatic fallback between providers.

## Architecture and persistence

```text
HTTP /v1 -> BrandService -> Repository -> PostgreSQL
                          |
                 durable extraction_jobs
                          |
worker -> EnrichmentSource -> source_documents -> LLMProvider
       -> schema/evidence validation -> transactional DRAFT + offerings
```

* `app/api/routes.py`: HTTP input/output, shared-token authorization, job polling.
* `app/services.py`: registration, enqueueing, editing, approval and revision rules.
* `app/repository.py`: ORM queries, offering identity preservation and atomic job claims.
* `app/crawling/`: URL policy, pinned public DNS/IP requests, robots and page discovery.
* `app/enrichment.py`: source-neutral document/bundle protocol for future source adapters.
* `app/llm/`: provider protocol and configured adapter; no OpenAI dependency in business logic.
  `LLMProvider` is the strategy interface, `build_provider` is the strategy registry/factory,
  and `ExtractionWorker` is the context that receives the configured strategy. Concrete
  `DeepSeekProvider` and `OpenAIProvider` strategies share Responses transport in `responses.py`.
* `app/provenance.py`: grounded evidence checks and user-correction provenance.
* `app/worker.py`: durable extraction orchestration and failure/recovery lifecycle.
* `app/models.py`, `alembic/`: PostgreSQL schema and versioned migrations.

`brands` holds canonical URL, name and revision. `profiles` holds status/confirmation and
eleven section objects in JSONB, with JSONB provenance/warnings. `offerings` holds independent
relational rows linked to the brand, with JSONB nested offering details and audience data.
`source_documents` retains content, source type, SHA-256 hash, URL, job linkage and UTC fetch
timestamp. `extraction_jobs` persists attempts, status, revision, lease and safe errors.

Worker processes claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`. A partial unique index
allows one active job per brand. Expired leases recover after worker crashes, with bounded
attempts. Lease tokens reject late completions; brand revisions reject results based on
outdated profiles. Network operations occur outside transactions. A job timeout is shorter
than its lease. On SIGTERM the worker finishes its current bounded job before exiting.
Multiple workers may run against the same database; each worker processes one job at a time.
Set a container shutdown grace period matching your timeout if graceful completion is needed.

The crawler follows relevant homepage/about/product/service/pricing/solution links and tries
common fallback paths. It enforces robots **before every redirect hop**, allows only the
registered host and its www variant, limits pages/body/text/time and does not execute JS.
All DNS answers must be public; requests connect to a validated IP with the original Host
and TLS server name, preventing a second DNS resolution from bypassing the check. HTTPX
proxy environment variables are disabled for crawling. HTTP(S) standard ports only.

JS-only sites, bot-protected pages, login walls, unavailable robots policies and offsite
redirects may need user-entered corrections. Query-based pages are not crawled. Optional
page failures appear as warnings; failure to read the starting page fails extraction.
No competitor discovery, social scraping, recommendations, video or campaign features exist.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | See `.env.example` | Must use `postgresql+psycopg://` |
| `LLM_PROVIDER` | `deepseek` | Strategy selected by `build_provider`: `deepseek` or `openai` |
| `DEEPSEEK_API_KEY` | Empty | Required when using DeepSeek |
| `DEEPSEEK_MODEL` | `deepseek-flash` | Configurable DeepSeek Responses model |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek API endpoint, with optional compatible gateway path |
| `OPENAI_API_KEY` | Empty | Required only when using OpenAI |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Configurable structured-output capable model |
| `LLM_TIMEOUT_SECONDS` | `90` | Per-attempt total model timeout |
| `LLM_MAX_ATTEMPTS` | `3` | Model retries for connection/timeout/429/5xx failures |
| `CRAWL_MAX_PAGES` | `10` | Maximum attempted page URLs per collection, including failures |
| `CRAWL_TIMEOUT_SECONDS` | `15` | Per-fetch total timeout |
| `CRAWL_MAX_BYTES` | `2000000` | Decompressed response body limit |
| `CRAWL_MAX_TEXT_CHARS` | `12000` | Text per document supplied to LLM and stored |
| `JOB_TIMEOUT_SECONDS` | `240` | Overall extraction attempt time limit |
| `JOB_LEASE_SECONDS` | `300` | Must exceed job timeout by more than ten seconds |
| `JOB_MAX_ATTEMPTS` | `3` | Maximum leased attempts including crash recovery |
| `WORKER_POLL_SECONDS` | `2` | Idle polling interval |
| `LOG_LEVEL` | `INFO` | Logs job IDs, stages and safe failure codes |
| `API_ACCESS_TOKEN` | Empty | Optional shared bearer token for `/v1` |
| `TEST_DATABASE_URL` | Test DB on `5433` | Separate disposable PostgreSQL test database |

`.env` is committed as credential-free development configuration. Keep its key/token fields
blank; put secrets in ignored `.env.local` or process environment variables. Do not add real
credentials to the tracked `.env` or `.env.example`. Logs omit document content and raw
provider response bodies. Both the API and worker need the same configuration.
PostgreSQL connections use bounded connect, statement and lock timeouts.

### Selecting a provider strategy

The whole brand onboarding pipeline uses DeepSeek by default. Set the following in `.env.local`:

```dotenv
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=your-private-deepseek-key
DEEPSEEK_MODEL=deepseek-flash
```

To select the retained OpenAI strategy instead:

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=your-private-openai-key
OPENAI_MODEL=gpt-4.1-mini
```

Restart the Python API/worker after changes, or run `docker compose up -d --force-recreate api worker`
to reload Compose environment files. Only the selected provider's key is required; missing
credentials produce `LLM_CONFIG` on extraction, without calling the other provider. Completed
profiles record the selected provider and model. Review, correction and confirmation use the
same provider-independent business rules and do not make extra LLM requests.

To add another provider, implement `LLMProvider.extract()` and register it in `build_provider`.
Return `ExtractionResult` with the same evidence contract; the worker/service stays unchanged.
Additional enrichment adapters implement `EnrichmentSource.collect()` and return timestamped
`Document` objects. There is no simulated provider wired into the production factory.

## Migrations

```bash
alembic upgrade head
alembic current
alembic check
# After an intentional ORM schema change:
alembic revision --autogenerate -m 'describe schema change'
# Review the migration, then apply it.
```

The initial migration includes foreign keys, JSONB columns, status checks and the partial
active-job index. Downgrading to `base` removes application tables; use only on disposable
data. JSONB field-contract changes still require a schema-version/data-migration decision.

## Testing

Install `requirements-dev.lock` and the editable project as shown above.

```bash
pytest -q -m 'not integration'          # Unit/HTTP transport/real SDK boundary tests, no DB or key
docker compose --profile test up -d --wait test-db
export TEST_DATABASE_URL=postgresql+psycopg://brand:brand@localhost:5433/brand_intelligence_test
pytest -q                              # Includes migrated PostgreSQL persistence and API tests
ruff check .
ruff format --check .
```

Integration tests use the shipped Alembic migrations and truncate application tables between
tests. They refuse database names that do not end in `_test`; never point them at real data.
If `TEST_DATABASE_URL` is absent, integration tests skip explicitly. No test uses SQLite or
requires paid LLM calls. The provider boundary test uses the real OpenAI SDK with a mocked
HTTP transport to verify strict schema serialization, provider routing and response parsing
for both DeepSeek and OpenAI. Tests also run both configured strategies through PostgreSQL
draft persistence, review, correction and confirmation with simulated provider HTTP responses.

Tests cover URL-only onboarding, all twelve sections, source traceability, fact/inference
separation, corrections/confirmation, offering rows/ID reuse, JSONB storage, revision conflicts,
active-job uniqueness, retries/timeouts, failed replacement preservation, stale worker fencing,
concurrent skip-locked claims, crawl restrictions and safe LLM errors.

See `VERIFICATION.md` for checks run in the delivery environment and remaining live-service
verification limits.
