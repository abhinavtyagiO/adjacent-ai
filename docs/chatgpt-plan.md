# ChatGPT plan integration — local/self-hosted preview

Eligibility checked against the official documentation on **14 November 2026**. This implements
the local/self-hosted route you selected. It is a single-workspace installation, not a hosted
multi-tenant service. OpenAI determines actual account/workspace eligibility at authorization
and request time; Plus/Pro availability is not guaranteed for every account or model.

[The quickstart](https://developers.openai.com/siwc/quickstart) and
[plan overview](https://developers.openai.com/siwc/token-sharing-open-source) distinguish local/
open-source clients from paid/remotely hosted apps. The latter need preview access through
the [client registration process](https://developers.openai.com/siwc/request-client-id).
`CHATGPT_DEPLOYMENT_MODE=hosted` is deliberately blocked; this implementation does not claim
commercial approval. No license was added or changed. Public repository visibility alone is
not treated as commercial enrollment or proof of open-source licensing.

## Configure and connect

Tracked defaults now select `openai_chatgpt`, disable API-key billing, and limit LLM/job
attempts to one. DeepSeek is rejected by the runtime factory even if an old environment
selects it. Its old adapter remains only for historical mocked transport tests. No Codex or
ChatGPT login cache is read or repurposed. An existing `OPENAI_API_KEY` is not a plan credential.

Set a strong `API_ACCESS_TOKEN` in ignored `.env.local` to protect this local workspace.
The existing local file has been configured without printing the token or overwriting other
credentials. New installations must supply their own value. Never commit it. This access
token is an application password, not an OpenAI API key or ChatGPT OAuth token.

Start the application when ready (`docker compose up --build -d`). Migration **0003** adds
nullable account/model binding to extraction jobs. Existing profiles and audit data remain.
Open **http://127.0.0.1:8000/v1/chatgpt/connect** (not localhost), enter the local access token,
load saved accounts, check the plan-use consent box, and choose **Continue with ChatGPT**.
The official browser flow requests the separate inference permission. On return, choose an
account-specific model. The local token is held only in page memory; OAuth tokens never
enter browser storage. Use the local token in Swagger's Authorize dialog or bearer headers
for brand APIs. Do not disable access logs selectively only after login: callback URLs carry
short-lived codes; Docker disables access logging and application logging suppresses it.

The [registration guide](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
uses dynamic registration, PKCE S256, state and nonce with an exact HTTP 127.0.0.1 callback.
The app validates ID-token signature/JWKS, issuer, audience, expiry, nonce and returning
subject/client identity before saving credentials. Returning accounts keep their issued client
ID; email does not merge registrations. Explicit re-consent is optional and uses `prompt=consent`,
not the preview-gated `force_reconsent`. Missing plan scopes retain identity with inference
blocked. Failed code exchange retains a temporary registration recovery in the same browser.

## Account APIs and onboarding

All account APIs except the static connection page and state/cookie-authorized callback need
the local bearer token. Browser management requests also enforce the configured origin.

| Method/path under `/v1/chatgpt` | Purpose |
|---|---|
| `POST /authorizations` | Start PKCE; requires `plan_usage_consent: true` |
| `GET /callback` | Validate one-use state/cookie and exchange the code |
| `GET /accounts` | Safe account labels/status; no credentials |
| `POST /accounts/{id}/select` | Select an already-authorized account |
| `GET /accounts/{id}/models` | Fresh account-specific catalog, visible models in server order |
| `PUT /accounts/{id}/model` | Select an available `model` slug |
| `POST /accounts/{id}/resume` | Explicitly resume after reviewing a usage limit; no inference |
| `DELETE /accounts/{id}/session` | Revoke renewable session and clear local tokens |

Existing URL registration, profile review/edit/confirm APIs are unchanged. Extraction accepts
optional `chatgpt_account_id`, `factual_model`, and `marketing_model`; omitted values snapshot
the active authorized account and selected model. Jobs never switch account if settings later
change. Old unbound queued jobs fail sign-in-required rather than guessing an owner. The
worker validates models with the same account before inference; stage errors are exposed in
`stage_errors`. Optional marketing failures still preserve the verified factual draft.

## Streaming and limits

The [models/inference contract](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
uses OAuth bearer access with public `/v1/models` and `/v1/responses`. It does not use ChatGPT
backend endpoints. Success requires `response.completed`; partial JSON, disconnects, incomplete
responses and late failure events are not successes. Text is schema-validated before the
existing evidence pipeline. Private reasoning events are ignored, not logged or stored.

The [preview restrictions](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
require streaming and `store:false`. The transport sends `instructions` instead of system
messages and omits unsupported fields, including `max_output_tokens`. Stage output allowances
therefore bound local assembly/reservation, not server-side generation. A timeout/abort can
still consume plan usage. There is no automatic streaming-inference retry or billing fallback.
Token counts remain observable; an API-key dollar estimate is not applied to plan/credit use.

[Error recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)
distinguishes usage limits, ineligible accounts, permission failures and temporary service
issues. A usage limit pauses that account until explicit resume after checking ChatGPT Usage;
no reset time is guessed. Terminal refresh errors clear unusable tokens and require sign-in.
Transient errors preserve credentials. Remote revocation not confirmed at sign-out is reported
explicitly, with a link to disconnect in ChatGPT settings. No app credits or API key are charged
as an error-recovery path.

## Protected storage and self-hosted VM handoff

[Session guidance](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
requires separate registrations and serialized rotating refresh. API/worker share encrypted,
atomically replaced files in `CHATGPT_AUTH_DIR` (default `.local/chatgpt`; Compose uses a private
`chatgpt_credentials` volume). Directories are 0700; keys/records are 0600. Cross-process locks
serialize renewal. The encryption key is in the protected runtime directory: protect that
service account, volume and backups; encryption cannot defend a compromised runtime holding
both key and ciphertext. Tokens are absent from PostgreSQL, HTTP account responses and logs.

For a [self-hosted VM](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms),
complete authorization locally, then use the owner-operated native runtime commands:

```bash
python -m app.chatgpt.transfer export --account-id YOUR_ACCOUNT_UUID --file /tmp/adjacent.siwc
# Transfer the encrypted file over SSH. On the VM, with the app installed and configured:
python -m app.chatgpt.transfer import --file /secure/path/adjacent.siwc
```

Both commands prompt for a transfer passphrase without echoing it. Stop the source worker first so no requests are in flight. Export hands off the
session by clearing local tokens without revoking the grant, so only the destination renews
it. Import re-encrypts with the VM's key and preserves its separately generated host ID.
Use `CHATGPT_DEPLOYMENT_MODE=self_hosted`; do not overwrite the VM host record with a laptop
copy. Run imports as the service user and keep the encrypted transfer file owner-only. With
Docker, perform these operations inside the service runtime/credential volume with matching
file ownership. No SSH transfer is automated and no actual user session was exported in tests.
Host-specific attribution/revocation of transferred sessions remains a documented preview gap.

## Verification boundary

Tests use generated RSA keys, MockTransport OAuth/JWKS/model/SSE responses, isolated protected
stores and migrated PostgreSQL. They cover consent, identity attacks, browser state, account
switching, token rotation/revocation, scopes, usage errors, preview request shape, job binding,
optional-stage persistence and encrypted VM handoff. No live sign-in or inference was used.
You must connect your account to validate real preview admission and available models. The
server remains stopped until you choose to start it. Do not treat this as deployed-commercial
approval, unlimited usage, or multi-tenant account isolation.


Factual extraction now uses compact shared source-ID citations and stops before marketing
when no usable facts survive validation. See [evidence quality](evidence-quality.md) for
`SKIPPED` stage semantics, pre-pruning counts, bounded context and stage-specific deadlines.
Timeouts, stream disconnections and explicit incomplete responses have separate error codes;
none triggers an automatic inference retry or a billing-provider fallback.
