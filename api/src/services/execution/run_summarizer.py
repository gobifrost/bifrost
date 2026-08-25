"""Post-run summarization — populates asked/did/confidence/metadata on an AgentRun.

This module owns:

- :func:`summarize_run`: load the completed run, render the input/output, ask
  the configured summarization model, and persist the parsed result onto
  ``AgentRun`` (asked/did/confidence/run_metadata/summary_status).
- :func:`enqueue_summarize`: thin RabbitMQ publish helper used by the
  ``agent-runs`` consumer once a run finishes.

Failure semantics: any error during the LLM call or JSON parsing is caught,
recorded on ``run.summary_error`` with ``summary_status='failed'``, and
swallowed. The handler in :mod:`src.jobs.summarize_worker` does the same
belt-and-suspenders so the message is never re-queued. The UI exposes a
regenerate button for recovery.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.pubsub import publish_agent_run_update
from src.core.cache import get_shared_redis
from src.jobs.rabbitmq import publish_message
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.services.ai_usage_service import record_ai_usage
from src.services.execution.model_selection import get_summarization_client
from src.services.llm import LLMMessage

logger = logging.getLogger(__name__)

SUMMARIZE_QUEUE = "agent-summarization"
SUMMARIZE_BACKFILL_QUEUE = "agent-summarization-backfill"

# Version tag written to AgentRun.summary_prompt_version on successful
# summarization. Bump this string whenever ``SUMMARIZE_SYSTEM_PROMPT`` or the
# user-message payload changes meaningfully, so admins can use the backfill
# endpoint's ``prompt_version_below`` filter to re-summarize runs stamped
# with an older version.
SUMMARIZE_PROMPT_VERSION = "v4"

SUMMARIZE_SYSTEM_PROMPT = """You summarize what an AI agent did on a single run.

You receive the agent's name, the system prompt that defines its job, the
run's input, and the run's output. Produce a JSON object with:

  - asked: one short sentence (<100 chars) describing what the user or event
    asked for, in the user's voice. Extract the specific ask, not a generic
    restatement of the agent's purpose.

  - did: a short prose explanation of how the agent worked through the
    request — the way someone would describe their work to a coworker. 1-4
    sentences (<800 chars total). Walk through the meaningful decisions
    and the reason behind each one ("I needed X, so I called Y; that gave
    me Z, which told me to..."). Skip filler tool calls (a couple of
    look-ups aren't worth narrating). DO NOT restate the agent's role or
    purpose — describe THIS run.

    *** TOOL MARKERS — STRICT RULE ***
    If the run made any tool calls (visible in the input as
    tool_call/tool_response entries, or implied by the agent's output),
    EVERY tool name you mention in `did` MUST be wrapped in square
    brackets. Use the exact machine-readable tool name from the run's
    tool_call steps (e.g. `ai_ticketing_get_ticket_details`,
    `delegate_to_security_subagent`), NOT a friendly paraphrase. The
    brackets are required syntax — the UI renders them as clickable chips.

    GOOD example (markers present, tool names exact):
      "I pulled the ticket via [ai_ticketing_get_ticket_details], saw the
      EOL alert came from a Windows 2012 R2 host, then delegated to
      [delegate_to_security_subagent] which recommended an in-place
      upgrade. Wrote the categorization back with
      [ai_ticketing_update_ticket]."

    BAD example (markers MISSING — do not do this):
      "I pulled the ticket details, saw the alert came from a 2012 R2
      host, then asked the security sub-agent for guidance and updated
      the ticket."

    If no tools were called on this run, write `did` as plain prose with
    no brackets. Brackets are required ONLY when tools were actually used.

  - answered: one short sentence (<100 chars) capturing the agent's final
    answer or outcome — the user-facing result of the run. Different from
    `did`: `did` is the work, `answered` is the result.

  - confidence: float 0.0-1.0 — how confident the agent's output appears to
    be.
  - confidence_reason: one sentence explaining the confidence assessment.
  - metadata: object of k/v pairs (string -> string) extracting notable
    entities from the run — the specific decisions, IDs, customer names,
    categories, severity levels, billing status, and so on. Max 8 entries.

Return a single JSON object and nothing else. Do not wrap it in markdown code
fences. Do not add a preamble, trailing prose, or explanation. The first
character of your response must be `{` and the last must be `}`."""


def _extract_json_object(text: str) -> str:
    """Best-effort extraction of a JSON object from an LLM response.

    Tolerates the two common failure modes we see in practice:
      1. Markdown code fences (```json ... ```) that json.loads won't parse.
      2. A prose preamble / trailing text around the actual object.

    Returns a string that may still fail json.loads; caller handles that.
    """
    s = (text or "").strip()
    if not s:
        return s

    # Strip leading/trailing code fences. Handles ```json, ``` and variants.
    if s.startswith("```"):
        # Drop opening fence + optional language tag
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline + 1 :]
        # Drop closing fence
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].rstrip()

    # If there's still prose around the object, slice from first `{` to
    # matching `}`. Bracket-matching over quoted strings to avoid tripping
    # on `"url": "https://x.com/{id}"`.
    start = s.find("{")
    if start == -1:
        return s
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if esc:
            esc = False
            continue
        if in_str:
            if ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return s[start:]


def _clamp_confidence(value: Any) -> float | None:
    """Clamp an LLM-returned confidence to [0.0, 1.0], or return ``None`` if invalid."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, f))


async def _broadcast_run(run: AgentRun, db: AsyncSession) -> None:
    """Best-effort broadcast of a run's current state. Swallows errors.

    The summarizer mutates summary_status / asked / did on AgentRun in
    several phases; each commit is followed by a broadcast so both the
    detail and list UIs can react without polling.
    """
    try:
        agent_name = (
            await db.execute(
                select(Agent.name).where(Agent.id == run.agent_id)
            )
        ).scalar_one_or_none() or ""
        await publish_agent_run_update(run, agent_name)
    except Exception:
        logger.exception("Failed to broadcast run update for %s", run.id)


def _truncate(value: Any, max_len: int) -> str | None:
    """Coerce to non-empty truncated string, or ``None`` if blank/missing."""
    if value is None:
        return None
    s = str(value)[:max_len]
    return s or None


async def summarize_run(
    run_id: UUID, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Summarize a completed run. Idempotent on ``summary_status='completed'``.

    Skips runs that are not ``status='completed'`` (e.g. failed/cancelled),
    and runs that have already been summarized. Marks ``summary_status='failed'``
    on any LLM/parse error so the UI can surface a regenerate option.
    """
    # Phase 1: load + transition pending → generating, resolve LLM client
    async with session_factory() as db:
        run = (
            await db.execute(select(AgentRun).where(AgentRun.id == run_id))
        ).scalar_one_or_none()
        if run is None or run.status != "completed":
            return
        if run.summary_status == "completed":
            return  # idempotent

        run.summary_status = "generating"
        run.summary_error = None
        await db.commit()
        await _broadcast_run(run, db)

        # Resolve LLM client + model BEFORE leaving the session
        # (model_selection takes the AsyncSession).
        llm_client, resolved_model = await get_summarization_client(db)

        # Snapshot fields we need for the prompt outside the session.
        run_input = run.input
        run_output = run.output
        org_id = run.org_id
        agent_name = ""
        agent_system_prompt = ""
        agent_row = (
            await db.execute(
                select(Agent.name, Agent.system_prompt).where(
                    Agent.id == run.agent_id
                )
            )
        ).one_or_none()
        if agent_row is not None:
            agent_name = agent_row[0] or ""
            # Cap the system prompt to avoid bloating the summarizer's input
            # token budget on agents with long instructions. The summarizer
            # only needs enough context to distinguish outcome from role.
            agent_system_prompt = (agent_row[1] or "")[:2000]

    # Build the prompt with agent context so the summarizer can describe
    # *what changed on this run* instead of paraphrasing the agent's role.
    user_content = json.dumps(
        {
            "agent_name": agent_name,
            "agent_system_prompt": agent_system_prompt,
            "input": run_input,
            "output": run_output,
        },
        default=str,
    )
    messages = [
        LLMMessage(role="system", content=SUMMARIZE_SYSTEM_PROMPT),
        LLMMessage(role="user", content=user_content),
    ]

    # Phase 2: call LLM (no DB connection held)
    # We intentionally don't cap max_tokens here. Providers that support an
    # omitted limit use their model default; Anthropic receives the adapter's
    # required ceiling. A local cap had been causing silent mid-object
    # truncation for reasoning models that spend tokens on hidden thinking.
    response = None
    try:
        # Pydantic AI's native transport owns the single bounded request retry
        # budget. Do not add an outer loop here: stacked budgets amplify 429s.
        response = await llm_client.complete(
            messages=messages,
            model=resolved_model,
        )
        raw_content = response.content or ""
        # Empty content is its own class of failure — OpenAI / reasoning models
        # sometimes return "" when the response is filtered or when token
        # budget is consumed by hidden reasoning. Surface it explicitly so the
        # admin knows to check model/config rather than chasing a parser bug.
        if not raw_content.strip():
            async with session_factory() as db:
                run = (
                    await db.execute(select(AgentRun).where(AgentRun.id == run_id))
                ).scalar_one()
                run.summary_status = "failed"
                run.summary_error = (
                    "Summarization model returned empty content. "
                    "Check model output filtering / reasoning-token budget."
                )
                await db.commit()
                await _broadcast_run(run, db)
            logger.warning(
                "Summarizer returned empty content for run %s (model=%s)",
                run_id,
                resolved_model,
            )
            return
        parsed = json.loads(_extract_json_object(raw_content))
    except json.JSONDecodeError as exc:
        # Log the actual content (truncated) so we can diagnose future failures
        # — without this the docker logs only told us "invalid JSON" with no
        # hint whether the model returned prose, fences, or garbage.
        raw_preview = (response.content or "")[:500] if response else "<no response>"
        logger.warning(
            "Summarizer returned invalid JSON for run %s: %s | raw=%r",
            run_id,
            exc,
            raw_preview,
        )
        # Detect the "truncated mid-object" case so the error message is
        # distinguishable from a generic "invalid JSON" response.
        looks_truncated = (
            raw_preview
            and raw_preview != "<no response>"
            and raw_preview.lstrip().startswith("{")
            and not raw_preview.rstrip().endswith("}")
        )
        async with session_factory() as db:
            run = (
                await db.execute(select(AgentRun).where(AgentRun.id == run_id))
            ).scalar_one()
            run.summary_status = "failed"
            if looks_truncated:
                run.summary_error = (
                    "Summarization model response was truncated mid-object "
                    "(token budget exhausted). Retry or reduce run payload."
                )
            else:
                run.summary_error = (
                    f"Invalid JSON from summarization model: {str(exc)[:200]}"
                )
            await db.commit()
            await _broadcast_run(run, db)
        return
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Summarizer LLM call failed for run %s", run_id)
        async with session_factory() as db:
            run = (
                await db.execute(select(AgentRun).where(AgentRun.id == run_id))
            ).scalar_one()
            run.summary_status = "failed"
            run.summary_error = (
                f"LLM provider request failed ({type(exc).__name__}): "
                f"{str(exc)[:160]}"
            )
            await db.commit()
            await _broadcast_run(run, db)
        return

    if not isinstance(parsed, dict):
        async with session_factory() as db:
            run = (
                await db.execute(select(AgentRun).where(AgentRun.id == run_id))
            ).scalar_one()
            run.summary_status = "failed"
            run.summary_error = "Summarization model did not return a JSON object"
            await db.commit()
            await _broadcast_run(run, db)
        return

    # Phase 3: persist success + AIUsage row
    async with session_factory() as db:
        run = (
            await db.execute(select(AgentRun).where(AgentRun.id == run_id))
        ).scalar_one()
        run.asked = _truncate(parsed.get("asked"), 400)
        run.did = _truncate(parsed.get("did"), 1200)
        run.answered = _truncate(parsed.get("answered"), 400)
        run.confidence = _clamp_confidence(parsed.get("confidence"))
        run.confidence_reason = _truncate(parsed.get("confidence_reason"), 500)

        md = parsed.get("metadata") or {}
        if isinstance(md, dict):
            extracted = {
                str(k): str(v)[:256]
                for k, v in md.items()
                if isinstance(v, (str, int, float))
            }
            existing = run.run_metadata or {}
            # Existing (agent-supplied) wins; LLM fills in gaps.
            merged = {**extracted, **existing}
            run.run_metadata = dict(list(merged.items())[:16])

        run.summary_generated_at = datetime.now(timezone.utc)
        run.summary_status = "completed"
        run.summary_error = None
        run.summary_prompt_version = SUMMARIZE_PROMPT_VERSION

        provider = getattr(llm_client, "provider_name", "unknown")
        model_name = getattr(response, "model", None) or resolved_model
        await record_ai_usage(
            session=db,
            redis_client=await get_shared_redis(),
            agent_run_id=run.id,
            organization_id=org_id,
            provider=provider,
            model=model_name,
            input_tokens=response.input_tokens or 0,
            output_tokens=response.output_tokens or 0,
            cache_read_tokens=response.cache_read_tokens,
            cache_write_tokens=response.cache_write_tokens,
            provider_cost=response.provider_cost,
        )
        await db.commit()
        await _broadcast_run(run, db)


async def enqueue_summarize(run_id: UUID) -> None:
    """Publish a summarize message for the agent-summarization worker."""
    await publish_message(SUMMARIZE_QUEUE, {"run_id": str(run_id)})
