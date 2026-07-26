"""Composes a full builder chat turn: message in, revision and reply out.

This is the seam between the three pieces that each know only their own job —
:mod:`src.services.builder.turns` owns the revision lifecycle and the write
lock, :mod:`src.services.builder.agent_runtime` owns the model loop and the
tools, and the ``Conversation``/``Message`` tables own what the user sees. This
service is what makes one user message drive all three.

Ordering is deliberate. The user's message is persisted *before* the model runs,
so a model or tool failure leaves a conversation that reads correctly — the
question is there, the turn row says it failed — rather than a silent hole where
the user's own words vanished along with the error. The runtime result is
captured out of the ``mutate`` closure because the turn service owns the
workspace's lifetime: by the time ``run_turn`` returns, the temporary directory
the agent worked in is already gone.

Concurrency is not this service's problem to solve: ``BuilderTurnConflict``
propagates untouched so the router can answer 409, which is the spec's rule that
contention is refused rather than queued.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import MessageRole
from src.models.orm.agents import Conversation, Message
from src.models.orm.solution_builder import SolutionBuilderSession, SolutionBuilderTurn
from src.models.orm.users import User
from src.services.builder.agent_runtime import (
    BUILDER_SYSTEM_PROMPT,
    InternalLoopRuntime,
    ToolCallRecord,
    TurnResult,
)
from src.services.builder.fs_tools import WorkspaceRoot
from src.services.builder.model_gateway import get_builder_llm_client
from src.services.builder.turns import BuilderProjectMissing, BuilderTurnService
from src.services.llm.base import BaseLLMClient

# Roles that belong in the model's view of the conversation. TOOL_CALL rows are
# presentation records for the UI — the model already saw those calls and their
# results inside the turn that made them, and they carry no content column.
_HISTORY_ROLES = (MessageRole.USER, MessageRole.ASSISTANT)


@dataclass
class AgentTurnOutcome:
    """What one agent turn produced, for the router to render."""

    turn: SolutionBuilderTurn
    final_text: str
    tool_call_count: int
    revision_created: bool


_SUMMARY_MAX_CHARS = 120


def _revision_summary(user_message: str) -> str:
    """One-line label for the revision a turn produces.

    The revision list is how a user finds the change they want to undo, so a
    revision with no summary is unusable there. The request is the most
    recognizable label available at write time — the model's own account of
    what it did lands in the assistant message, not here.
    """
    first_line = user_message.strip().splitlines()[0].strip() if user_message.strip() else ""
    if not first_line:
        return "builder turn"
    if len(first_line) <= _SUMMARY_MAX_CHARS:
        return first_line
    return first_line[: _SUMMARY_MAX_CHARS - 1].rstrip() + "…"


class BuilderAgentTurnService:
    """Runs one model-driven builder turn end to end."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.turns = BuilderTurnService(db)

    async def run_agent_turn(
        self,
        solution_id: UUID,
        *,
        session_id: UUID,
        requested_by: UUID | None,
        user_message: str,
    ) -> AgentTurnOutcome:
        """Persist ``user_message``, run the agent against the Solution, and reply.

        Raises :class:`~src.services.builder.model_gateway.BuilderModelUnavailable`
        when no LLM is configured, :class:`~src.services.builder.turns.BuilderTurnConflict`
        when another writer holds the Solution, and re-raises whatever failed the
        turn otherwise — with the user's message and the failed turn row already
        flushed.
        """
        session = await self._load_session(solution_id, session_id)
        conversation_id = session.conversation_id

        question = await self._append_message(
            conversation_id,
            role=MessageRole.USER,
            content=user_message,
        )

        client = await get_builder_llm_client(self.db)
        runtime = InternalLoopRuntime(client)
        history = await self._load_history(
            conversation_id, exclude_message_id=question.id
        )

        result: TurnResult | None = None

        async def mutate(workspace: WorkspaceRoot) -> None:
            nonlocal result
            result = await runtime.run_turn(
                BUILDER_SYSTEM_PROMPT,
                user_message,
                workspace,
                history=history,
            )

        started = time.monotonic()
        turn = await self.turns.run_turn(
            solution_id,
            session_id=session_id,
            requested_by=requested_by,
            mutate=mutate,
            summary=_revision_summary(user_message),
        )
        duration_ms = int((time.monotonic() - started) * 1000)

        if result is None:
            # run_turn returned without the mutation having run, which would mean
            # the lifecycle changed underneath this service.
            raise BuilderProjectMissing(
                f"builder turn {turn.id} completed without running the agent"
            )

        await self._append_tool_calls(conversation_id, result.tool_calls)

        assistant = await self._append_message(
            conversation_id,
            role=MessageRole.ASSISTANT,
            content=result.final_text,
            model=client.model_name,
            token_count_input=result.input_tokens,
            token_count_output=result.output_tokens,
            duration_ms=duration_ms,
        )

        await self._record_usage(
            client=client,
            result=result,
            conversation_id=conversation_id,
            message_id=assistant.id,
            user_id=requested_by,
            duration_ms=duration_ms,
        )

        return AgentTurnOutcome(
            turn=turn,
            final_text=result.final_text,
            tool_call_count=result.tool_call_count,
            revision_created=turn.output_revision_id != turn.base_revision_id,
        )

    async def _load_session(
        self, solution_id: UUID, session_id: UUID
    ) -> SolutionBuilderSession:
        session = await self.db.get(SolutionBuilderSession, session_id)
        if session is None or session.solution_id != solution_id:
            raise BuilderProjectMissing(
                f"builder session {session_id} does not belong to Solution {solution_id}"
            )
        return session

    async def _load_history(
        self, conversation_id: UUID, *, exclude_message_id: UUID
    ) -> list[dict[str, Any]]:
        """Prior user/assistant messages in conversation order.

        ``exclude_message_id`` is this turn's own user message, already persisted
        by the time this runs. The runtime appends the current user message
        itself, so including it here would show the model the same question
        twice.
        """
        rows = (
            (
                await self.db.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.role.in_(_HISTORY_ROLES),
                        Message.id != exclude_message_id,
                    )
                    .order_by(Message.sequence)
                )
            )
            .scalars()
            .all()
        )
        return [
            {"role": row.role.value, "content": row.content}
            for row in rows
            if row.content
        ]

    async def _append_tool_calls(
        self, conversation_id: UUID, records: list[ToolCallRecord]
    ) -> None:
        """Record each executed tool call the way the chat UI reads them.

        Two rows per call, matching ``agent_executor``: a ``TOOL_CALL`` row that
        the tool-progress panel renders (name, arguments, terminal state,
        result), and the companion ``TOOL`` row that keeps the transcript
        replayable. Both are written after the fact — the loop has already
        finished — so every state is terminal rather than ``running``.
        """
        for record in records:
            call_id = str(uuid4())
            await self._append_message(
                conversation_id,
                role=MessageRole.TOOL_CALL,
                content=None,
                tool_name=record.name,
                tool_call_id=call_id,
                tool_input=record.arguments,
                tool_state="completed" if record.ok else "error",
                tool_result={"output": record.result}
                if record.ok
                else {"error": record.result},
                duration_ms=record.duration_ms,
            )
            await self._append_message(
                conversation_id,
                role=MessageRole.TOOL,
                content=record.result,
                tool_name=record.name,
                tool_call_id=call_id,
            )

    async def _append_message(
        self,
        conversation_id: UUID,
        *,
        role: MessageRole,
        content: str | None,
        model: str | None = None,
        token_count_input: int | None = None,
        token_count_output: int | None = None,
        duration_ms: int | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        tool_input: dict[str, Any] | None = None,
        tool_state: str | None = None,
        tool_result: dict[str, Any] | None = None,
    ) -> Message:
        """Add one message at the end of the conversation and touch the parent."""
        next_sequence = await self._next_sequence(conversation_id)
        message = Message(
            id=uuid4(),
            conversation_id=conversation_id,
            role=role,
            content=content,
            model=model,
            token_count_input=token_count_input,
            token_count_output=token_count_output,
            duration_ms=duration_ms,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_input=tool_input,
            tool_state=tool_state,
            tool_result=tool_result,
            sequence=next_sequence,
        )
        self.db.add(message)
        await self.db.flush()
        return message

    async def _next_sequence(self, conversation_id: UUID) -> int:
        current = await self.db.scalar(
            select(func.coalesce(func.max(Message.sequence), 0)).where(
                Message.conversation_id == conversation_id
            )
        )
        return (current or 0) + 1

    async def _record_usage(
        self,
        *,
        client: BaseLLMClient,
        result: TurnResult,
        conversation_id: UUID,
        message_id: UUID,
        user_id: UUID | None,
        duration_ms: int,
    ) -> None:
        """Attribute the turn's tokens to the builder conversation.

        The Solution is reachable from the conversation through the builder
        session, so ``ai_usage`` needs no Solution column of its own.
        """
        # Imported here to keep the redis and pricing machinery out of this
        # module's import closure, matching how agent_executor reaches for them.
        from src.core.cache import get_shared_redis
        from src.services.ai_usage_service import record_ai_usage

        organization_id = await self.db.scalar(
            select(User.organization_id)
            .join(Conversation, Conversation.user_id == User.id)
            .where(Conversation.id == conversation_id)
        )
        redis_client = await get_shared_redis()
        await record_ai_usage(
            session=self.db,
            redis_client=redis_client,
            provider=client.provider_name,
            model=client.model_name,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            duration_ms=duration_ms,
            conversation_id=conversation_id,
            message_id=message_id,
            organization_id=organization_id,
            user_id=user_id,
        )
