"""Two structured requests over the authorized existing transport; no retries or fallback."""

import copy
import json

from app.creative.prompts import EVALUATOR, GENERATOR
from app.creative.schemas import EvaluatorResult, GeneratorResult
from app.errors import DomainError


class CreativeProvider:
    def __init__(self, provider, settings, model):
        self.transport = copy.copy(provider)
        self.transport.settings = provider.settings.model_copy(update={"llm_max_attempts": 1})
        self.transport.usage_records = []
        self.settings, self.model = settings, model
        self.calls = 0

    @property
    def usage_records(self):
        return self.transport.usage_records

    async def request(self, payload, schema, prompt, stage, limit):
        # Same conservative character estimate used by Phase 1; schema overhead included.
        size = (
            len(json.dumps(payload, ensure_ascii=False))
            + len(prompt)
            + len(json.dumps(schema.model_json_schema()))
        )
        if size > self.settings.creative_max_input_tokens * 3 - 2000:
            raise DomainError("CREATIVE_INPUT_BUDGET", "Creative request exceeds input limit", 422)
        if self.calls >= 2:
            raise DomainError("CREATIVE_CALL_BUDGET", "At most two inference requests are allowed")
        self.calls += 1
        return await self.transport.request_structured(
            payload,
            schema,
            prompt,
            stage,
            self.model,
            self.settings.marketing_reasoning_effort,
            limit,
        )

    async def generate(self, research):
        return await self.request(
            {"research": research},
            GeneratorResult,
            GENERATOR,
            "creative_generator",
            self.settings.creative_generator_max_output_tokens,
        )

    async def evaluate(self, research, candidates):
        # The evaluator needs referenced facts and brand guidance, not another crawl dump.
        referenced = set()
        for row in candidates:
            content = row["candidate"]
            referenced.update(content["research_reference_ids"])
            referenced.update(content["capability_reference_ids"])
            for asset in content["asset_requirements"]:
                referenced.update(asset["research_reference_ids"])
        compact = {
            "brief": research["brief"],
            "restrictions": research["restrictions"],
            "limitations": research["limitations"],
            "items": [
                i
                for i in research["items"]
                if i["id"] in referenced
                or i["path"].startswith(
                    (
                        "/brand_identity/",
                        "/brand_voice/",
                        "/business_positioning/",
                        "/visual_identity/",
                    )
                )
            ],
        }
        return await self.request(
            {"research": compact, "candidates": candidates},
            EvaluatorResult,
            EVALUATOR,
            "creative_evaluator",
            self.settings.creative_evaluator_max_output_tokens,
        )
