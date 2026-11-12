from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest
from pydantic import SecretStr

from app.errors import DomainError
from app.llm.openai_provider import OpenAIProvider


def mock_client(monkeypatch, side_effect):
    client = SimpleNamespace(responses=SimpleNamespace(parse=AsyncMock(side_effect=side_effect)))
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr("app.llm.openai_provider.AsyncOpenAI", MagicMock(return_value=context))
    monkeypatch.setattr("app.llm.openai_provider.asyncio.sleep", AsyncMock())
    return client


async def test_missing_key_has_actionable_safe_error(settings, bundle):
    settings.openai_api_key = None
    with pytest.raises(DomainError) as error:
        await OpenAIProvider(settings).extract("https://acme.example/", bundle)
    assert error.value.code == "LLM_CONFIG"


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
            SimpleNamespace(output_parsed=extraction_result),
        ],
    )
    result = await OpenAIProvider(settings).extract("https://acme.example/", bundle)
    assert result == extraction_result and client.responses.parse.await_count == 2
    assert client.responses.parse.call_args.kwargs["text_format"].__name__ == "ExtractionResult"
    assert client.responses.parse.call_args.kwargs["store"] is False


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
        await OpenAIProvider(settings).extract("https://acme.example/", bundle)
    assert client.responses.parse.await_count == 1
    assert "secret" not in error.value.message


async def test_refusal_never_becomes_an_empty_success(settings, bundle, monkeypatch):
    settings.openai_api_key = SecretStr("test-only")
    mock_client(monkeypatch, [SimpleNamespace(output_parsed=None)])
    with pytest.raises(DomainError) as error:
        await OpenAIProvider(settings).extract("https://acme.example/", bundle)
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
        assert request.url.path == "/v1/responses"
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
                "model": settings.openai_model,
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
        "app.llm.openai_provider.AsyncOpenAI",
        lambda **kwargs: real_client(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ),
    )
    result = await OpenAIProvider(settings).extract("https://acme.example/", bundle)
    assert result == extraction_result


async def test_truncated_response_is_clear(settings, bundle, monkeypatch):
    settings.openai_api_key = SecretStr("test-only")
    mock_client(monkeypatch, [SimpleNamespace(output_parsed=None, status="incomplete")])
    with pytest.raises(DomainError) as error:
        await OpenAIProvider(settings).extract("https://acme.example/", bundle)
    assert error.value.code == "LLM_INVALID_OUTPUT"
