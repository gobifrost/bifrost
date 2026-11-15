"""Post-run summarization — populates asked/did/confidence/metadata on an AgentRun.

This module owns:

- :func:`summarize_run`: load the completed run, render the input/output, ask
  the configured summarization model, and persist the parsed result onto
  ``AgentRun`` (asked/did/confidence/run_metadata/summary_status).

Summaries are requested through :mod:`src.services.execution.summary_dispatch`.

Failure semantics: any error during the LLM call or JSON parsing is caught,
recorded on ``run.summary_error`` with ``summary_status='failed'``, and
swallowed. The handler in :mod:`src.jobs.summarize_worker` does the same
belt-and-suspenders so the message is never re-queued. The UI exposes a
regenerate button for recovery.
"""
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.pubsub import publish_agent_run_update
from src.core.cache import get_shared_redis
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.services.ai_behavior_service import AGENT_EXECUTION_MODE_METADATA_KEY
from src.services.ai_usage_service import persist_ai_usage_receipt, record_ai_usage
from src.services.agent_runtime.summary_compute_execution import (
    compute_summary, current_summary_compute_receipt, is_summary_compute_execution,
)
from src.services.execution.model_selection import get_summarization_client
from src.services.execution.run_metadata import MAX_KEYS
from src.services.llm import LLMMessage
from shared.agent_summary_compute import (
    SUMMARIZE_PROMPT_VERSION,
    SummaryCompletion,
    SummaryComputeInput,
    SummaryCompletionRequest,
)

logger = logging.getLogger(__name__)


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


async def summarize_run(
    run_id: UUID, session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]]
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

    # Phase 2: call the authorized model boundary (no DB connection held).
    # Pydantic AI's native transport owns the single bounded request retry
    # budget. Do not add an outer loop here: stacked budgets amplify 429s.
    async def complete(request: SummaryCompletionRequest) -> SummaryCompletion:
        try:
            response = await llm_client.complete(
                messages=[
                    LLMMessage(role=message.role, content=message.content)
                    for message in request.messages
                ],
                model=request.model,
            )
        except Exception:
            logger.exception("Summarizer LLM call failed for run %s", run_id)
            raise
        completion = SummaryCompletion(
            content=response.content,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_read_tokens=response.cache_read_tokens,
            cache_write_tokens=response.cache_write_tokens,
            reasoning_tokens=response.reasoning_tokens,
            provider_cost=(
                str(response.provider_cost)
                if response.provider_cost is not None
                else None
            ),
        )
        receipt = current_summary_compute_receipt()
        if receipt is not None:
            # This is consumed-provider evidence, independent of whether this
            # owner can still publish a summary. Commit before the compute ACK.
            await persist_ai_usage_receipt(
                receipt,
                agent_run_id=run_id, organization_id=org_id,
                provider=getattr(llm_client, "provider_name", "unknown"),
                model=completion.model or resolved_model,
                input_tokens=completion.input_tokens or 0,
                output_tokens=completion.output_tokens or 0,
                cache_read_tokens=completion.cache_read_tokens,
                cache_write_tokens=completion.cache_write_tokens,
                reasoning_tokens=completion.reasoning_tokens,
                provider_cost=Decimal(completion.provider_cost) if completion.provider_cost is not None else None,
            )
        return completion

    result = await compute_summary(
        SummaryComputeInput(
            agent_name=agent_name,
            agent_system_prompt=agent_system_prompt,
            run_input=run_input,
            run_output=run_output,
            model=resolved_model,
        ),
        complete,
    )
    if result.failure is not None:
        if result.failure.kind == "empty_content":
            logger.warning(
                "Summarizer returned empty content for run %s (model=%s)",
                run_id,
                resolved_model,
            )
        elif result.failure.kind == "invalid_json":
            logger.warning(
                "Summarizer returned invalid JSON for run %s: %s | raw=%r",
                run_id,
                result.failure.error,
                result.raw_content[:500],
            )
        async with session_factory() as db:
            run = (
                await db.execute(select(AgentRun).where(AgentRun.id == run_id))
            ).scalar_one()
            run.summary_status = "failed"
            run.summary_error = result.failure.error
            await db.commit()
            await _broadcast_run(run, db)
        return

    # Phase 3: persist success + AIUsage row
    assert result.fields is not None
    assert result.completion is not None
    async with session_factory() as db:
        run = (
            await db.execute(select(AgentRun).where(AgentRun.id == run_id))
        ).scalar_one()
        run.asked = result.fields.asked
        run.did = result.fields.did
        run.answered = result.fields.answered
        run.confidence = result.fields.confidence
        run.confidence_reason = result.fields.confidence_reason

        if result.fields.metadata is not None:
            extracted = {
                key: value
                for key, value in result.fields.metadata.items()
                if key != AGENT_EXECUTION_MODE_METADATA_KEY
            }
            existing = run.run_metadata or {}
            # Existing (agent-supplied) wins; LLM fills in gaps.
            persisted_mode = existing.get(AGENT_EXECUTION_MODE_METADATA_KEY)
            merged = {
                **extracted,
                **{
                    key: value
                    for key, value in existing.items()
                    if key != AGENT_EXECUTION_MODE_METADATA_KEY
                },
            }
            # Execution topology is selected by the platform before a run
            # begins. It is not summarizer metadata, so an LLM may neither
            # invent nor displace it, even when the metadata cap is full.
            limit = MAX_KEYS - (1 if persisted_mode in {"delegated", "automatic"} else 0)
            run.run_metadata = dict(list(merged.items())[:limit])
            if persisted_mode in {"delegated", "automatic"}:
                run.run_metadata[AGENT_EXECUTION_MODE_METADATA_KEY] = persisted_mode

        run.summary_generated_at = datetime.now(timezone.utc)
        run.summary_status = "completed"
        run.summary_error = None
        run.summary_prompt_version = SUMMARIZE_PROMPT_VERSION

        provider = getattr(llm_client, "provider_name", "unknown")
        model_name = result.completion.model or resolved_model
        if not is_summary_compute_execution():
            await record_ai_usage(
                session=db,
                redis_client=await get_shared_redis(),
                agent_run_id=run.id,
                organization_id=org_id,
                provider=provider,
                model=model_name,
                input_tokens=result.completion.input_tokens or 0,
                output_tokens=result.completion.output_tokens or 0,
                cache_read_tokens=result.completion.cache_read_tokens,
                cache_write_tokens=result.completion.cache_write_tokens,
                reasoning_tokens=result.completion.reasoning_tokens,
                provider_cost=(
                    Decimal(result.completion.provider_cost)
                    if result.completion.provider_cost is not None
                    else None
                ),
            )
        await db.commit()
        await _broadcast_run(run, db)
