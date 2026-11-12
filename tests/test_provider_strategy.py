import json
from pathlib import Path

import httpx
import openai
import pytest
from dotenv import dotenv_values
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.errors import DomainError
from app.llm.base import build_provider
from app.llm.deepseek_provider import DeepSeekProvider
from app.llm.openai_provider import OpenAIProvider


def test_deepseek_is_default_and_openai_is_still_selectable(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    settings = Settings(_env_file=None)
    assert isinstance(build_provider(settings), DeepSeekProvider)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert isinstance(build_provider(Settings(_env_file=None)), OpenAIProvider)


def test_unknown_provider_never_silently_selects_another_service(settings):
    settings.llm_provider = "unregistered"
    with pytest.raises(DomainError) as error:
        build_provider(settings)
    assert error.value.code == "LLM_CONFIG"


def test_env_defaults_private_overrides_and_process_precedence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for variable in ["LLM_PROVIDER", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"]:
        monkeypatch.delenv(variable, raising=False)
    (tmp_path / ".env").write_text(
        "LLM_PROVIDER=deepseek\nDEEPSEEK_API_KEY=\nDEEPSEEK_MODEL=default-model\n"
    )
    (tmp_path / ".env.local").write_text(
        "DEEPSEEK_API_KEY=local-test-only\nDEEPSEEK_MODEL=local-model\n"
    )
    settings = Settings()
    assert settings.deepseek_api_key.get_secret_value() == "local-test-only"
    assert settings.deepseek_model == "local-model"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "process-test-only")
    assert Settings().deepseek_api_key.get_secret_value() == "process-test-only"


def test_committed_environment_has_deepseek_defaults_without_credentials():
    root = Path(__file__).resolve().parents[1]
    values = dotenv_values(root / ".env")
    assert values["LLM_PROVIDER"] == "deepseek"
    assert (
        values["DEEPSEEK_API_KEY"] == values["OPENAI_API_KEY"] == values["API_ACCESS_TOKEN"] == ""
    )
    assert ".env.local" in (root / ".gitignore").read_text().splitlines()


def test_deepseek_endpoint_must_be_an_http_url():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, deepseek_base_url="not-an-api-url")


async def test_custom_deepseek_endpoint_and_model_are_used(
    settings, bundle, extraction_result, monkeypatch
):
    settings.deepseek_api_key = SecretStr("deepseek-test-only")
    settings.deepseek_model = "custom-model"
    settings.deepseek_base_url = "https://gateway.example/v1"
    real_client = openai.AsyncOpenAI

    def handler(request):
        assert str(request.url) == "https://gateway.example/v1/responses"
        assert json.loads(request.content)["model"] == "custom-model"
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "custom-model",
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
    assert (
        await build_provider(settings).extract("https://acme.example/", bundle) == extraction_result
    )
