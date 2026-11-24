# Phase 2A: Create and review ad ideas

Phase 2A uses a saved Brand Intelligence Profile (BIP) and your campaign brief to suggest short-form ad ideas. You can review the ideas, edit them, and choose one to send to a future production workflow.

The app uses research it already saved. It leaves the brand profile as it is and does not crawl the site again or open links in your brief. Phase 2A covers planning and review. Video creation, publishing, and competitor or social media research are outside its scope.

## What to put in a brief

Enter your campaign goal and either the audience you want to reach or the concern you want to address. You can also enter a platform, language, region, call to action (CTA), destination URL, phrases to avoid, and written creative references. Leave optional details blank if you have not decided on them. Destination URLs keep tracking parameters and page anchors.

| Scope | What to choose |
|---|---|
| `BRAND` | Promote the brand as a whole. Leave the offering key blank. |
| `FAMILY` | Choose a saved product family. Research includes the products in that family. |
| `PRODUCT` | Choose one saved offering that is not a family. |

Ideas are designed for vertical, 9:16 video with text on screen (`TEXT_OVER_VIDEO`). The default duration is 15 seconds. You can set it to 5–60 seconds. Start with about 10–20 seconds for a typical short ad.

## How an idea becomes a shortlist entry

1. **Save a copy of the research.** The app records the exact versions of the brief and brand profile, along with references to the saved website sources.
2. **Generate ideas.** The first model call aims for six to eight ideas. Each uses one main reason the audience might pay attention: recognition, curiosity, desire, or relevance.
3. **Check and save the ideas.** The app checks the format, audience, CTA, product references, excluded phrases, repeated ideas, and production assumptions. It saves the ideas that pass before evaluation starts.
4. **Build a shortlist.** The second model call reviews each saved idea. It gives strengths, weaknesses, a reason for its decision, and seven scores from 0 to 10. It can choose up to five ideas. The scores describe creative quality as judged by the model. They do not predict clicks, sales, or profit.

An idea can use one of two on-screen text formats:

- `SINGLE_CAPTION` gives you one main overlay and an optional closing brand message.
- `SEQUENTIAL_CAPTIONS` gives you four captions in order for the hook, development, product connection, and payoff.

Captions contain the words viewers see. Footage and production directions go in separate fields. The app flags captions that may be too long for the chosen duration.

Each idea includes the audience situation, the main insight, the product benefit, a main hook and two or three alternatives, and four parts of the story: opening, development, connection to the product, and payoff. It also includes suggested footage, assets or demos to create, a closing line, a CTA, and the assumptions and risks to review.

If the evaluator chooses fewer than five ideas, the shortlist shows `insufficient: true`. If it chooses none, the job reports `INSUFFICIENT_DATA`. If evaluation fails, the job reports `PARTIAL` and you can still open the saved ideas.

The evaluator compares the central message of every idea with the others. It groups ideas that say substantially the same thing, even if they use different hooks or formats. Only the strongest idea the evaluator identifies in each group can make the shortlist. Each shortlisted idea includes a short explanation of what makes it different.

## What the app knows and what you need to check

Research can include brand positioning and voice, product features, audience hypotheses, customer concerns and reviews, visual guidance, and campaign references. The app gives the brief and brand rules priority. If these alone exceed the size limit, the job stops before making a model call. The app picks short excerpts from across the saved pages based on the audience, concern, objective, and selected offering. It skips repeated text, logo links, and common boilerplate. It reserves space for relevant excerpts and customer concerns. The research response shows how many it included or left out. Each excerpt identifies its saved source URL, hash, and line number.

For evaluation, the app sends a smaller set of material, including the items the ideas refer to and relevant brand guidance. The evaluator compares the actual hooks, story, closing line, payoff, and captions with the cited capabilities. Unsupported promises get review flags. A product scope mismatch or an unsupported safety assurance blocks selection. Copy about pregnancy, breastfeeding, or suitability for children also gets a review flag.

- **Product benefits.** An idea must copy its stated product capability from a saved research item and reference that item by ID. This makes it easy to trace the benefit to the right product and family. Long values can still be read as background, but cannot be used as a capability reference if they exceed the field limit. If there is no usable capability, generation stops before a model call.
- **Website sources.** A website observation gets a source-verified label when its value and cited quote match the saved text. URLs, fetch dates, and content hashes help identify that text. This confirms what appeared on the site. Clinical and advertising claims still need their own review.
- **Draft facts and opinions.** The app keeps draft extractions, user statements, model hypotheses, and customer opinions separate. It uses simple word checks to flag some negative or mixed reviews. Other reviews are left unrated for sentiment, and none are automatically cleared to use in an ad.
- **Brand rules and duplicates.** Both model calls receive the brand rules. The app catches some obvious problems, such as identical hooks written with different punctuation, repeated central ideas, and blanket claims about cures or safety. It can miss paraphrases and rules that depend on context.
- **Assets.** Suggested assets are marked `requires_creation: true`. Check what you already have, what needs to be made, and what requires permission. A mention in the research does not establish ownership or access.

## Edit, choose, and hand off an idea

You can change wording such as the hook, insight, audience situation, story, closing line, CTA, or captions. The original draft stays available. Editing clears the current score and shortlist status because they apply to the earlier wording. A new generation creates new ideas and keeps earlier edits and selections.

Use **Select** to choose one idea for production. Selecting another clears the earlier selection. Use **Approve** after someone has reviewed the concept and acknowledged the claim and asset risks. Both actions require up-to-date research and an idea with no blocking flags. Approval records a decision about production. The handoff keeps the risks visible and sets `advertising_claims_approved` to `false`.

The handoff contains the brief and idea in a structured format for Phase 2B. If the brief or brand profile changes, you can still view earlier work. Generate from the latest versions before handing off an idea.

## Use the API

Follow the setup and sign-in steps in the [README](../README.md). Apply migration `0005` before starting the new worker. For local Python, run `alembic upgrade head`. For Docker, rebuild and start the services. These endpoints use the app's existing bearer token.

[Complete HTTP request examples](../examples/phase2a.http)

| Method | Endpoint | Use it to |
|---|---|---|
| POST / GET | `/v1/brands/{brand_id}/campaigns` | Create or list campaigns |
| GET | `/v1/campaigns/{id}` | Read the brief and its version |
| PATCH | `/v1/campaigns/{id}/brief` | Edit the brief |
| POST / GET | `/v1/campaigns/{id}/generations` | Start a job or see past jobs |
| GET | `/v1/creative-generations/{id}` | Check status, errors, and model usage |
| GET | `/v1/campaigns/{id}/research/{research_id}` | See the saved copy of the research |
| GET | `/v1/campaigns/{id}/concepts?job_id=...` | See all saved ideas, including partial results |
| GET | `/v1/campaigns/{id}/shortlist?job_id=...` | See the shortlisted ideas |
| GET / PATCH | `/v1/concepts/{id}` | Read or edit one idea |
| POST | `/v1/concepts/{id}/select` | Choose the idea |
| POST | `/v1/concepts/{id}/approve` | Record a human production review |
| GET | `/v1/concepts/{id}/handoff` | Get the structured production handoff |

When editing, send the version you last read as `expected_revision`. A `409` means the version is stale. Fetch the latest version before making your edit. A `422` means the edit is invalid. Updating a list in the brief replaces that list. Editing a story updates only the parts you send. Product capability and research references stay tied to the original idea.

Without `job_id`, the shortlist shows the latest run, even if that run failed. Pass the earlier job ID to see its shortlist. Use the concepts endpoint to see all saved ideas and browse them by page.

## Storage and job limits

Migration `0005` adds four PostgreSQL tables.

| Table | What it stores |
|---|---|
| `campaigns` | The brand, editable brief, and brief version |
| `research_snapshots` | A fixed copy of the research, with brief and BIP versions and the original extraction job ID |
| `creative_jobs` | Job status, account and model, worker claim, errors, and usage |
| `creative_concepts` | Original and edited ideas, review flags, scores, and selection |

The app stores nested fields such as briefs, hooks, and scores as JSONB. Database constraints allow one active job and one selected idea per campaign. The worker alternates between the brand and creative queues. It checks that it still owns the job and that the brief and brand profile are current before saving results. An edit made during evaluation takes priority over the evaluator's result for that idea.

A creative job makes at most **two model calls** on the configured account and model. Each call gets one attempt. The app uses the configured billing source without switching to another model or billing method. The existing provider rules and overall job limits apply.

| Setting | Default |
|---|---:|
| `CREATIVE_CONTEXT_MAX_CHARS` | 30,000 characters of research JSON |
| `CREATIVE_MAX_INPUT_TOKENS` | 20,000 estimated tokens per request, including the response schema |
| `CREATIVE_GENERATOR_MAX_OUTPUT_TOKENS` | 8,000 |
| `CREATIVE_EVALUATOR_MAX_OUTPUT_TOKENS` | 4,000 |
| `CREATIVE_GENERATOR_TIMEOUT_SECONDS` | 120 seconds |
| `CREATIVE_EVALUATOR_TIMEOUT_SECONDS` | 60 seconds |

The API key provider sends its output limit to the model. The ChatGPT plan provider applies a local response size limit and deadline because the preview does not accept that setting. This cannot guarantee an exact number of tokens billed by the provider.

If generation fails, the job reports `FAILED`. If all ideas fail the initial checks, the job reports `INSUFFICIENT_DATA` and skips evaluation. If the worker loses its claim on an unfinished job, the app keeps any saved ideas, marks the job finished, and flags possible missing usage. Start a new generation explicitly if you want another run. Logs record IDs, stages, and error codes. Job status also shows timings and token counts when the provider supplies them.

## Test the workflow

Run the Phase 2A tests against a disposable PostgreSQL database:

```sh
pytest -q tests/test_creative_strategy.py tests/test_creative_provider.py \
  tests/test_creative_workflow.py tests/test_creative_migrations.py
```

The tests use saved brand profiles and example model responses. Opal's brief targets students on Instagram Reels. Minimalist's brief targets students and young adults interested in skincare ingredients. Both are awareness campaigns with no region specified. The five manually written Opal ideas serve as comparison material for reviewers and stay out of the model prompts.

The tests check that the workflow behaves correctly. **A live review is needed to judge whether the ideas are useful.** For an explicitly authorized live check, run one campaign generation per brand, one at a time, using the existing model settings. Save the research, ideas, shortlist, reasons for rejection, timings, and usage. Compare Opal's ideas with the manual examples. For Minimalist, check whether the ideas address real customer concerns, describe ingredients and benefits accurately, and handle negative feedback fairly. For both, ask whether each idea feels distinct, fits the brand, and is feasible to produce.

### Prepare and export the Opal benchmark

The benchmark uses an awareness campaign for students on Instagram Reels. It uses the brand as a whole, runs for 15 seconds at 9:16, and leaves geography blank. Use the local API with the updated code and the token in `.env.local`.

```sh
python -m evaluations.phase2a prepare OPAL_BRAND_ID > opal-campaign.json
```

This creates or reuses a matching campaign and records the current BIP revision and saved source job. After explicit authorization, follow the generation and polling steps in the HTTP examples for this campaign. Export as soon as the job finishes so the report includes the original evaluations before any human edits.

```sh
python -m evaluations.phase2a export CAMPAIGN_ID JOB_ID > opal-benchmark.json
```

The report includes the saved research sent to the generator, all stored candidate outputs, rejected candidates with their flags, the shortlist and its reasons, actual hooks and captions, and model usage and timing for each stage. It also shows any partial failure. A failed response that could not be parsed may have no candidates to export. Both commands leave generation to the separate API step.

Rolling back migration `0005` removes the creative tables and keeps the Phase 1 profiles, offerings, sources, and jobs. Back up campaign and concept data before rolling back.
