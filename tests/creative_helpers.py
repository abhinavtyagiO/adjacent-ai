"""Explicitly synthetic model output builders; frozen real research is separate."""

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from app.creative.research import build_context
from app.creative.schemas import (
    CampaignBrief,
    Candidate,
    EvaluatorResult,
    GeneratedCandidate,
    GeneratorResult,
)
from app.schemas import BIP

FIXTURES = Path(__file__).parent / "fixtures/creative"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def docs(data):
    return [
        SimpleNamespace(
            url=d["url"],
            title=d.get("title"),
            content=d["text"],
            fetched_at=datetime.fromisoformat(d["fetched_at"]),
            source_type=d["source_type"],
            source_metadata=d.get("metadata", {}),
            content_hash=hashlib.sha256(d["text"].encode()).hexdigest(),
        )
        for d in data["documents"]
    ]


def research(name, max_chars=30000, brief=None):
    data = fixture(name)
    return build_context(
        BIP.model_validate(data["profile"]),
        data["provenance"],
        docs(data),
        brief or CampaignBrief.model_validate(data["brief"]),
        [],
        max_chars,
    )


# Different illustrative mechanisms, deliberately not the user's five reference titles.
SCENARIOS = {
    "opal": [
        (
            "The interrupted paragraph",
            "RECOGNITION",
            "INTERRUPTION_LOOP",
            "Reading the same paragraph after checking an app",
            "Protect a planned reading session",
        ),
        (
            "A calendar gap",
            "CURIOSITY",
            "MISSING_STEP",
            "Planning study time without an attention boundary",
            "Make planned focus time less vulnerable to app checks",
        ),
        (
            "Room for an evening",
            "DESIRE",
            "OUTCOME_CONTRAST",
            "Wanting time for an offline evening activity",
            "Make space for a chosen offline activity",
        ),
        (
            "A task-specific boundary",
            "RELEVANCE",
            "PRODUCT_DEMONSTRATION",
            "Starting homework with distracting apps nearby",
            "Choose a focus boundary for a specific task",
        ),
        (
            "A marker of attention",
            "CURIOSITY",
            "VISUAL_METAPHOR",
            "Wanting to notice completed focus time",
            "Make a focus milestone tangible",
        ),
        (
            "Essentials still matter",
            "RELEVANCE",
            "OBJECTION_REFRAME",
            "Worrying about access to essential apps during focus",
            "Choose a suitable blocking mode",
        ),
    ],
    "minimalist": [
        (
            "A label-reading pause",
            "RECOGNITION",
            "LABEL_CONFUSION",
            "Comparing ingredient labels before choosing skincare",
            "Understand the formula before choosing",
        ),
        (
            "Two formulas one question",
            "CURIOSITY",
            "SIDE_BY_SIDE_COMPARISON",
            "Wondering how named vitamin C formulas differ",
            "Compare supplied ingredients without assuming superior results",
        ),
        (
            "A routine with a place",
            "DESIRE",
            "ROUTINE_ROADMAP",
            "Wanting a clear place for a product in a routine",
            "Make a considered routine decision",
        ),
        (
            "One texture is not everyone",
            "RELEVANCE",
            "OBJECTION_REFRAME",
            "Considering suitability after reading mixed customer opinions",
            "Treat individual experiences as questions rather than proof",
        ),
        (
            "Beyond the face",
            "CURIOSITY",
            "CATEGORY_EXPLORATION",
            "Thinking about different sun-care formats",
            "Explore categories without transferring SPF between products",
        ),
        (
            "A question before checkout",
            "RECOGNITION",
            "DECISION_CHECKLIST",
            "Having unanswered usage questions before choosing",
            "Consider usage and suitability information",
        ),
    ],
}


def generated(context, name="opal"):
    capability = next(
        (i for i in context["items"] if i["capability"] and "/features/" in i["path"]),
        next(i for i in context["items"] if i["capability"]),
    )
    rows = []
    for i, (title, driver, mechanism, situation, aspiration) in enumerate(SCENARIOS[name]):
        rows.append(
            GeneratedCandidate(
                candidate_key=f"idea-{i}",
                title=title,
                core_insight=f"{situation}; {aspiration}.",
                psychological_driver=driver,
                strategic_mechanism=mechanism,
                audience=context["brief"].get("audience") or context["brief"]["concern"],
                audience_situation=situation,
                problem_or_aspiration=aspiration,
                product_capability=capability["text"],
                capability_reference_ids=[capability["id"]],
                primary_hook=title + "?",
                alternative_hooks=[
                    f"Consider this: {title.lower()}.",
                    f"What changes with {title.lower()}?",
                ],
                story={
                    "opening": situation,
                    "development": aspiration,
                    "product_connection": capability["text"],
                    "payoff": (
                        "Invite the viewer to explore the feature and decide whether it fits."
                    ),
                },
                on_screen_copy={"treatment": "SINGLE_CAPTION", "text": title + "?"},
                b_roll=["A simple everyday scene with readable text overlays"],
                closing_line="Explore the product and decide what fits.",
                cta=context["brief"].get("cta") or "Learn more",
                research_reference_ids=[capability["id"]],
                factual_assumptions=[],
                potential_risks=["Review claims and asset permissions before advertising."],
            )
        )
    return GeneratorResult(candidates=rows)


def evaluated(candidates, count=5):
    return EvaluatorResult(
        overlap_groups=[],
        evaluations=[
            {
                "candidate_key": c.candidate_key,
                "scores": {
                    k: 7
                    for k in (
                        "audience_relevance",
                        "hook_clarity",
                        "distinctiveness",
                        "truthful_benefit_connection",
                        "brand_consistency",
                        "production_feasibility",
                        "evidence_and_risk",
                    )
                },
                "strengths": ["Clear audience situation and source-bound capability."],
                "weaknesses": ["Requires marketer review; this score is a synthetic fixture."],
                "selection_rationale": (
                    "Illustrative distinct mechanism; no prediction of advertising performance."
                ),
                "selected": i < count,
                "grounding": {
                    **dict.fromkeys(
                        ("hooks", "story", "closing_line", "payoff", "on_screen_copy"),
                        "SUPPORTED",
                    ),
                    "explanation": "The copy stays within the supplied capability.",
                },
                "distinction": f"Different message through {c.strategic_mechanism}.",
            }
            for i, c in enumerate(candidates)
        ],
    )


class MockCreativeTransport:
    """No network; append exactly one synthetic usage record per invoked stage."""

    name = "mock"
    model = "mock-only"

    def __init__(
        self, settings, name="opal", evaluator_error=None, on_generate=None, on_evaluate=None
    ):
        self.settings = settings
        self.name_fixture = name
        self.evaluator_error = evaluator_error
        self.on_generate, self.on_evaluate = on_generate, on_evaluate
        self.calls = []
        self.usage_records = []

    async def request_structured(
        self, payload, response_type, prompt, stage, model, effort, max_output_tokens
    ):
        self.calls.append(
            {
                "stage": stage,
                "model": model,
                "effort": effort,
                "retry_limit": self.settings.llm_max_attempts,
                "output_limit": max_output_tokens,
                "payload": copy.deepcopy(payload),
            }
        )
        record = {
            "stage": stage,
            "provider": "mock",
            "model": model,
            "attempt": 1,
            "input_tokens": 100,
            "output_tokens": 50,
            "budget_tokens": 150,
            "latency_seconds": 0.01,
            "status": "succeeded",
        }
        self.usage_records.append(record)
        if stage == "creative_generator":
            result = generated(payload["research"], self.name_fixture)
            if self.on_generate:
                self.on_generate(result)
            return result
        if self.on_evaluate:
            self.on_evaluate(payload)
        if self.evaluator_error:
            record["status"] = "failed"
            raise self.evaluator_error
        return evaluated([Candidate.model_validate(c["candidate"]) for c in payload["candidates"]])
