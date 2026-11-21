"""DeepSeek strategy using its Responses API JSON-schema output support."""

from app.config import Settings
from app.llm.responses import StructuredResponsesProvider


class DeepSeekProvider(StructuredResponsesProvider):
    name = "deepseek"

    def __init__(self, settings: Settings):
        super().__init__(
            settings,
            name=self.name,
            model=settings.deepseek_model,
            api_key=settings.deepseek_api_key,
            api_key_variable="DEEPSEEK_API_KEY",
            base_url=str(settings.deepseek_base_url),
            # Extraction needs structured evidence, rather than reasoning-token output.
            disable_thinking=True,
        )
