# Turn an approved concept into a video

Phase 2B turns an approved Phase 2A concept and authorized media into a private MP4. You can lay captions over a background clip or reveal uploaded product photos in sequence. The worker uses the approved copy and the campaign duration. It runs without an AI model.

## Start the services

With Docker Compose, set a strong `API_ACCESS_TOKEN` in the ignored `.env.local` file, then run these commands from the repository root.

```bash
docker compose up --build -d
docker compose logs -f api render-worker
```

Compose migrates PostgreSQL and shares a private `media_data` volume between the API and render worker. It keeps the render worker on a separate internal network with the database. The renderer receives local media and runs Chrome without an external stock or AI connection.

For local development, install Python 3.12, PostgreSQL 16, Node.js 22.20.0, FFmpeg with `libx264`, FFprobe, and Chrome or Chromium. Follow the [Python setup in the README](../README.md#local-python-development), then run:

```bash
npm --prefix video-renderer ci --no-audit --no-fund
npm --prefix video-renderer run typecheck
export RENDER_BROWSER_EXECUTABLE=/absolute/path/to/chromium
alembic upgrade head
python -m app.video.worker
```

Run the API in a separate terminal. Start both processes from the repository root with the same database and media directory. The existing extraction worker is only needed for brand extraction and creative generation.

| Setting | Default or requirement |
| --- | --- |
| `API_ACCESS_TOKEN` | Required for media and video requests. The API uses the existing shared workspace token. |
| `MEDIA_STORAGE_PATH` | `.local/media` on the host and `/srv/media` in Compose. Use a private writable directory shared by the API and render worker. |
| `MEDIA_MAX_BYTES` | `67108864` bytes per uploaded or imported file. |
| `MEDIA_PROCESS_TIMEOUT_SECONDS` | `45` seconds per FFmpeg or FFprobe command. |
| `RENDER_BROWSER_EXECUTABLE` | Required for local renders. Compose sets it to `/usr/bin/chromium`. |
| `RENDER_PACKAGE_PATH` | `video-renderer`. |
| `RENDER_TIMEOUT_SECONDS` | `360` seconds for the Remotion subprocess. Staging and validation are tracked separately. |
| `RENDER_CONCURRENCY` | `2` browser render threads per job. |
| `PIXABAY_API_KEY` | Optional. Set it in `.env.local` for external searches. Uploads and internal search work without it. |

Set render-worker overrides on that process. In Compose, add overrides to the render-worker environment with a local override file. The worker does not load the API's private environment file. Compose sets CPU, memory and process limits for the render worker.

## Make a video

1. **Add media.** Upload a JPEG, PNG, WebP, MP4, MOV, or WebM that you have rights to use. Use `BACKGROUND` for a clip and `PRODUCT` for an authorized product photo. You can also store images as `BRAND` for your media library. Uploads take a raw binary request body. The API keeps the original, creates a thumbnail, and makes an MP4 derivative when the clip needs it.
2. **Approve the current concept.** Review the copy, claims, and media requirements, then approve the current revision through the [Phase 2A review API](phase2a.md). Approval records the review for production handoff. Review the final ad and any rights separately before publication.
3. **Create the VideoSpec.** Choose `TEXT_OVER_BROLL` with one background video at least as long as the campaign, or `PRODUCT_REVEAL` with up to five eligible product photos of at least 240 pixels per side. Captions come from the approved concept. For a legacy concept, send `final_captions` and `confirm_legacy_captions: true`. Single captions can include a closing brand message. Sequential captions use the saved hook, development, product connection, and payoff text. If the captions need more reading time, shorten and reapprove the copy or use a longer campaign.
4. **Queue it.** Pass the current VideoSpec revision to the render endpoint. Use the returned poll URL until the job reaches `SUCCEEDED` or `FAILED`. If a revision changes during rendering, the job fails with a stale-input error. After addressing the reported issue, queue the current spec again.
5. **Review it.** Download the MP4 or stream it with your bearer token. Check the caption readability, framing, and advertising claims in the finished video.

Use [the ready-to-edit API requests](../examples/phase2b.http) or the local API docs. Send `Authorization: Bearer $API_ACCESS_TOKEN` on each request.

| Method and path (under `/v1`) | Purpose |
| --- | --- |
| `POST /brands/{brand_id}/media?purpose=BACKGROUND&confirm_rights=true` | Upload media. Add a description and comma-separated tags to make it searchable. |
| `GET /brands/{brand_id}/media` | List the brand's assets and their thumbnails. |
| `GET /brands/{brand_id}/media/search` | Search the brand library by keywords, type, orientation, dimensions, and duration. |
| `POST /brands/{brand_id}/media/pixabay` | Import one selected search result for the brand. |
| `POST /concepts/{concept_id}/video-specs` | Compile a spec from the approved concept revision. |
| `GET`, `PATCH /video-specs/{spec_id}` | Read the spec or update its style, fit, or overlays using the current revision. |
| `POST /video-specs/{spec_id}/renders` | Queue the current spec revision. |
| `GET /renders/{job_id}` | Read status, exact inputs, timings, and links to validated output. |
| `GET /renders/{job_id}/video` | Stream the MP4 with optional byte ranges. Add `?download=true` to download. |
| `GET /renders/{job_id}/preview` | View a preview thumbnail. |

The format is a 1080 × 1920 vertical MP4 at 30 FPS, encoded as H.264 with `yuv420p` pixels. It is silent by default. For a background clip, set `audio: "BACKGROUND"` only when you can use the source audio. Choose `fit: "cover"` to fill the frame or `"contain"` to keep the whole asset visible. You can set caption position, font size, colors, and safe margins. Product reveal supports an approved CTA, controlled zoom, and crossfades.

## Search and import stock media

Add `search_pixabay=true` to a background search to look on Pixabay when the brand library has no matches. The response includes previews, attribution, and a `search_id`. Pick one result and send its `provider_asset_id`, type, and `confirm_rights: true` to the import endpoint. The API reuses that result if already acquired for this brand. Equivalent external searches share a 24-hour cache. If the provider rate-limits search, use the response's retry delay before searching again.

Keep previews and attribution tied to the original Pixabay result. Check whether each asset's license and any third-party rights cover the intended use. Product reveal requires authorized product uploads. Review [Pixabay's API rules](https://pixabay.com/api/docs/) and [content license summary](https://pixabay.com/service/license-summary/) before using stock in an ad.

## What runs where

- `VideoService` validates brand scope and concept revision, works with the media processor, compiles caption timing, and freezes the exact spec at queue time. PostgreSQL holds asset metadata, search cache, specs, job status, and validation results.
- `MediaProcessor` reads technical metadata with FFprobe and uses FFmpeg to check decoding, make thumbnails, and prepare compatible clips when needed. `StorageProvider` defines private object read, write, delete and streaming operations. The local implementation stores original files, derivatives, and render outputs at separate keys.
- `PixabayMediaProvider` handles search, caching, rate limits, and downloads from approved public hosts. It accepts a saved result ID from the client and checks the file size, format, and actual bytes before storing it.
- `RenderWorker` leases one job at a time, checks stored asset hashes and current revisions, and stages local files for Remotion. It validates the output's streams, duration, frame rate, codec, and decoding before serving it. Stage names and render times are available on the job. Logs record IDs and failure codes without credentials.
- The two React compositions in `video-renderer/src/templates` share caption and brand overlay components. The TypeScript entry point renders only the supported, versioned templates. See [Remotion's licensing terms](https://www.remotion.dev/docs/license) for eligibility and any commercial requirements.

The bearer token protects a single local workspace. Keep the API bound to localhost with a private storage path. The storage interface allows a future object store. It currently uses a local volume.

## Run the offline checks

Point `TEST_DATABASE_URL` to disposable PostgreSQL, install the Node dependencies, and set `RENDER_BROWSER_EXECUTABLE` to your local browser. Run:

```bash
RUN_REAL_RENDER=1 pytest -q
```

The tests generate small media fixtures, mock Pixabay, render and decode actual MP4s, and check private, seekable playback. Run `alembic check` against an upgraded database to check model and migration alignment.

To replay a saved Phase 2A export without changing its brand or campaign, migrate a fresh PostgreSQL database whose name ends with `_benchmark_test`. Set `DATABASE_URL` to that database and run the offline exporter:

```bash
python -m evaluations.phase2b --input /path/to/opal-benchmark.json \
  --output /path/to/new-opal-export --label Opal --template TEXT_OVER_BROLL
```

For a product layout demonstration, use a saved Drip Drop Attire export with `--label "Drip Drop Attire" --template PRODUCT_REVEAL`. The exporter uses synthetic motion and geometric placeholders and labels the output. It copies the saved concept into the test database and confirms the saved hook as the caption for a legacy concept. It exports the MP4, exact spec, media metadata, job, FFprobe JSON, and representative frames. Use the normal media and render APIs for a benchmark with authorized footage or product photos.
