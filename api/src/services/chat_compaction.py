"""Lossless chat compaction (Chat V2 M5, §4 of the chat UX design spec).

Compaction summarizes *older turns in the model's working context only*. The
database is the source of truth and is **never modified** — every original
message stays in the conversation scrollback. A compaction checkpoint is
persisted on the ``Conversation`` (summary text + the sequence boundary it
covers) so it survives between turns and the manual "Compact older turns"
button has lasting effect.

Two entry points share the same machinery:

- :func:`maybe_auto_compact` — called from the chat turn when the working
  context crosses ``COMPACTION_THRESHOLD_FRACTION`` of the model's context
  window (§4.2). Advances the checkpoint and returns a result the executor
  turns into ``compaction_started`` / ``compaction_complete`` chunks.
- :func:`compact_now` — backs the manual ``POST .../compact`` endpoint (§4.3).
  Runs the same summarization immediately, regardless of budget.

Tool-output protection (§4.4): the most recent tool outputs (within
``TOOL_OUTPUT_PROTECT_TOKENS``) are always kept verbatim; only turns strictly
older than the protected window are eligible to be folded into the summary.

Cost accounting: the summarizer call is a small auxiliary LLM call. Its token
usage is recorded as an ``AIUsage`` row tied to the conversation, mirroring the
agent-run summarizer (``execution/run_summarizer.py``), so compaction cost
rolls into the same dashboards.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agents import Conversation, Message
from src.models.orm.ai_usage import AIUsage
from src.services.execution.model_selection import get_summarization_client
from src.services.llm import LLMMessage

logger = logging.getLogger(__name__)

# §4.2: auto-compact when working context exceeds this fraction of the model's
# context window. Per-model: the window comes from the model resolver / platform
# registry, not a hardcoded token count.
COMPACTION_THRESHOLD_FRACTION = 0.85

# §4.4: keep this many tokens of the most recent tool outputs verbatim. Turns
# older than the protected window are eligible for folding into the summary.
TOOL_OUTPUT_PROTECT_TOKENS = 10_000

# Always keep at least this many of the most recent messages verbatim, so the
# model never loses the immediate conversational thread to a summary.
KEEP_RECENT_MESSAGES = 8

# Used when the model's context window is unknown (uncached model): fall back to
# a conservative window so auto-compaction still has a sane trigger point.
FALLBACK_CONTEXT_WINDOW = 200_000

_SUMMARY_HEADER = "[Conversation history summary]"

_SUMMARIZE_SYSTEM_PROMPT = (
    "You are compacting the earlier part of a chat conversation so it fits in "
    "the model's context window without losing meaning. Summarize the messages "
    "below concisely. Preserve: key facts the user provided, decisions made, "
    "open questions, and the names/IDs of any entities (tickets, files, tools "
    "called and their outcomes). Write in past tense, third person, as a dense "
    "briefing the assistant can rely on to continue the conversation. Do not "
    "address the user. Keep it under 1000 words."
)


@dataclass(frozen=True)
class CompactionResult:
    """Outcome of a compaction attempt."""

    compacted: bool
    turns_compacted: int
    tokens_before: int
    tokens_after: int
    summary: str | None = None
    message: str = ""


def estimate_tokens(messages: list[LLMMessage]) -> int:
    """Estimate token count for a list of LLM messages (~4 chars/token).

    Conservative heuristic matching ``AgentExecutor._estimate_tokens`` so the
    threshold math here lines up with what the executor reports.
    """
    import json

    total = 0
    for msg in messages:
        if msg.content:
            total += len(msg.content) // 4
        if msg.tool_calls:
            tool_json = json.dumps(
                [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in msg.tool_calls
                ]
            )
            total += len(tool_json) // 4
    return total


def compaction_threshold_tokens(context_window: int | None) -> int:
    """Return the auto-compaction trigger point in tokens (§4.2).

    ``0.85 * context_window``. Falls back to a conservative window when the
    model's context window is unknown.
    """
    window = context_window if context_window and context_window > 0 else FALLBACK_CONTEXT_WINDOW
    return int(window * COMPACTION_THRESHOLD_FRACTION)


def _message_tokens(msg: Message) -> int:
    """Estimate the token weight of a single DB message row (~4 chars/token)."""
    import json

    total = 0
    if msg.content:
        total += len(msg.content) // 4
    if msg.tool_calls:
        total += len(json.dumps(msg.tool_calls)) // 4
    if msg.tool_input:
        total += len(json.dumps(msg.tool_input)) // 4
    return total


def _find_turn_starts(messages: list[Message]) -> list[int]:
    """Indices where a new turn begins (a user message starts a turn).

    Cutting at a user-message boundary keeps assistant/tool_use/tool_result
    groups intact, so we never strand a tool_result without its tool_use.
    """
    from src.models.enums import MessageRole

    starts: list[int] = []
    for i, m in enumerate(messages):
        if m.role == MessageRole.USER:
            starts.append(i)
    return starts


def compute_fold_boundary(
    branch: list[Message],
    *,
    already_through: int | None,
) -> tuple[int, list[Message]] | None:
    """Decide which messages to fold into the summary.

    Returns ``(through_sequence, folded_messages)`` or ``None`` if there is
    nothing new worth folding (too few older turns, or everything older is
    already covered by the existing checkpoint).

    ``branch`` is the full chronological active branch. ``already_through`` is
    the conversation's current ``compaction_through_sequence`` (None if never
    compacted).

    The cut point is the latest user-message turn boundary that leaves at least
    ``KEEP_RECENT_MESSAGES`` recent messages verbatim AND keeps the most recent
    ``TOOL_OUTPUT_PROTECT_TOKENS`` of tool output out of the fold (§4.4).
    """
    from src.models.enums import MessageRole

    if len(branch) <= KEEP_RECENT_MESSAGES:
        return None

    # Protected tail (§4.4): walk backwards accumulating tool-output tokens.
    # The most recent tool outputs are kept verbatim up to the protect budget;
    # the first (oldest) tool message that would push us over the budget marks
    # the start of the protected window. Messages with no large recent tool
    # output protect nothing here — that case is governed by keep_floor below.
    protected_start = len(branch)
    protected_tool_tokens = 0
    for i in range(len(branch) - 1, -1, -1):
        m = branch[i]
        if m.role == MessageRole.TOOL and m.content:
            protected_tool_tokens += len(m.content) // 4
            if protected_tool_tokens > TOOL_OUTPUT_PROTECT_TOKENS:
                # Protect from this tool message's turn onward.
                protected_start = i
                break

    # Also never fold the last KEEP_RECENT_MESSAGES.
    keep_floor = len(branch) - KEEP_RECENT_MESSAGES
    max_fold_exclusive = min(protected_start, keep_floor)
    if max_fold_exclusive <= 0:
        return None

    # Snap to the latest user-turn boundary at or before max_fold_exclusive.
    turn_starts = _find_turn_starts(branch)
    cut_idx = 0
    for s in turn_starts:
        if s <= max_fold_exclusive:
            cut_idx = s
        else:
            break

    if cut_idx <= 0:
        return None

    folded = branch[:cut_idx]
    if not folded:
        return None

    through_sequence = folded[-1].sequence

    # Nothing new to do if the existing checkpoint already covers this span.
    if already_through is not None and through_sequence <= already_through:
        return None

    return through_sequence, folded


def render_messages_for_summary(messages: list[Message]) -> str:
    """Flatten DB messages into a plain-text transcript for the summarizer."""
    import json

    from src.models.enums import MessageRole

    parts: list[str] = []
    for m in messages:
        if m.role == MessageRole.USER and m.content:
            parts.append(f"USER: {m.content}")
        elif m.role == MessageRole.ASSISTANT:
            if m.content:
                parts.append(f"ASSISTANT: {m.content}")
            if m.tool_calls:
                for tc in m.tool_calls:
                    parts.append(
                        f"TOOL_CALL: {tc.get('name')}({json.dumps(tc.get('arguments', {}))})"
                    )
        elif m.role == MessageRole.TOOL_CALL:
            parts.append(
                f"TOOL_CALL: {m.tool_name}({json.dumps(m.tool_input or {})})"
            )
        elif m.role == MessageRole.TOOL and m.content:
            parts.append(f"TOOL_RESULT ({m.tool_name}): {m.content}")
    return "\n\n".join(parts)


async def _summarize(
    db: AsyncSession,
    folded: list[Message],
) -> tuple[str, str, str, int, int]:
    """Run the summarizer LLM call.

    Reuses the agent-run summarizer's model selection (``get_summarization_client``)
    so compaction picks the org's configured cheap/fast tier (§4.1).

    Returns ``(summary_text, provider, model, input_tokens, output_tokens)``.
    """
    llm_client, resolved_model = await get_summarization_client(db)
    transcript = render_messages_for_summary(folded)
    messages = [
        LLMMessage(role="system", content=_SUMMARIZE_SYSTEM_PROMPT),
        LLMMessage(role="user", content=transcript),
    ]
    response = await llm_client.complete(messages=messages, model=resolved_model)
    summary = (response.content or "").strip()
    provider = getattr(llm_client, "provider_name", "unknown")
    model_name = getattr(response, "model", None) or resolved_model
    input_tokens = getattr(response, "input_tokens", 0) or 0
    output_tokens = getattr(response, "output_tokens", 0) or 0
    return summary, provider, model_name, input_tokens, output_tokens


def _record_usage(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    organization_id: UUID | None,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Add an ``AIUsage`` row for a compaction summarizer call.

    Mirrors ``run_summarizer``: cost is left None (priced downstream), tied to
    the conversation so it rolls into the org's chat spend.
    """
    db.add(
        AIUsage(
            conversation_id=conversation_id,
            organization_id=organization_id,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=None,
            timestamp=datetime.now(timezone.utc),
        )
    )


async def _run_compaction(
    db: AsyncSession,
    conversation: Conversation,
    branch: list[Message],
    *,
    organization_id: UUID | None,
) -> CompactionResult:
    """Core: fold older turns into a (possibly extended) summary checkpoint.

    Loads the conversation FOR UPDATE-style via the passed session, generates a
    summary covering ``[existing summary] + newly-folded turns``, and persists
    the advanced checkpoint. The DB messages themselves are untouched.
    """
    boundary = compute_fold_boundary(
        branch, already_through=conversation.compaction_through_sequence
    )
    if boundary is None:
        return CompactionResult(
            compacted=False,
            turns_compacted=0,
            tokens_before=0,
            tokens_after=0,
            message="Nothing to compact yet — not enough older turns.",
        )

    through_sequence, folded = boundary

    # When a prior checkpoint exists, only the *newly* folded messages (those
    # past the old boundary) need summarizing; we prepend the prior summary so
    # the new summary subsumes it.
    prior = conversation.compaction_through_sequence
    new_messages = (
        [m for m in folded if m.sequence > prior] if prior is not None else folded
    )

    tokens_before = sum(_message_tokens(m) for m in folded)

    to_summarize: list[Message] = new_messages
    summary, provider, model, in_tok, out_tok = await _summarize(db, to_summarize)

    if not summary:
        logger.warning(
            "Compaction summarizer returned empty content for conversation %s",
            conversation.id,
        )
        return CompactionResult(
            compacted=False,
            turns_compacted=0,
            tokens_before=tokens_before,
            tokens_after=tokens_before,
            message="Compaction unavailable — summarizer returned no content.",
        )

    if prior is not None and conversation.compaction_summary:
        merged_summary = (
            f"{conversation.compaction_summary.rstrip()}\n\n{summary}"
        )
    else:
        merged_summary = summary

    new_turns = len(_find_turn_starts(new_messages))
    total_original = (conversation.compaction_original_tokens or 0) + sum(
        _message_tokens(m) for m in new_messages
    )

    conversation.compaction_summary = merged_summary
    conversation.compaction_through_sequence = through_sequence
    conversation.compaction_original_tokens = total_original
    conversation.updated_at = datetime.now(timezone.utc)

    _record_usage(
        db,
        conversation_id=conversation.id,
        organization_id=organization_id,
        provider=provider,
        model=model,
        input_tokens=in_tok,
        output_tokens=out_tok,
    )

    tokens_after = len(merged_summary) // 4
    return CompactionResult(
        compacted=True,
        turns_compacted=new_turns,
        tokens_before=total_original,
        tokens_after=tokens_after,
        summary=merged_summary,
        message=(
            f"Compacted {new_turns} earlier "
            f"{'turn' if new_turns == 1 else 'turns'} into a summary."
        ),
    )


async def compact_now(
    db: AsyncSession,
    conversation_id: UUID,
    *,
    organization_id: UUID | None,
) -> CompactionResult:
    """Manual compaction (§4.3) — run the summarization immediately.

    Loads the active branch, folds eligible older turns, persists the checkpoint.
    Caller is responsible for committing the session.
    """
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        raise ValueError(f"conversation {conversation_id} not found")
    branch = await _load_active_branch(db, conversation)
    return await _run_compaction(
        db, conversation, branch, organization_id=organization_id
    )


async def maybe_auto_compact(
    session_factory,
    conversation_id: UUID,
    *,
    organization_id: UUID | None,
) -> CompactionResult:
    """Auto compaction (§4.2) — fold eligible older turns, commit the checkpoint.

    Called from the chat turn once the executor has determined the working
    context is over the per-model threshold. Runs in its own short-lived
    session and commits, so the rebuilt history (loaded by the executor right
    after) sees the persisted checkpoint.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    sf: async_sessionmaker[AsyncSession] = session_factory
    async with sf() as db:
        conversation = await db.get(Conversation, conversation_id)
        if conversation is None:
            return CompactionResult(
                compacted=False,
                turns_compacted=0,
                tokens_before=0,
                tokens_after=0,
                message="Conversation not found.",
            )
        branch = await _load_active_branch(db, conversation)
        result = await _run_compaction(
            db, conversation, branch, organization_id=organization_id
        )
        if result.compacted:
            await db.commit()
        return result


async def _load_active_branch(
    db: AsyncSession, conversation: Conversation
) -> list[Message]:
    """Chronological active branch (parent-chain walk from the active leaf).

    Mirrors ``AgentExecutor._load_active_branch`` but takes an explicit session
    so the compaction service can run inside the endpoint's request session.
    """
    leaf_id = conversation.active_leaf_message_id
    if leaf_id is None:
        fallback = (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.sequence.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if fallback is None:
            return []
        leaf_id = fallback.id

    chain: list[Message] = []
    current_id: UUID | None = leaf_id
    seen: set[UUID] = set()
    while current_id is not None:
        if current_id in seen:
            break
        seen.add(current_id)
        msg = await db.get(Message, current_id)
        if msg is None:
            break
        chain.append(msg)
        current_id = msg.parent_message_id
    chain.reverse()
    return chain


def build_summary_block(conversation: Conversation) -> LLMMessage | None:
    """Render the conversation's compaction checkpoint as a working-context block.

    Returns the ``[Conversation history summary]`` ``LLMMessage`` to inject after
    the system prompt, or None when there's no checkpoint. The block is a user
    message (the same shape the old prune path used) so it reads as prior
    context rather than an instruction.
    """
    if not conversation.compaction_summary or conversation.compaction_through_sequence is None:
        return None
    orig = conversation.compaction_original_tokens
    note = f" (~{orig:,} tokens originally)" if orig else ""
    return LLMMessage(
        role="user",
        content=f"{_SUMMARY_HEADER}{note}:\n{conversation.compaction_summary}",
    )
