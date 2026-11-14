from collections.abc import Callable
from typing import Protocol

from app.config import Settings
from app.enrichment import SourceBundle
from app.errors import DomainError
from app.schemas import BIP, ExtractionResult, MarketingResult


class LLMProvider(Protocol):
    """Strategy interface consumed by every LLM step in brand onboarding.

    ExtractionWorker is the context: inject one selected strategy at startup, while
    crawling, evidence validation, review and persistence remain provider-independent.
    """

    name: str
    model: str

    async def extract_facts(self, website_url: str, sources: SourceBundle) -> ExtractionResult: ...

    async def infer_marketing(
        self, profile: BIP, sources: SourceBundle, selected_offering_key: str | None = None
    ) -> MarketingResult: ...

    async def extract(self, website_url: str, sources: SourceBundle) -> ExtractionResult:
        """Return schema-validated data. Evidence is independently checked by the service."""
        ...


def build_provider(settings: Settings) -> LLMProvider:
    """Resolve configuration to a strategy; never fall back to a different paid service."""
    from app.llm.chatgpt_provider import ChatGPTProvider
    from app.llm.openai_provider import OpenAIProvider

    strategies: dict[str, Callable[[Settings], LLMProvider]] = {
        ChatGPTProvider.name: ChatGPTProvider,
        OpenAIProvider.name: OpenAIProvider,
    }
    if settings.llm_provider == "deepseek":
        raise DomainError("LLM_PROVIDER_DISABLED", "DeepSeek is disabled for application requests")
    if settings.llm_provider == "openai" and not settings.openai_api_key_billing_enabled:
        raise DomainError(
            "API_KEY_BILLING_DISABLED",
            "API-key billing needs an explicit opt-in; use ChatGPT sign-in instead",
        )
    strategy = strategies.get(settings.llm_provider)
    if strategy is None:
        raise DomainError("LLM_CONFIG", "Unsupported LLM_PROVIDER; choose openai_chatgpt")
    return strategy(settings)
