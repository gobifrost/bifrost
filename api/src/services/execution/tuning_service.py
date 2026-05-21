"""Per-flag and consolidated tuning conversation service.

Owns the assistant-side of the multi-turn tuning chat that hangs off a
flagged ``AgentRun``: appends the user's turn, calls the configured tuning
model with the run + history context, and persists the assistant reply.

Also exposes the *consolidated tuning session* surface (Task 17): one LLM
call across all currently-flagged runs for an agent, dry-run that proposal
against each flagged run, and apply the proposal (updates
``Agent.system_prompt``, writes ``AgentPromptHistory``, clears verdicts on
the affected runs so they re-enter review under the new prompt).

This module exposes:

- :func:`get_or_create_conversation`: return the existing
  ``AgentRunFlagConversation`` for a run, or create an empty one.
- :func:`append_user_message_and_reply`: append a user turn, call the
  tuning LLM for a reply, persist both on the conversation's JSONB
  ``messages`` column, and record an ``AIUsage`` row for cost tracking.
- :func:`enqueue_tune_chat`: thin RabbitMQ publish helper used by the API
  router that accepts a new user message; the worker consumes the message
  and invokes :func:`append_user_message_and_reply`.
- :func:`propose_consolidated_tuning`: single LLM call producing one
  consolidated prompt proposal informed by all flagged runs.
- :func:`dry_run_consolidated`: per-run dry-run of a consolidated
  proposal; capped at the first 10 flagged runs to bound cost.
- :func:`apply_consolidated_tuning`: persist the new system prompt,
  write history, and clear verdicts on affected flagged runs.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.log_safety import log_safe
from src.jobs.rabbitmq import publish_message
from src.models.orm.agent_prompt_history import AgentPromptHistory
from src.models.orm.agent_run_flag_conversations import AgentRunFlagConversation
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.models.orm.ai_usage import AIUsage
from src.services.execution.dry_run import evaluate_against_prompt
from src.services.execution.model_selection import get_tuning_client
from src.services.llm import LLMMessage

logger = logging.getLogger(__name__)

TUNE_CHAT_QUEUE = "agent-tuning-chat"


FLAG_DIAGNOSE_SYSTEM = """You help users refine AI agent prompts. Given a flagged agent run (one that produced a wrong result), the user's note about what went wrong, and the conversation so far, respond naturally:
- Ask a clarifying question if the note is ambiguous
- Diagnose the likely cause by pointing to the prompt, tool choice, or missing knowledge
- When you have enough info, propose a specific, minimal prompt change (as a diff — add/keep/remove blocks)
Don't propose changes if the user hasn't confirmed the issue. Always be specific. Never apologize — the user wants action."""


async def get_or_create_conversation(
    run_id: UUID, db: AsyncSession
) -> AgentRunFlagConversation:
    """Return the existing flag conversation for ``run_id``, or create an empty one.

    Uses a flush (not commit) so the caller controls the transaction boundary.
    """
    conv = (
        await db.execute(
            select(AgentRunFlagConversation).where(
                AgentRunFlagConversation.run_id == run_id
            )
        )
    ).scalar_one_or_none()
    if conv is None:
        now = datetime.now(timezone.utc)
        conv = AgentRunFlagConversation(
            id=uuid4(),
            run_id=run_id,
            messages=[],
            created_at=now,
            last_updated_at=now,
        )
        db.add(conv)
        await db.flush()
    return conv


async def append_user_message_and_reply(
    run_id: UUID, content: str, db: AsyncSession
) -> AgentRunFlagConversation:
    """Append a user turn, call the tuning LLM for a reply, persist both + AIUsage.

    Returns the updated conversation. Caller is responsible for the outer
    transaction lifetime; this function commits at the end so the reply is
    durable even if the caller later rolls back.
    """
    run = (
        await db.execute(select(AgentRun).where(AgentRun.id == run_id))
    ).scalar_one()

    conv = await get_or_create_conversation(run_id, db)

    now = datetime.now(timezone.utc)
    # SQLAlchemy JSONB mutation: rebuild the list and reassign so the
    # dirty-state is tracked. In-place ``.append`` does not flag the
    # attribute as modified on JSONB columns unless MutableList is used.
    messages = list(conv.messages or [])
    messages.append(
        {
            "kind": "user",
            "content": content,
            "at": now.isoformat(),
        }
    )

    # Build the LLM prompt. Keep it simple: input/output + conversation history.
    prompt_payload = {
        "agent_run": {
            "input": run.input,
            "output": run.output,
        },
        "history": messages,
    }
    llm_messages = [
        LLMMessage(role="system", content=FLAG_DIAGNOSE_SYSTEM),
        LLMMessage(role="user", content=json.dumps(prompt_payload, default=str)),
    ]

    llm_client, resolved_model = await get_tuning_client(db)
    response = await llm_client.complete(
        messages=llm_messages, model=resolved_model, max_tokens=1500
    )

    messages.append(
        {
            "kind": "assistant",
            "content": response.content or "",
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )

    conv.messages = messages
    conv.last_updated_at = datetime.now(timezone.utc)

    provider = getattr(llm_client, "provider_name", "unknown")
    model_name = getattr(response, "model", None) or resolved_model
    db.add(
        AIUsage(
            agent_run_id=run.id,
            organization_id=run.org_id,
            provider=provider,
            model=model_name,
            input_tokens=getattr(response, "input_tokens", 0) or 0,
            output_tokens=getattr(response, "output_tokens", 0) or 0,
            cost=None,
            timestamp=datetime.now(timezone.utc),
        )
    )
    await db.commit()
    await db.refresh(conv)
    return conv


async def enqueue_tune_chat(run_id: UUID, content: str) -> None:
    """Publish a tune-chat message for the agent-tuning-chat worker."""
    await publish_message(
        TUNE_CHAT_QUEUE, {"run_id": str(run_id), "content": content}
    )


# ---------------------------------------------------------------------------
# Consolidated tuning session (Task 17)
# ---------------------------------------------------------------------------

CONSOLIDATED_DRY_RUN_LIMIT = 10

CONSOLIDATED_PROPOSAL_SYSTEM_PROMPT = """You analyze a batch of flagged AI agent runs and propose a single consolidated system prompt change that addresses the patterns you see.

You will receive:
  - The agent's current system prompt
  - A list of flagged runs (each with its input, output, and the user's tuning conversation about what went wrong)

Produce a single, minimal-diff revision of the system prompt that addresses the recurring issues. Prefer adding a small, specific instruction over rewriting from scratch.

Return ONLY a JSON object:
{
  "summary": "<one short paragraph describing the recurring issue and the change you're proposing>",
  "proposed_prompt": "<the full revised system prompt>"
}"""


@dataclass
class ConsolidatedProposal:
    """In-memory result of :func:`propose_consolidated_tuning`."""

    summary: str
    proposed_prompt: str
    affected_run_ids: list[UUID]


@dataclass
class AppliedTuning:
    """In-memory result of :func:`apply_consolidated_tuning`."""

    agent_id: UUID
    history_id: UUID
    affected_run_ids: list[UUID]


async def _load_flagged_runs_with_conversations(
    agent_id: UUID,
    db: AsyncSession,
    *,
    org_id: UUID | None = None,
    restrict_to_org: bool = False,
) -> list[tuple[AgentRun, AgentRunFlagConversation | None]]:
    """Load all completed thumbs-down runs for ``agent_id`` and their conversations."""
    stmt = (
        select(AgentRun)
        .where(AgentRun.agent_id == agent_id)
        .where(AgentRun.verdict == "down")
        .where(AgentRun.status == "completed")
        .order_by(AgentRun.created_at)
    )
    if restrict_to_org:
        if org_id is None:
            stmt = stmt.where(AgentRun.org_id.is_(None))
        else:
            stmt = stmt.where(AgentRun.org_id == org_id)

    runs = (
        (
            await db.execute(stmt)
        )
        .scalars()
        .all()
    )

    if not runs:
        return []

    convs = (
        (
            await db.execute(
                select(AgentRunFlagConversation).where(
                    AgentRunFlagConversation.run_id.in_([r.id for r in runs])
                )
            )
        )
        .scalars()
        .all()
    )
    by_run = {c.run_id: c for c in convs}
    return [(r, by_run.get(r.id)) for r in runs]


async def propose_consolidated_tuning(
    agent_id: UUID,
    db: AsyncSession,
    *,
    org_id: UUID | None = None,
    restrict_to_org: bool = False,
) -> ConsolidatedProposal:
    """Single LLM call across all flagged runs; returns one consolidated proposal.

    Raises ``LookupError`` if there are no flagged runs (caller maps to 404).
    """
    agent = (
        await db.execute(select(Agent).where(Agent.id == agent_id))
    ).scalar_one_or_none()
    if agent is None:
        raise LookupError(f"Agent {agent_id} not found")

    pairs = await _load_flagged_runs_with_conversations(
        agent_id,
        db,
        org_id=org_id,
        restrict_to_org=restrict_to_org,
    )
    if not pairs:
        raise LookupError(
            f"Agent {agent_id} has no flagged (thumbs-down) runs to tune"
        )

    flagged_payload = []
    for run, conv in pairs:
        flagged_payload.append(
            {
                "run_id": str(run.id),
                "input": run.input,
                "output": run.output,
                "verdict_note": run.verdict_note,
                "conversation": list(conv.messages) if conv else [],
            }
        )

    payload = {
        "current_system_prompt": agent.system_prompt,
        "flagged_runs": flagged_payload,
    }

    llm_client, resolved_model = await get_tuning_client(db)
    messages = [
        LLMMessage(role="system", content=CONSOLIDATED_PROPOSAL_SYSTEM_PROMPT),
        LLMMessage(role="user", content=json.dumps(payload, default=str)),
    ]
    response = await llm_client.complete(
        messages=messages, model=resolved_model, max_tokens=4000
    )

    try:
        parsed = json.loads(response.content or "")
    except json.JSONDecodeError:
        logger.warning(
            "Consolidated tuning model returned invalid JSON for agent %s",
            log_safe(agent_id),
        )
        parsed = {}

    if not isinstance(parsed, dict):
        parsed = {}

    summary = str(parsed.get("summary") or "").strip() or (
        "No summary provided by the tuning model."
    )
    proposed_prompt = str(parsed.get("proposed_prompt") or "").strip()
    if not proposed_prompt:
        # Fall back to the current prompt so the response is always valid;
        # the UI can still show the (empty) summary and the user can
        # decide not to apply.
        proposed_prompt = agent.system_prompt

    return ConsolidatedProposal(
        summary=summary[:2000],
        proposed_prompt=proposed_prompt,
        affected_run_ids=[r.id for r, _ in pairs],
    )


async def dry_run_consolidated(
    agent_id: UUID,
    proposed_prompt: str,
    db: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    org_id: UUID | None = None,
    restrict_to_org: bool = False,
) -> list[tuple[UUID, bool, str, float]]:
    """Run :func:`evaluate_against_prompt` for each flagged run (capped).

    Returns a list of ``(run_id, would_still_decide_same, reasoning, confidence)``
    tuples, capped at :data:`CONSOLIDATED_DRY_RUN_LIMIT` runs to bound cost.
    """
    pairs = await _load_flagged_runs_with_conversations(
        agent_id,
        db,
        org_id=org_id,
        restrict_to_org=restrict_to_org,
    )
    capped = pairs[:CONSOLIDATED_DRY_RUN_LIMIT]
    results: list[tuple[UUID, bool, str, float]] = []
    for run, _conv in capped:
        verdict = await evaluate_against_prompt(
            run_id=run.id,
            proposed_prompt=proposed_prompt,
            session_factory=session_factory,
        )
        results.append(
            (
                run.id,
                verdict.would_still_decide_same,
                verdict.reasoning,
                verdict.confidence,
            )
        )
    return results


async def apply_consolidated_tuning(
    agent_id: UUID,
    new_prompt: str,
    reason: str | None,
    user_id: UUID | None,
    db: AsyncSession,
    *,
    org_id: UUID | None = None,
    restrict_to_org: bool = False,
) -> AppliedTuning:
    """Apply a consolidated tuning proposal.

    Updates ``agent.system_prompt``, inserts an ``AgentPromptHistory`` row,
    and clears ``verdict``/``verdict_note`` on the flagged runs so they
    re-enter the unreviewed queue under the new prompt. Caller commits.
    """
    agent = (
        await db.execute(select(Agent).where(Agent.id == agent_id))
    ).scalar_one_or_none()
    if agent is None:
        raise LookupError(f"Agent {agent_id} not found")

    pairs = await _load_flagged_runs_with_conversations(
        agent_id,
        db,
        org_id=org_id,
        restrict_to_org=restrict_to_org,
    )
    affected_ids = [r.id for r, _ in pairs]

    previous_prompt = agent.system_prompt
    now = datetime.now(timezone.utc)

    history = AgentPromptHistory(
        id=uuid4(),
        agent_id=agent.id,
        previous_prompt=previous_prompt,
        new_prompt=new_prompt,
        changed_by=user_id,
        changed_at=now,
        reason=reason,
    )
    db.add(history)

    agent.system_prompt = new_prompt
    agent.updated_at = now

    # Clear verdict on affected runs so they re-enter the review queue.
    for run, _conv in pairs:
        run.verdict = None
        run.verdict_note = None
        run.verdict_set_at = now
        run.verdict_set_by = user_id

    await db.flush()

    return AppliedTuning(
        agent_id=agent.id,
        history_id=history.id,
        affected_run_ids=affected_ids,
    )
