from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest
from pydantic import SecretStr

from app.errors import DomainError
from app.llm.base import build_provider as runtime_provider
from app.llm.deepseek_provider import DeepSeekProvider


def build_provider(settings):
    """Legacy wire-format tests instantiate the disabled adapter directly, never runtime routing."""
    if settings.llm_provider == "deepseek":
        return DeepSeekProvider(settings)
    settings.openai_api_key_billing_enabled = True
    return runtime_provider(settings)


@pytest.fixture(autouse=True, params=["deepseek", "openai"])
def configured_strategy(settings, request):
    settings.llm_provider = request.param
    settings.llm_max_attempts = 3
    settings.deepseek_api_key = SecretStr("deepseek-test-only")
    settings.openai_api_key = SecretStr("openai-test-only")


def mock_client(monkeypatch, side_effect):
    client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=side_effect)))
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr("app.llm.responses.AsyncOpenAI", MagicMock(return_value=context))
    monkeypatch.setattr("app.llm.responses.asyncio.sleep", AsyncMock())
    return client


async def test_missing_key_has_actionable_safe_error(settings, bundle):
    setattr(settings, f"{settings.llm_provider}_api_key", None)
    with pytest.raises(DomainError) as error:
        await build_provider(settings).extract("https://acme.example/", bundle)
    assert error.value.code == "LLM_CONFIG"
    assert settings.llm_provider.upper() + "_API_KEY" in error.value.message


async def test_retry_on_rate_limit_and_structured_schema_used(
    settings, bundle, extraction_result, monkeypatch
):
    settings.openai_api_key = SecretStr("test-only")
    response = httpx.Response(
        429, request=httpx.Request("POST", "https://api.openai.com/v1/responses")
    )
    client = mock_client(
        monkeypatch,
        [
            openai.RateLimitError("secret upstream body", response=response, body=None),
            SimpleNamespace(output_text=extraction_result.model_dump_json()),
        ],
    )
    result = await build_provider(settings).extract("https://acme.example/", bundle)
    assert result == extraction_result and client.responses.create.await_count == 2
    assert client.responses.create.call_args.kwargs["text"]["format"]["name"] == "ExtractionResult"
    assert client.responses.create.call_args.kwargs["store"] is False


async def test_authentication_error_not_retried_or_leaked(settings, bundle, monkeypatch):
    settings.openai_api_key = SecretStr("test-only")
    response = httpx.Response(
        401, request=httpx.Request("POST", "https://api.openai.com/v1/responses")
    )
    client = mock_client(
        monkeypatch,
        [
            openai.AuthenticationError(
                "secret upstream body",
                response=response,
                body=None,
            )
        ],
    )
    with pytest.raises(DomainError) as error:
        await build_provider(settings).extract("https://acme.example/", bundle)
    assert client.responses.create.await_count == 1
    assert "secret" not in error.value.message


async def test_refusal_never_becomes_an_empty_success(settings, bundle, monkeypatch):
    settings.openai_api_key = SecretStr("test-only")
    mock_client(monkeypatch, [SimpleNamespace(output_text="")])
    with pytest.raises(DomainError) as error:
        await build_provider(settings).extract("https://acme.example/", bundle)
    assert error.value.code == "LLM_REFUSED"


async def test_actual_sdk_sends_strict_json_schema_and_parses_response(
    settings, bundle, extraction_result, monkeypatch
):
    """Exercise the real SDK serialization/parsing boundary without a paid API call."""
    import json

    settings.openai_api_key = SecretStr("test-only")
    real_client = openai.AsyncOpenAI

    def handler(request):
        body = json.loads(request.content)
        if settings.llm_provider == "deepseek":
            assert request.url.host == "api.deepseek.com" and request.url.path == "/responses"
            assert request.headers["authorization"] == "Bearer deepseek-test-only"
            assert body["reasoning"] == {"effort": "none"}
            assert body["model"] == settings.deepseek_model
        else:
            assert request.url.host == "api.openai.com" and request.url.path == "/v1/responses"
            assert request.headers["authorization"] == "Bearer test-only"
            assert "reasoning" not in body
            assert body["model"] == settings.openai_model
        output_format = body["text"]["format"]
        assert output_format["type"] == "json_schema" and output_format["strict"] is True
        schema = output_format["schema"]
        assert schema["additionalProperties"] is False
        identity = schema["$defs"]["BrandIdentity"]
        assert set(identity["required"]) == set(identity["properties"])
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": build_provider(settings).model,
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": extraction_result.model_dump_json(),
                                "annotations": [],
                            }
                        ],
                    }
                ],
            },
        )

    monkeypatch.setattr(
        "app.llm.responses.AsyncOpenAI",
        lambda **kwargs: real_client(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ),
    )
    result = await build_provider(settings).extract("https://acme.example/", bundle)
    assert result == extraction_result


async def test_truncated_response_is_clear(settings, bundle, monkeypatch):
    settings.openai_api_key = SecretStr("test-only")
    client = mock_client(
        monkeypatch,
        [SimpleNamespace(output_text="", status="incomplete")] * settings.llm_max_attempts,
    )
    with pytest.raises(DomainError) as error:
        await build_provider(settings).extract("https://acme.example/", bundle)
    assert error.value.code == "LLM_INVALID_OUTPUT"
    assert client.responses.create.await_count == settings.llm_max_attempts


@pytest.mark.parametrize("problem", ["invalid", "truncated"])
async def test_structured_output_retry_recovers_without_logging_payload(
    settings, bundle, extraction_result, monkeypatch, caplog, problem
):
    from pydantic import ValidationError

    from app.schemas import ExtractionResult

    if problem == "invalid":
        with pytest.raises(ValidationError) as validation:
            ExtractionResult.model_validate({"profile": "private-payload-value"})
        first = validation.value
    else:
        first = SimpleNamespace(output_text="", status="incomplete")
    client = mock_client(
        monkeypatch, [first, SimpleNamespace(output_text=extraction_result.model_dump_json())]
    )
    assert (
        await build_provider(settings).extract("https://acme.example/", bundle) == extraction_result
    )
    assert client.responses.create.await_count == 2
    assert "private-payload-value" not in caplog.text


async def test_request_timeout_is_bounded_and_retried(settings, bundle, monkeypatch):
    import asyncio

    settings.llm_max_attempts = 2
    settings.llm_timeout_seconds = 0.01
    client = mock_client(monkeypatch, None)

    # An unresolved future exercises cancellation without depending on patched sleep.
    async def never_finishes(**kwargs):
        await asyncio.Future()

    client.responses.create.side_effect = never_finishes
    with pytest.raises(DomainError) as error:
        await build_provider(settings).extract("https://acme.example/", bundle)
    assert error.value.code == "LLM_UNAVAILABLE" and error.value.retryable
    assert client.responses.create.await_count == 2


async def test_invalid_schema_is_rejected_using_the_real_sdk(settings, bundle, monkeypatch):
    real_client = openai.AsyncOpenAI

    def handler(request):
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": build_provider(settings).model,
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"invented_section":true}',
                                "annotations": [],
                            }
                        ],
                    }
                ],
            },
        )

    monkeypatch.setattr(
        "app.llm.responses.AsyncOpenAI",
        lambda **kwargs: real_client(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ),
    )
    with pytest.raises(DomainError) as error:
        await build_provider(settings).extract("https://acme.example/", bundle)
    assert error.value.code == "LLM_INVALID_OUTPUT"
