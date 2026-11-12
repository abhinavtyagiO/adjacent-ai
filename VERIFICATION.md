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
