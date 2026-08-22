"""Dry-run a proposed prompt against a past run's transcript.

Single LLM call: "given this proposed prompt, would you still make the same
decision as recorded in this completed run?". No tool execution, no
engine replay — this is a cheap counterfactual check used by the tuning UI
to preview the impact of a proposed prompt change before the user commits
to it.

Records an ``AIUsage`` row against the original run with ``sequence=8000``
so dry-run cost shows up on the run's cost breakdown (alongside the
agent's own LLM calls and the summarization/tuning calls) without being
confused with the agent's own reasoning.
"""
import json
import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.log_safety import log_safe
from src.models.orm.agent_runs import AgentRun, AgentRunStep
from src.core.cache import get_shared_redis
from src.services.ai_usage_service import record_ai_usage
from src.services.execution.model_selection import get_tuning_client
from src.services.llm import LLMMessage

logger = logging.getLogger(__name__)


DRY_RUN_SYSTEM_PROMPT = """You evaluate whether a proposed system prompt change would alter an agent's past decision.

Given: (1) a proposed new system prompt, (2) the original user input, (3) the agent's recorded execution transcript (if any), and (4) the final output the agent produced.

Answer: with this new prompt, would you have made the same decision?

Return ONLY a JSON object:
{
  "would_still_decide_same": bool,
  "reasoning": "<one or two sentences explaining your conclusion>",
  "alternative_action": "<null if same decision; otherwise what you would do instead, one sentence>",
  "confidence": <float 0.0-1.0>
}
Be honest. If the new prompt has no relevant guidance, say would_still_decide_same=true."""


@dataclass
class DryRunResult:
    """Structured output of a dry-run evaluation."""

    would_still_decide_same: bool
    reasoning: str
    alternative_action: str | None
    confidence: float


async def evaluate_against_prompt(
    *,
    run_id: UUID,
    proposed_prompt: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> DryRunResult:
    """Run one LLM call asking whether ``proposed_prompt`` would change the run's decision.

    Loads input/output/steps, prompts the tuning model with the full context,
    parses a small JSON verdict, and records an ``AIUsage`` row on the original
    run (``sequence=8000``) for cost tracking.
    """
    # Phase 1: load run + resolve tuning client
    async with session_factory() as db:
        run = (
            await db.execute(select(AgentRun).where(AgentRun.id == run_id))
        ).scalar_one()
        llm_client, resolved_model = await get_tuning_client(db)
        run_input = run.input
        run_output = run.output
        org_id = run.org_id

        # Load steps while the session is open.
        steps = (
            (
                await db.execute(
                    select(AgentRunStep)
                    .where(AgentRunStep.run_id == run_id)
                    .order_by(AgentRunStep.step_number)
                )
            )
            .scalars()
            .all()
        )

    transcript: list[dict] = []
    for s in steps:
        content = s.content if isinstance(s.content, dict) else {}
        if s.type == "tool_call":
            transcript.append(
                {
                    "role": "tool_call",
                    "tool": content.get("tool"),
                    "args": content.get("args", {}),
                    "result": content.get("result"),
                }
            )
        else:
            transcript.append({"role": "agent_reasoning", "content": content})

    payload = {
        "proposed_prompt": proposed_prompt,
        "original_input": run_input,
        "transcript": transcript[:40],
        "original_output": run_output,
    }

    messages = [
        LLMMessage(role="system", content=DRY_RUN_SYSTEM_PROMPT),
        LLMMessage(
            role="user", content=json.dumps(payload, default=str)
        ),
    ]

    # Phase 2: LLM call (no DB connection held)
    response = await llm_client.complete(
        messages=messages, model=resolved_model, max_tokens=600
    )

    try:
        parsed = json.loads(response.content or "")
    except json.JSONDecodeError:
        logger.warning("Dry-run returned invalid JSON for run %s", log_safe(run_id))
        parsed = {
            "would_still_decide_same": True,
            "reasoning": "Unable to evaluate (model returned invalid JSON)",
            "alternative_action": None,
            "confidence": 0.0,
        }

    if not isinstance(parsed, dict):
        parsed = {
            "would_still_decide_same": True,
            "reasoning": "Unable to evaluate (model did not return a JSON object)",
            "alternative_action": None,
            "confidence": 0.0,
        }

    # Phase 3: persist AIUsage row
    async with session_factory() as db:
        await record_ai_usage(
            session=db,
            redis_client=await get_shared_redis(),
            agent_run_id=run_id,
            organization_id=org_id,
            provider=llm_client.provider_name,
            model=response.model or resolved_model,
            input_tokens=response.input_tokens or 0,
            output_tokens=response.output_tokens or 0,
            cache_read_tokens=response.cache_read_tokens,
            cache_write_tokens=response.cache_write_tokens,
            provider_cost=response.provider_cost,
            sequence=8000,
        )
        await db.commit()

    conf_raw = parsed.get("confidence")
    try:
        conf_f = (
            max(0.0, min(1.0, float(conf_raw))) if conf_raw is not None else 0.0
        )
    except (TypeError, ValueError):
        conf_f = 0.0

    alt = parsed.get("alternative_action")
    alt_str = str(alt)[:500] if alt else None

    return DryRunResult(
        would_still_decide_same=bool(parsed.get("would_still_decide_same", True)),
        reasoning=str(parsed.get("reasoning") or "")[:500],
        alternative_action=alt_str,
        confidence=conf_f,
    )
