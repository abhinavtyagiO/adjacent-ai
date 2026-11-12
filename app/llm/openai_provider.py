"""OpenAI strategy, retaining strict Responses output and the existing model default."""

from app.config import Settings
from app.llm.responses import StructuredResponsesProvider


class OpenAIProvider(StructuredResponsesProvider):
    name = "openai"

    def __init__(self, settings: Settings):
        super().__init__(
            settings,
            name=self.name,
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            api_key_variable="OPENAI_API_KEY",
        )
