"""Real provider strategies over mocked transports only; never live inference."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest
from pydantic import SecretStr

from app.creative.provider import CreativeProvider
from app.creative.schemas import GeneratorResult
from app.errors import DomainError
from app.llm.openai_provider import OpenAIProvider
from tests.creative_helpers import evaluated, generated, research
from tests.test_chatgpt import chatgpt as chatgpt_fixture
from tests.test_chatgpt import signing_key as signing_key_fixture

chatgpt = chatgpt_fixture
signing_key = signing_key_fixture


async def test_chatgpt_plan_generator_and_evaluator_reuse_stream_contract(chatgpt, caplog):
    transport, account = await chatgpt.provider()
    adapter = CreativeProvider(transport, chatgpt.settings, "account-model")
    context = research("opal")
    chatgpt.marketing = generated(context)
    result = await adapter.generate(context)
    chatgpt.marketing = evaluated(result.candidates)
    evaluation = await adapter.evaluate(
        context, [{"candidate": c.model_dump(), "validation": {}} for c in result.candidates]
    )
    calls = [r for r in chatgpt.calls if r.url.path == "/v1/responses"]
    assert len(calls) == 2 and len(evaluation.evaluations) == 6
    for request in calls:
        body = json.loads(request.content)
        assert body["stream"] is True and body["store"] is False
        assert body["model"] == "account-model" and "max_output_tokens" not in body
        assert all(i["role"] != "system" for i in body["input"])
    assert all(
        r["billing_source"] == "chatgpt_plan" and r["attempt"] == 1 for r in adapter.usage_records
    )
    assert [r["stage"] for r in adapter.usage_records] == [
        "creative_generator",
        "creative_evaluator",
    ]
    assert "PRIVATE_REASONING" not in caplog.text + json.dumps(adapter.usage_records)
    eval_payload = json.loads(json.loads(calls[1].content)["input"][0]["content"])
    assert "source_documents" not in eval_payload["research"]
    assert not any(i["kind"] == "SAVED_SOURCE_PASSAGE" for i in eval_payload["research"]["items"])
    assert eval_payload["research"]["restrictions"] == context["restrictions"]


async def test_chatgpt_usage_limit_does_not_retry_or_fall_back(chatgpt):
    transport, _ = await chatgpt.provider()
    chatgpt.stream_status = 429
    adapter = CreativeProvider(transport, chatgpt.settings, "account-model")
    with pytest.raises(DomainError):
        await adapter.generate(research("opal"))
    assert len([r for r in chatgpt.calls if r.url.path == "/v1/responses"]) == 1
    assert len(adapter.usage_records) == 1 and adapter.usage_records[0]["status"] == "failed"


async def test_api_key_transport_is_forced_to_one_attempt_and_records_bad_output_usage(
    settings, monkeypatch
):
    settings.openai_api_key = SecretStr("fake-test-only")
    settings.llm_max_attempts = 5
    create = AsyncMock(
        return_value=SimpleNamespace(
            output_text="{invalid",
            status="completed",
            usage=SimpleNamespace(input_tokens=150, output_tokens=40, input_tokens_details=None),
        )
    )
    context = MagicMock()
    context.__aenter__ = AsyncMock(
        return_value=SimpleNamespace(responses=SimpleNamespace(create=create))
    )
    context.__aexit__ = AsyncMock(return_value=None)
    sdk = MagicMock(return_value=context)
    monkeypatch.setattr("app.llm.responses.AsyncOpenAI", sdk)
    adapter = CreativeProvider(OpenAIProvider(settings), settings, "explicit-test-model")
    with pytest.raises(DomainError):
        await adapter.generate(research("opal"))
    assert create.await_count == 1 and sdk.call_args.kwargs["max_retries"] == 0
    assert create.call_args.kwargs["text"]["format"]["name"] == GeneratorResult.__name__
    assert create.call_args.kwargs["model"] == "explicit-test-model"
    assert (
        create.call_args.kwargs["max_output_tokens"]
        == settings.creative_generator_max_output_tokens
    )
    assert (
        adapter.usage_records[0]["input_tokens"] == 150
        and adapter.usage_records[0]["output_tokens"] == 40
    )


async def test_api_key_rate_limit_does_not_retry_with_larger_global_retry_setting(
    settings, monkeypatch
):
    settings.openai_api_key = SecretStr("fake-test-only")
    settings.llm_max_attempts = 5
    response = httpx.Response(
        429, request=httpx.Request("POST", "https://api.openai.com/v1/responses")
    )
    create = AsyncMock(
        side_effect=openai.RateLimitError("private upstream message", response=response, body=None)
    )
    context = MagicMock()
    context.__aenter__ = AsyncMock(
        return_value=SimpleNamespace(responses=SimpleNamespace(create=create))
    )
    context.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr("app.llm.responses.AsyncOpenAI", MagicMock(return_value=context))
    adapter = CreativeProvider(OpenAIProvider(settings), settings, "test-model")
    with pytest.raises(DomainError) as error:
        await adapter.generate(research("opal"))
    assert create.await_count == 1 and "private" not in error.value.message
