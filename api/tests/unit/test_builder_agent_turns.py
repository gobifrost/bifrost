"""Unit tests for the composed builder agent turn.

The model is a scripted fake (the style established in
``test_builder_agent_runtime.py``) and the revision store and write lock are
faked the same way ``test_builder_turns.py`` fakes them, so what is under test
here is purely the composition: what gets persisted, in what order, and what
survives a failure.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import MessageRole
from src.models.orm.agents import Conversation, Message
from src.models.orm.ai_usage import AIUsage
from src.models.orm.solution_builder import (
    SolutionBuilderSession,
    SolutionBuilderTurn,
    SolutionSourceRevision,
)
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.services.builder import agent_turns as agent_turns_module
from src.services.builder import turns as turns_module
from src.services.builder.agent_turns import BuilderAgentTurnService
from src.services.builder.turns import (
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    BuilderTurnConflict,
    BuilderTurnService,
)
from src.services.llm.base import (
    BaseLLMClient,
    LLMConfig,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    ToolCallRequest,
    ToolDefinition,
)
from src.services.solutions.write_lock import SolutionWriteLockHeld


class ScriptedLLMClient(BaseLLMClient):
    """Returns pre-set responses in order, recording what it was asked."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__(
            LLMConfig(provider="openai", model="test-model", api_key="unused")
        )
        self._responses = list(responses)
        self.calls: list[list[LLMMessage]] = []

    def script(self, responses: list[LLMResponse]) -> None:
        """Replace the remaining responses; one is consumed per ``complete``."""
        self._responses = list(responses)

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        if not self._responses:
            raise AssertionError("scripted client ran out of responses")
        return self._responses.pop(0)

    def stream(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        raise NotImplementedError("the builder loop is non-streaming")

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self.config.model


class ExplodingLLMClient(ScriptedLLMClient):
    """Fails the way a provider outage does: mid-``complete``."""

    def __init__(self) -> None:
        super().__init__([])

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        raise RuntimeError("model provider is down")


# ── fakes ───────────────────────────────────────────────────────────────────


class FakeRevisionStorage:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs

    async def write_from_path(self, revision_id: UUID | str, path: Path) -> None:
        self.blobs[str(revision_id)] = path.read_bytes()

    async def copy_to_path(self, revision_id: UUID | str, dest: Path) -> bool:
        blob = self.blobs.get(str(revision_id))
        if blob is None:
            return False
        dest.write_bytes(blob)
        return True


@contextlib.asynccontextmanager
async def _null_lock(solution_id: UUID) -> AsyncIterator[None]:
    yield


@contextlib.asynccontextmanager
async def _held_lock(solution_id: UUID) -> AsyncIterator[None]:
    raise SolutionWriteLockHeld(str(solution_id))
    yield  # pragma: no cover - unreachable, satisfies the generator contract


@pytest.fixture
def blobs() -> dict[str, bytes]:
    return {}


@pytest.fixture
def client() -> ScriptedLLMClient:
    """Default script: one reply, no tool calls."""
    return ScriptedLLMClient(
        [LLMResponse(content="Nothing to change.", input_tokens=11, output_tokens=3)]
    )


@pytest.fixture(autouse=True)
def fake_infrastructure(
    monkeypatch: pytest.MonkeyPatch,
    blobs: dict[str, bytes],
    client: ScriptedLLMClient,
) -> None:
    monkeypatch.setattr(turns_module, "solution_write_lock", _null_lock)
    monkeypatch.setattr(
        BuilderTurnService,
        "_storage",
        lambda self, solution_id: FakeRevisionStorage(blobs),
    )

    async def _client(db: AsyncSession) -> BaseLLMClient:
        return client

    monkeypatch.setattr(agent_turns_module, "get_builder_llm_client", _client)


@pytest_asyncio.fixture
async def solution(db_session: AsyncSession) -> Solution:
    row = Solution(
        id=uuid4(), slug=f"builder-{uuid4().hex[:8]}", name="Builder", organization_id=None
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest_asyncio.fixture
async def session_row(
    db_session: AsyncSession, solution: Solution
) -> SolutionBuilderSession:
    # is_superuser satisfies ck_users_org_requires_superuser without an
    # Organization row, matching test_builder_turns.py.
    user = User(
        id=uuid4(),
        email=f"builder-{uuid4().hex[:8]}@example.com",
        is_superuser=True,
    )
    db_session.add(user)
    conversation = Conversation(id=uuid4(), user_id=user.id)
    db_session.add(conversation)
    await db_session.flush()

    row = SolutionBuilderSession(
        id=uuid4(),
        solution_id=solution.id,
        conversation_id=conversation.id,
        user_id=user.id,
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest_asyncio.fixture
async def service(
    db_session: AsyncSession, solution: Solution
) -> BuilderAgentTurnService:
    await BuilderTurnService(db_session).create_project(
        solution.id,
        slug="my-sol",
        name="My Solution",
        conversation_id=None,
        user_id=None,
    )
    return BuilderAgentTurnService(db_session)


async def _messages(db: AsyncSession, conversation_id: UUID) -> list[Message]:
    rows = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.sequence)
    )
    return list(rows.scalars().all())


# ── happy path ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_turn_persists_both_messages_and_reports_the_reply(
    service: BuilderAgentTurnService,
    db_session: AsyncSession,
    solution: Solution,
    session_row: SolutionBuilderSession,
):
    outcome = await service.run_agent_turn(
        solution.id,
        session_id=session_row.id,
        requested_by=session_row.user_id,
        user_message="Leave it alone.",
    )

    assert outcome.final_text == "Nothing to change."
    assert outcome.turn.status == STATUS_SUCCEEDED

    messages = await _messages(db_session, session_row.conversation_id)
    assert [(m.role, m.content) for m in messages] == [
        (MessageRole.USER, "Leave it alone."),
        (MessageRole.ASSISTANT, "Nothing to change."),
    ]
    assert [m.sequence for m in messages] == [1, 2]
    assert messages[1].model == "test-model"
    assert messages[1].token_count_input == 11
    assert messages[1].token_count_output == 3


@pytest.mark.asyncio
async def test_a_writing_turn_creates_a_revision(
    db_session: AsyncSession,
    service: BuilderAgentTurnService,
    solution: Solution,
    session_row: SolutionBuilderSession,
    client: ScriptedLLMClient,
):
    """A tool call that changes content advances the revision pointer."""
    client.script([
        LLMResponse(
            tool_calls=[
                ToolCallRequest(
                    id="call-1",
                    name="write_file",
                    arguments={"path": "workflows/new.py", "content": "x = 1\n"},
                )
            ],
            input_tokens=40,
            output_tokens=9,
        ),
        LLMResponse(content="Added workflows/new.py.", input_tokens=60, output_tokens=7),
    ])

    outcome = await service.run_agent_turn(
        solution.id,
        session_id=session_row.id,
        requested_by=session_row.user_id,
        user_message="Add a workflow.",
    )

    assert outcome.revision_created is True
    assert outcome.tool_call_count == 1
    assert outcome.turn.output_revision_id != outcome.turn.base_revision_id

    revisions = await db_session.execute(
        select(SolutionSourceRevision).where(
            SolutionSourceRevision.solution_id == solution.id
        )
    )
    assert len(list(revisions.scalars().all())) == 2


@pytest.mark.asyncio
async def test_a_no_op_turn_creates_no_revision(
    service: BuilderAgentTurnService,
    solution: Solution,
    session_row: SolutionBuilderSession,
):
    outcome = await service.run_agent_turn(
        solution.id,
        session_id=session_row.id,
        requested_by=session_row.user_id,
        user_message="Just checking in.",
    )

    assert outcome.revision_created is False
    assert outcome.turn.output_revision_id == outcome.turn.base_revision_id


@pytest.mark.asyncio
async def test_turn_writes_ai_usage_with_the_runtime_token_counts(
    db_session: AsyncSession,
    service: BuilderAgentTurnService,
    solution: Solution,
    session_row: SolutionBuilderSession,
    client: ScriptedLLMClient,
):
    """Tokens are summed across every model call the turn made, not just the last."""
    client.script([
        LLMResponse(
            tool_calls=[ToolCallRequest(id="c1", name="list_files", arguments={})],
            input_tokens=100,
            output_tokens=5,
        ),
        LLMResponse(content="Looks fine.", input_tokens=250, output_tokens=12),
    ])

    await service.run_agent_turn(
        solution.id,
        session_id=session_row.id,
        requested_by=session_row.user_id,
        user_message="What is in here?",
    )

    rows = await db_session.execute(
        select(AIUsage).where(AIUsage.conversation_id == session_row.conversation_id)
    )
    usage = rows.scalars().one()
    assert usage.input_tokens == 350
    assert usage.output_tokens == 17
    assert usage.provider == "openai"
    assert usage.user_id == session_row.user_id

    messages = await _messages(db_session, session_row.conversation_id)
    assert usage.message_id == messages[-1].id


# ── tool call rows ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_each_tool_call_is_persisted_for_the_progress_panel(
    db_session: AsyncSession,
    service: BuilderAgentTurnService,
    solution: Solution,
    session_row: SolutionBuilderSession,
    client: ScriptedLLMClient,
):
    """Two rows per call, in order, between the question and the reply."""
    client.script(
        [
            LLMResponse(
                tool_calls=[
                    ToolCallRequest(id="c1", name="list_files", arguments={}),
                    ToolCallRequest(
                        id="c2",
                        name="write_file",
                        arguments={"path": "workflows/a.py", "content": "a = 1\n"},
                    ),
                ],
                input_tokens=30,
                output_tokens=8,
            ),
            LLMResponse(content="Added it.", input_tokens=40, output_tokens=4),
        ]
    )

    await service.run_agent_turn(
        solution.id,
        session_id=session_row.id,
        requested_by=session_row.user_id,
        user_message="Add a workflow.",
    )

    messages = await _messages(db_session, session_row.conversation_id)
    assert [m.role for m in messages] == [
        MessageRole.USER,
        MessageRole.TOOL_CALL,
        MessageRole.TOOL,
        MessageRole.TOOL_CALL,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]

    first_call, first_result = messages[1], messages[2]
    assert first_call.tool_name == "list_files"
    assert first_call.tool_state == "completed"
    assert first_call.tool_input == {}
    assert "bifrost.solution.yaml" in first_call.tool_result["output"]
    # The companion TOOL row carries the same text and ties back by call id.
    assert first_result.content == first_call.tool_result["output"]
    assert first_result.tool_call_id == first_call.tool_call_id

    second_call = messages[3]
    assert second_call.tool_name == "write_file"
    assert second_call.tool_input == {"path": "workflows/a.py", "content": "a = 1\n"}
    assert second_call.tool_result == {"output": "Wrote workflows/a.py."}


@pytest.mark.asyncio
async def test_a_refused_tool_call_is_persisted_as_an_error(
    db_session: AsyncSession,
    service: BuilderAgentTurnService,
    solution: Solution,
    session_row: SolutionBuilderSession,
    client: ScriptedLLMClient,
):
    """A refused call must stay visible — that is what makes a bad turn debuggable."""
    client.script(
        [
            LLMResponse(
                tool_calls=[
                    ToolCallRequest(
                        id="c1",
                        name="read_file",
                        arguments={"path": "../../etc/passwd"},
                    )
                ],
                input_tokens=20,
                output_tokens=6,
            ),
            LLMResponse(content="I cannot read that.", input_tokens=25, output_tokens=5),
        ]
    )

    outcome = await service.run_agent_turn(
        solution.id,
        session_id=session_row.id,
        requested_by=session_row.user_id,
        user_message="Read /etc/passwd.",
    )

    # The turn itself still succeeds: a refused tool is the model's problem to
    # work around, not a lifecycle failure.
    assert outcome.turn.status == STATUS_SUCCEEDED
    assert outcome.tool_call_count == 1

    messages = await _messages(db_session, session_row.conversation_id)
    tool_call = next(m for m in messages if m.role == MessageRole.TOOL_CALL)
    assert tool_call.tool_state == "error"
    assert "path contains" in tool_call.tool_result["error"]
    assert "output" not in tool_call.tool_result


# ── history ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prior_turns_reach_the_model_in_conversation_order(
    service: BuilderAgentTurnService,
    solution: Solution,
    session_row: SolutionBuilderSession,
    client: ScriptedLLMClient,
):
    client.script([
        LLMResponse(content="First reply.", input_tokens=5, output_tokens=2),
        LLMResponse(content="Second reply.", input_tokens=9, output_tokens=2),
    ])

    await service.run_agent_turn(
        solution.id,
        session_id=session_row.id,
        requested_by=session_row.user_id,
        user_message="First question.",
    )
    await service.run_agent_turn(
        solution.id,
        session_id=session_row.id,
        requested_by=session_row.user_id,
        user_message="Second question.",
    )

    # The second turn's single model call: system, the two prior messages in
    # order, then the current question — and the current question exactly once.
    second_call = client.calls[1]
    assert [(m.role, m.content) for m in second_call] == [
        ("system", second_call[0].content),
        ("user", "First question."),
        ("assistant", "First reply."),
        ("user", "Second question."),
    ]


# ── failure ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_failure_fails_the_turn_but_keeps_the_user_message(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    service: BuilderAgentTurnService,
    solution: Solution,
    session_row: SolutionBuilderSession,
):
    async def _exploding(db: AsyncSession) -> BaseLLMClient:
        return ExplodingLLMClient()

    monkeypatch.setattr(agent_turns_module, "get_builder_llm_client", _exploding)

    with pytest.raises(RuntimeError, match="model provider is down"):
        await service.run_agent_turn(
            solution.id,
            session_id=session_row.id,
            requested_by=session_row.user_id,
            user_message="Break something.",
        )

    # The question survives; there is no silent hole in the conversation.
    messages = await _messages(db_session, session_row.conversation_id)
    assert [(m.role, m.content) for m in messages] == [
        (MessageRole.USER, "Break something.")
    ]

    turn = await _latest_turn(db_session, session_row.id)
    assert turn.status == STATUS_FAILED
    assert turn.output_revision_id is None

    revisions = await db_session.execute(
        select(SolutionSourceRevision).where(
            SolutionSourceRevision.solution_id == solution.id
        )
    )
    assert len(list(revisions.scalars().all())) == 1  # scaffold only


@pytest.mark.asyncio
async def test_write_lock_contention_propagates_as_a_conflict(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    service: BuilderAgentTurnService,
    solution: Solution,
    session_row: SolutionBuilderSession,
):
    monkeypatch.setattr(turns_module, "solution_write_lock", _held_lock)

    with pytest.raises(BuilderTurnConflict):
        await service.run_agent_turn(
            solution.id,
            session_id=session_row.id,
            requested_by=session_row.user_id,
            user_message="Race me.",
        )

    messages = await _messages(db_session, session_row.conversation_id)
    assert [m.role for m in messages] == [MessageRole.USER]


async def _latest_turn(db: AsyncSession, session_id: UUID) -> SolutionBuilderTurn:
    rows = await db.execute(
        select(SolutionBuilderTurn)
        .where(SolutionBuilderTurn.session_id == session_id)
        .order_by(SolutionBuilderTurn.created_at.desc())
    )
    turn = rows.scalars().first()
    assert turn is not None
    return turn


class TestRevisionSummary:
    """A revision with no summary is unusable in the revision list, which is
    where a user finds the change they want to undo."""

    def test_uses_the_request_as_the_label(self) -> None:
        from src.services.builder.agent_turns import _revision_summary

        assert _revision_summary("Add a README") == "Add a README"

    def test_takes_only_the_first_line(self) -> None:
        from src.services.builder.agent_turns import _revision_summary

        assert _revision_summary("Add a table\n\nwith fields x, y") == "Add a table"

    def test_truncates_a_long_request(self) -> None:
        from src.services.builder.agent_turns import _revision_summary

        out = _revision_summary("x" * 500)
        assert len(out) <= 120 and out.endswith("…")

    def test_blank_request_still_gets_a_label(self) -> None:
        from src.services.builder.agent_turns import _revision_summary

        assert _revision_summary("   \n  ") == "builder turn"
