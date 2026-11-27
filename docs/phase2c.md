# Phase 2C · The Adjacent AI workspace

Use the local workspace to research a brand, make a campaign, review creative ideas, and render a short video. FastAPI runs these workflows and stores the records in PostgreSQL.

## Start the frontend

1. Start FastAPI, PostgreSQL, and the workers using the [backend quick start](../README.md#quick-start-with-docker-compose). Rebuild the API and workers if you ran an earlier version. Set `API_ACCESS_TOKEN` in the repository's `.env.local`.
2. Use Node.js 22.18 or 24. Copy `frontend/.env.example` to `frontend/.env.local` if you have not set it up. Use the same FastAPI token for `ADJACENT_API_TOKEN`, enable `ADJACENT_LOCAL_AUTH=true`, and choose a separate local passphrase of at least 12 characters. Generate a random secret with `openssl rand -hex 32` and set it as `ADJACENT_SESSION_SECRET`.
3. Install the frontend and shared renderer dependencies from the repository root.

```sh
npm --prefix frontend ci
npm --prefix video-renderer ci
npm --prefix frontend run dev
```

4. Open [the local sign-in page](http://127.0.0.1:3000/sign-in). Enter the **frontend passphrase**. Use the exact address in `ADJACENT_LOCAL_ORIGIN`. To use a different port, update the origin and pass the same port to Next.js.

Local sign-in gives access to the workspace configured on the server. If you want to run brand research or generate ideas, connect your account and select a model using the [ChatGPT provider setup](chatgpt-plan.md). Set `PIXABAY_API_KEY` in the backend's `.env.local` for stock search. You can also use your own uploads and saved media.

| Server-only setting | Purpose |
| --- | --- |
| `ADJACENT_API_URL` | FastAPI origin. Defaults to `http://127.0.0.1:8000`. |
| `ADJACENT_API_TOKEN` | Existing FastAPI bearer token. |
| `ADJACENT_LOCAL_AUTH` | Enables local sign-in when set to `true`. Defaults to disabled. |
| `ADJACENT_LOCAL_ORIGIN` | Exact loopback origin for local browser access. |
| `ADJACENT_LOCAL_PASSPHRASE` | Separate secret entered on the local sign-in page. |
| `ADJACENT_SESSION_SECRET` | Random signing secret of 32 bytes or more. |

A successful sign-in sets an eight-hour HttpOnly cookie. Sign-out clears the browser's cookie and query cache. The gateway checks the local session and allows specific FastAPI reads and writes. It checks the request origin on writes and keeps the FastAPI token on the server. Changing the passphrase, session secret, or token expires existing sessions.

## Make an ad

1. **Add a brand.** Choose **Add a brand** and enter the website. **Analyze my brand** registers the brand and starts the research job. You can refresh the progress page or find the last job on the brand page.
2. **Review the profile.** Check the product catalog, sources, claims, and audience. You can edit the brand summary, positioning, audiences, customer psychology, and voice. Use **Confirm profile** when you have reviewed it. The claims panel shows provisional claims and source references for review.
3. **Create a campaign.** Set the objective, audience, platform, and duration. You can promote the brand, an offering, or a product family. Save the brief, then select **Generate ideas**. You can return to the same generation or switch between saved generations.
4. **Review an idea.** Open a shortlisted idea or browse all saved candidates. Check the references and flags, edit the hooks and captions, and complete the three review checks to approve the current revision. Editing copy after approval requires a new review.
5. **Set up the video.** Choose a background video or an ordered set of product photos. Search saved assets, upload your own media, or import a selected Pixabay result. Adjust caption appearance and confirm media rights. **Save video setup** saves the VideoSpec and opens the interactive preview.
6. **Render and download.** Select **Generate video** to queue the saved setup. Refreshing keeps the same render job. After it finishes, play or download the MP4 and open earlier renders in the history. The backend checks that the caption text and optional brand label still match the approved revision. Older ideas need saved captions and a fresh approval before rendering.

Research, idea generation, stock imports, and rendering start from explicit actions. Opening a page restores the saved records and polls active jobs.

## Follow the code

- `frontend/src/app/` holds the Next.js routes and gateway. `frontend/src/lib/server/` checks sessions, origins, and allowed API routes.
- `frontend/src/styles/globals.css` and `frontend/src/components/` hold the shared colors, spacing, Inter typography, shadcn/ui controls, and navigation from the [Figma design](https://www.figma.com/design/p6G4fHiU9lD71OOTK1VyKG).
- `frontend/src/features/` groups each part of the workflow. React Hook Form and Zod handle form input. TanStack Query loads records, caches them, and polls jobs. Routes and query parameters keep the selected brand, job, and video setup through page refreshes.
- `frontend/src/features/video-studio/` uses Zustand for the local media and appearance choices. The preview uses Remotion Player with the same compositions as `video-renderer/`. FastAPI serves only the assets bound to the saved VideoSpec through the private gateway.
- `frontend/src/api/generated/` contains client types and query hooks generated by Orval. The FastAPI OpenAPI document describes the records and operations tagged `workspace-dashboard` and `workspace-workflow`.

The backend publishes typed response models, saved extraction history, filtered render history, and the private VideoSpec media endpoint. These changes use the existing database tables.

### Refresh the API client

From the repository root with the Python environment active, run:

```sh
PYTHONPATH=. python frontend/scripts/export_openapi.py
npm --prefix frontend run api:generate
```

Review the changes to `frontend/openapi.json` and the generated client together.

## Run the tests

Install FFmpeg and FFprobe. Start the test PostgreSQL service and create the separate frontend test database before starting Playwright.

```sh
docker compose --profile test up -d test-db
docker compose exec -T test-db createdb -U brand phase2c_frontend_test
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm --prefix frontend test
npm --prefix frontend exec -- playwright install chromium
TEST_DATABASE_URL=postgresql+psycopg://brand:brand@localhost:5433/phase2c_frontend_test \
  npm --prefix frontend run test:e2e
```

Run the `createdb` command once. Playwright starts Next.js and FastAPI with test credentials and migrates the test database. The browser tests cover sign-in, navigation, and the full path from a new website to an MP4 download. The website, model, and Pixabay responses are fixtures. PostgreSQL, FastAPI, job processing, preview playback, and Remotion rendering all run locally. See the [backend test guide](phase2b.md#run-the-offline-checks) for the media and renderer tests.

## Production sign-in and ownership proposal

Production sign-in is deferred. The proposed approach uses an OpenID Connect provider chosen by the workspace owner. Next.js would manage the user's session. FastAPI would verify the user's identity and check workspace membership in PostgreSQL on protected requests.

Add workspace and membership tables and a workspace ID on each brand. Move existing records into a legacy workspace and scope website uniqueness to each workspace. Apply ownership checks to every descendant route, including extraction, campaigns, creative jobs, source snapshots, media assets, previews, renders, and downloads. Resolve workspace membership on the server. Bind background jobs and connected provider credentials to the workspace that created them. Add integration tests that prove separate workspaces cannot use each other's resources or jobs.

The local passphrase is a private development option. Public account creation, invitations, recovery, and workspace isolation depend on the approved production authentication design.
