"""ChatGPT-plan Responses strategy: account-bound, streaming, never API-key billed."""

import asyncio
import json
import time

import httpx
from openai.lib._pydantic import to_strict_json_schema

from app.chatgpt.service import RESOURCE, ChatGPTService, local_only
from app.errors import DomainError
from app.llm.responses import StructuredResponsesProvider, parse_output


class ChatGPTProvider(StructuredResponsesProvider):
    name = "openai_chatgpt"

    def __init__(
        self, settings, service=None, account_id=None, factual_model=None, marketing_model=None
    ):
        super().__init__(
            settings,
            name=self.name,
            model="account-selected",
            api_key=None,
            api_key_variable="ChatGPT OAuth",
        )
        self.service = service or ChatGPTService(settings)
        self.account_id = str(account_id) if account_id else None
        self.job_models = (factual_model, marketing_model)
        self.usage_records = []

    def for_job(self, account_id=None, factual_model=None, marketing_model=None):
        local_only(self.settings)
        if not account_id:
            raise DomainError(
                "CHATGPT_ACCOUNT_REQUIRED",
                "This job needs an explicitly bound ChatGPT account",
                401,
            )
        account = self.service.require_account(account_id)
        fact = factual_model or self.settings.factual_model or account.get("selected_model")
        marketing = (
            marketing_model or self.settings.marketing_model or account.get("selected_model")
        )
        if not fact or not marketing:
            raise DomainError(
                "CHATGPT_MODEL_REQUIRED", "Choose an account-specific model before extraction", 422
            )
        settings = self.settings.model_copy(
            update={"factual_model": fact, "marketing_model": marketing}
        )
        provider = ChatGPTProvider(settings, self.service, account_id, fact, marketing)
        provider.model = fact
        return provider

    async def _request(
        self, payload, response_type, prompt, stage, model, effort, max_output_tokens
    ):
        local_only(self.settings)
        if not self.account_id:
            raise DomainError(
                "CHATGPT_ACCOUNT_REQUIRED", "Sign in and bind a ChatGPT account first", 401
            )
        catalog = await self.service.models(self.account_id)
        if model not in {item["slug"] for item in catalog}:
            raise DomainError(
                "CHATGPT_MODEL_UNAVAILABLE",
                "Requested model is unavailable to the selected account",
                422,
            )
        token = await self.service.access_token(self.account_id)
        reservation = (
            len(json.dumps(payload))
            + len(prompt)
            + len(json.dumps(response_type.model_json_schema()))
        ) // 3 + max_output_tokens
        if (
            sum(r["budget_tokens"] for r in self.usage_records) + reservation
            > self.settings.llm_max_total_tokens
        ):
            raise DomainError("LLM_BUDGET", "Pipeline token budget exhausted")
        body = {
            "model": model,
            "instructions": prompt,
            "input": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": response_type.__name__,
                    "strict": True,
                    "schema": to_strict_json_schema(response_type),
                }
            },
            "store": False,
            "stream": True,
        }
        if effort:
            body["reasoning"] = {"effort": effort}
        # Preview forbids max_output_tokens/system messages/background/metadata/etc.
        # Bound the local stream instead; this cannot impose a server-side token cap.
        record = {
            "stage": stage,
            "provider": self.name,
            "model": model,
            "reasoning_effort": effort,
            "attempt": 1,
            "input_tokens": None,
            "output_tokens": None,
            "cached_input_tokens": None,
            "budget_tokens": reservation,
            "status": "failed",
            "billing_source": "chatgpt_plan",
        }
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.settings.stage_timeout(stage)):
                if self.service.client:
                    result = await self._stream(
                        self.service.client, body, token, record, max_output_tokens
                    )
                else:
                    async with httpx.AsyncClient(
                        timeout=self.settings.stage_timeout(stage),
                        follow_redirects=False,
                        trust_env=False,
                    ) as client:
                        result = await self._stream(client, body, token, record, max_output_tokens)
            try:
                parsed = parse_output(result, response_type)
            except ValueError as error:
                raise DomainError(
                    "LLM_INVALID_OUTPUT",
                    "ChatGPT returned invalid structured output; "
                    "no retry or billing fallback was performed",
                ) from error
            record["status"] = "succeeded"
            return parsed
        except (httpx.TimeoutException, TimeoutError) as error:
            record["error_code"] = "CHATGPT_TIMEOUT"
            raise DomainError(
                "CHATGPT_TIMEOUT",
                "ChatGPT stage deadline or network timeout exceeded; no automatic inference retry",
                504,
                details={"stage": stage, "timeout_seconds": self.settings.stage_timeout(stage)},
            ) from error
        except httpx.HTTPError as error:
            record["error_code"] = "CHATGPT_STREAM_DISCONNECTED"
            raise DomainError(
                "CHATGPT_STREAM_DISCONNECTED",
                "ChatGPT connection failed or disconnected; no automatic inference retry",
                503,
            ) from error
        except DomainError as error:
            record["error_code"] = error.code
            raise
        finally:
            record["latency_seconds"] = round(time.monotonic() - started, 3)
            self.usage_records.append(record)

    async def _stream(self, client, body, token, record, output_budget):
        async with client.stream(
            "POST",
            RESOURCE + "/responses",
            json=body,
            headers={"Authorization": "Bearer " + token, "Accept": "text/event-stream"},
        ) as response:
            request_id = response.headers.get("x-request-id")
            record["request_id"] = request_id
            if response.status_code != 200:
                await response.aread()
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                raise self.service.upstream_error(
                    self.account_id, response.status_code, payload, request_id
                )
            parts, size, event_lines = [], 0, []
            completed = False
            async for line in response.aiter_lines():
                if len(line) > 1_000_000:
                    raise DomainError(
                        "LLM_OUTPUT_BUDGET", "ChatGPT stream event exceeded the local size limit"
                    )
                if line.startswith("data:"):
                    event_lines.append(line[5:].lstrip())
                    if sum(map(len, event_lines)) > 1_000_000:
                        raise DomainError(
                            "LLM_OUTPUT_BUDGET",
                            "ChatGPT stream event exceeded the local size limit",
                        )
                    continue
                if line != "" or not event_lines:
                    continue
                raw = "\n".join(event_lines)
                event_lines = []
                if raw == "[DONE]":
                    break
                try:
                    event = json.loads(raw)
                except ValueError as error:
                    raise DomainError(
                        "CHATGPT_STREAM_INVALID", "ChatGPT returned an invalid stream event", 502
                    ) from error
                if not isinstance(event, dict):
                    raise DomainError("CHATGPT_STREAM_INVALID", "Invalid stream event shape", 502)
                kind = event.get("type")
                if kind == "response.output_text.delta":
                    delta = event.get("delta")
                    if not isinstance(delta, str):
                        raise DomainError(
                            "CHATGPT_STREAM_INVALID", "ChatGPT returned an invalid text event", 502
                        )
                    parts.append(delta)
                    size += len(delta)
                    if size > min(output_budget * 5, 200000):
                        raise DomainError(
                            "LLM_OUTPUT_BUDGET", "ChatGPT output exceeded the local assembly limit"
                        )
                elif kind in {"response.failed", "error"}:
                    payload = (
                        event.get("response", {})
                        if kind == "response.failed"
                        else {"error": {"code": event.get("code"), "param": event.get("param")}}
                    )
                    if isinstance(payload, dict):
                        self._usage(payload, record)
                    raise self.service.upstream_error(
                        self.account_id, response.status_code, payload, request_id
                    )
                elif kind == "response.incomplete":
                    self._usage(event.get("response", {}), record)
                    raise DomainError(
                        "CHATGPT_RESPONSE_INCOMPLETE",
                        "ChatGPT reported an incomplete response; no automatic inference retry",
                        502,
                    )
                elif kind == "response.completed":
                    result = event.get("response", {})
                    if not isinstance(result, dict):
                        raise DomainError("CHATGPT_STREAM_INVALID", "Invalid completion event", 502)
                    self._usage(result, record)
                    if result.get("status") != "completed":
                        raise DomainError(
                            "CHATGPT_STREAM_INVALID", "ChatGPT did not complete the response", 502
                        )
                    completed = True
                    break
            if not completed:
                raise DomainError(
                    "CHATGPT_STREAM_DISCONNECTED", "Stream ended without response.completed", 503
                )
            if not parts:
                raise DomainError("LLM_REFUSED", "ChatGPT returned no structured text")
            return "".join(parts)

    @staticmethod
    def _usage(response, record):
        usage = response.get("usage") or {}
        if isinstance(usage.get("input_tokens"), int) and isinstance(
            usage.get("output_tokens"), int
        ):
            record.update(
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cached_input_tokens=(usage.get("input_tokens_details") or {}).get(
                    "cached_tokens", 0
                ),
                budget_tokens=usage["input_tokens"] + usage["output_tokens"],
            )
