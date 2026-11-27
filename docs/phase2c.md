# Phase 2C Slice 1 · Open your workspace

You can sign in to a local Adjacent AI workspace, switch brands, and read the brand record, saved campaigns, and render history. The dashboard uses records already in PostgreSQL. It loads these records through FastAPI when you open a page.

## Start the frontend

1. Start the updated FastAPI server using the [backend quick start](../README.md#quick-start-with-docker-compose). Set a strong `API_ACCESS_TOKEN` in the repository's `.env.local`. If an earlier API container is running, rebuild it so the render history endpoint is available.
2. Use Node.js 22.18 or 24. Copy `frontend/.env.example` to `frontend/.env.local`. Set `ADJACENT_API_TOKEN` to the existing FastAPI token and `ADJACENT_LOCAL_AUTH=true`. Choose a separate passphrase of at least 12 characters for `ADJACENT_LOCAL_PASSPHRASE`. Generate a random session secret with `openssl rand -hex 32` and put it in `ADJACENT_SESSION_SECRET`.
3. From the repository root, run the following commands.

```sh
npm --prefix frontend ci
npm --prefix frontend run dev
```

4. Open [the local sign-in page](http://127.0.0.1:3000/sign-in). Enter the **frontend passphrase**. Use the exact address in `ADJACENT_LOCAL_ORIGIN`. To use a different port, update the origin and pass the same port to Next.js.

Local sign-in gives access to the one workspace configured on the server. The **Connect a workspace** screen uses the same local sign-in. Settings shows the workspace and lets you sign out.

| Server-only setting | Purpose |
| --- | --- |
| `ADJACENT_API_URL` | FastAPI origin. Defaults to `http://127.0.0.1:8000`. |
| `ADJACENT_API_TOKEN` | Existing FastAPI bearer token. |
| `ADJACENT_LOCAL_AUTH` | Enables local sign-in when set to `true`. Defaults to disabled. |
| `ADJACENT_LOCAL_ORIGIN` | Exact loopback origin for local browser access. |
| `ADJACENT_LOCAL_PASSPHRASE` | Separate secret entered on the local sign-in page. |
| `ADJACENT_SESSION_SECRET` | Random signing secret of 32 bytes or more. |

A successful sign-in sets an eight-hour HttpOnly cookie. Changing the configured passphrase, signing secret, or FastAPI token expires existing sessions. Sign-out clears the browser's cookie and query cache. The workspace and its API gateway require a valid local session. The gateway forwards the server token only to the configured FastAPI origin and accepts only the read routes used by Slice 1.

## Follow the code

- `frontend/src/app/` contains Next.js routes, including local sign-in and the read-only API gateway. The signed-in layout verifies the session before loading the workspace.
- `frontend/src/features/auth/` uses React Hook Form and Zod for the sign-in form. `frontend/src/lib/server/` verifies the local origin, signs and checks cookies, and restricts the gateway's paths.
- `frontend/src/styles/globals.css` holds the colors, spacing, and Inter typography from the [Figma design](https://www.figma.com/design/p6G4fHiU9lD71OOTK1VyKG). The shared shadcn/ui components, sidebar, cards, loading states, and notices live under `frontend/src/components/`.
- `frontend/src/features/brands/` reads and presents workspace records. TanStack Query caches the API reads and refreshes render history while it shows an active render. The selected brand stays in the URL.
- `frontend/src/api/generated/` contains the client types and hooks produced by Orval. It uses the saved FastAPI OpenAPI document and selects the operations tagged `workspace-dashboard`.

The backend adds a paginated `GET /v1/brands/{brand_id}/renders` list and publishes response models for the two existing lists the dashboard uses. No migration is required for Slice 1.

### Refresh the API client

From the repository root with the Python environment active, run:

```sh
PYTHONPATH=. python frontend/scripts/export_openapi.py
npm --prefix frontend run api:generate
```

Review the changes to `frontend/openapi.json` and the generated client together.

## Test Slice 1

Run the build, component tests, and browser tests with the dependencies installed:

```sh
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm --prefix frontend test
npm --prefix frontend exec -- playwright install chromium
TEST_DATABASE_URL=postgresql+psycopg://brand:brand@localhost:5433/phase2c_frontend_test \
  npm --prefix frontend run test:e2e
```

Create the dedicated PostgreSQL database before the browser tests. Install FFmpeg for the test thumbnail. Playwright starts local Next.js and FastAPI processes with isolated test credentials, migrates the test database, and inserts saved test records. It checks sign-in, credential isolation, data from FastAPI, brand switching after refresh, mobile navigation, and sign-out. The tests start no inference or render jobs.

## Production sign-in and ownership proposal

Approve the identity provider and membership model before enabling public access. The proposed approach uses an OpenID Connect provider chosen by the workspace owner. Next.js would use a server-side session for the signed-in user. FastAPI would verify the user identity on every protected request and use PostgreSQL workspace memberships to authorize the operation. Give workspace owners membership management and let members use the workspace's production tools. Define any additional roles after reviewing sharing needs.

Add workspace and membership tables and a workspace ID on each brand. Move existing records into a legacy workspace and scope website uniqueness to each workspace. Apply ownership checks to every descendant route, including extraction, campaigns, creative jobs, source snapshots, media assets, previews, renders, and downloads. Resolve workspace membership on the server. Bind background jobs and connected provider credentials to the workspace that created them. Add integration tests that prove separate workspaces cannot use each other's resources or jobs.

The local passphrase is a private development option. Public account creation, invitations, recovery, and workspace isolation depend on the approved production authentication design.

## Follow-on work

Slices 2–4 add editing, job creation, and production tools. The video slice will reuse the existing Remotion compositions through Remotion Player. Zustand can hold studio selections and appearance state when needed.

Before enabling render actions in the frontend, make the backend require approval of the exact caption copy for a legacy concept. The current VideoSpec compiler can accept new captions after concept approval. Also restrict the brand label to reviewed brand identity text or copy explicitly approved with the concept. The current create and edit checks accept any label up to 40 characters. Apply the same check when queuing and finalizing a render. Historical outputs can remain available for review.
