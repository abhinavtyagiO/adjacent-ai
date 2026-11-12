from typing import Protocol

from app.config import Settings
from app.enrichment import SourceBundle
from app.errors import DomainError
from app.schemas import ExtractionResult


class LLMProvider(Protocol):
    name: str
    model: str

    async def extract(self, website_url: str, sources: SourceBundle) -> ExtractionResult:
        """Return schema-validated data. Evidence is independently checked by the service."""
        ...


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "openai":
        from app.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(settings)
    raise DomainError("LLM_CONFIG", "Unsupported LLM_PROVIDER configuration")
