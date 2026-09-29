#!/usr/bin/env python3
"""Live check: profile reasoning choices reach real providers.

Sends one short request per case with reasoning at its lowest and highest
catalog choice and compares the reasoning tokens each provider reports. A
setting that is silently dropped shows up as identical usage on both sides.

Not part of CI (it spends real tokens and needs real keys). Run on demand,
for example before a release, from a container with the API dependencies:

    op run --env-file=<refs> -- docker exec -i -e OPENROUTER_API_KEY \\
        -e ANTHROPIC_API_KEY -w /app <api-container> python - \\
        < api/scripts/check_live_reasoning.py

Cases whose key is absent are reported as not run.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, replace
from decimal import Decimal

from pydantic_ai.messages import ModelRequest
from pydantic_ai.models import ModelRequestParameters

from src.services.agent_runtime.model_factory import agent_model_settings, create_agent_model
from src.services.agent_runtime.usage import provider_reported_cost, reasoning_tokens
from src.services.llm.base import LLMConfig
from src.services.model_catalog import load_bundled_catalog

PROMPT = (
    "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the "
    "ball. How many cents does the ball cost? Reply with only the number."
)
OPENROUTER = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class Case:
    catalog_provider: str
    model: str
    provider: str
    endpoint: str | None
    key_env: str
    low: str
    high: str


CASES = [
    Case("openrouter", "anthropic/claude-haiku-4.5", "openai", OPENROUTER, "OPENROUTER_API_KEY", "off", "on"),
    Case("openrouter", "deepseek/deepseek-v4-flash-0731", "openai", OPENROUTER, "OPENROUTER_API_KEY", "off", "max"),
    Case("openrouter", "google/gemini-3.8-flash", "openai", OPENROUTER, "OPENROUTER_API_KEY", "low", "high"),
    Case("openrouter", "openai/gpt-5.4-nano", "openai", OPENROUTER, "OPENROUTER_API_KEY", "none", "xhigh"),
    Case("anthropic", "claude-haiku-4-5", "anthropic", None, "ANTHROPIC_API_KEY", "off", "on"),
]


async def run(config: LLMConfig) -> tuple[int, int, Decimal | None, str]:
    model = create_agent_model(config)
    settings = agent_model_settings(config, max_tokens=4096, session_id="live-reasoning-check")
    response = await model.request(
        [ModelRequest.user_text_prompt(PROMPT)],
        settings,  # type: ignore[arg-type]
        ModelRequestParameters(),
    )
    return (
        reasoning_tokens(response.usage),
        response.usage.output_tokens,
        provider_reported_cost(response),
        (response.text or "").strip()[:20],
    )


async def main() -> int:
    catalog = load_bundled_catalog()
    failures = 0
    for case in CASES:
        key = os.environ.get(case.key_env)
        label = f"{case.catalog_provider}:{case.model}"
        if not key:
            print(f"NOT RUN  {label}: {case.key_env} is not set")
            continue
        entry = catalog.model(case.catalog_provider, case.model)
        choices = entry.reasoning_choices if entry else []
        missing = [c for c in (case.low, case.high) if c not in choices]
        if missing:
            print(f"FAIL     {label}: catalog does not list {missing} (has {choices})")
            failures += 1
            continue
        base = LLMConfig(
            provider=case.provider,  # type: ignore[arg-type]
            model=case.model,
            api_key=key,
            endpoint=case.endpoint,
            catalog_provider_id=case.catalog_provider,
        )
        results = {}
        for choice in (case.low, case.high):
            try:
                results[choice] = await run(replace(base, reasoning_effort=choice))
            except Exception as exc:  # report every case, then fail the run
                results[choice] = exc
        for choice, result in results.items():
            if isinstance(result, Exception):
                print(f"FAIL     {label} [{choice}]: {type(result).__name__}: {result}")
            else:
                reasoning, output, cost, text = result
                print(
                    f"         {label} [{choice}]: reasoning={reasoning} output={output} "
                    f"cost={cost} answer={text!r}"
                )
        low, high = results[case.low], results[case.high]
        if isinstance(low, Exception) or isinstance(high, Exception):
            failures += 1
            continue
        # The higher choice must produce reasoning, and more of it than the
        # lower one; "off"/"none" must produce none.
        ok = high[0] > 0 and high[0] > low[0]
        if case.low in ("off", "none"):
            ok = ok and low[0] == 0
        print(f"{'PASS' if ok else 'FAIL'}     {label}: {case.low} -> {case.high}")
        failures += 0 if ok else 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
