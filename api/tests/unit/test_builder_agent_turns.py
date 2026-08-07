from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.agents import ChatStreamChunk
from src.models.orm.agents import Agent, Conversation
from src.models.orm.solution_builder import SolutionBuilderSession
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.services.builder import agent_turns as agent_turns_module
from src.services.builder import turns as turns_module
from src.services.builder.agent_turns import (
    BuilderAgentTurnService,
    BuilderModelUnavailable,
)
from src.services.builder.scaffold import (
    BUILDER_AGENT_SYSTEM_TOOLS,
    BUILDER_SKILL_BUNDLE_PATH,
    builder_agent_id,
)
from src.services.builder.turns import BuilderTurnService


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
async def _null_lock(_solution_id: UUID) -> AsyncIterator[None]:
    yield


class FakeAgentExecutor:
    fail = False
    mutate = False
    captured_agent: Agent | None = None

    def __init__(self, _session_factory, *, builder_workspace=None) -> None:
        self.workspace = builder_workspace

    async def chat(self, agent, _conversation, _message, **_kwargs):
        FakeAgentExecutor.captured_agent = agent
        if self.mutate:
            self.workspace.write_file("workflows/generated.py", b"value = 1\n")
        if self.fail:
            yield ChatStreamChunk(type="error", error="model unavailable")
        else:
            yield ChatStreamChunk(type="done", content="Done.")


@pytest.fixture(autouse=True)
def fake_infrastructure(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, bytes]:
    blobs: dict[str, bytes] = {}
    monkeypatch.setattr(turns_module, "solution_write_lock", _null_lock)
    monkeypatch.setattr(
        BuilderTurnService,
        "_storage",
        lambda self, solution_id: FakeRevisionStorage(blobs),
    )
    monkeypatch.setattr(agent_turns_module, "AgentExecutor", FakeAgentExecutor)
    FakeAgentExecutor.fail = False
    FakeAgentExecutor.mutate = False
    FakeAgentExecutor.captured_agent = None
    return blobs


@pytest_asyncio.fixture
async def builder_rows(
    db_session: AsyncSession,
) -> tuple[Solution, SolutionBuilderSession]:
    user = User(
        id=uuid4(),
        email=f"builder-{uuid4().hex[:8]}@example.com",
        is_superuser=True,
    )
    db_session.add(user)
    await db_session.flush()
    solution = Solution(
        id=uuid4(),
        slug=f"builder-{uuid4().hex[:8]}",
        name="Builder",
        owner_user_id=user.id,
        visibility="private",
    )
    db_session.add(solution)
    await db_session.flush()
    conversation = Conversation(
        id=uuid4(),
        user_id=user.id,
        channel="builder",
    )
    db_session.add(conversation)
    await db_session.flush()
    session = SolutionBuilderSession(
        id=uuid4(),
        solution_id=solution.id,
        conversation_id=conversation.id,
        user_id=user.id,
    )
    db_session.add(session)
    await BuilderTurnService(db_session).create_project(
        solution.id,
        slug=solution.slug,
        name=solution.name,
        conversation_id=None,
        user_id=user.id,
    )
    await db_session.flush()
    return solution, session


@pytest.mark.asyncio
async def test_builder_turn_uses_bundle_backed_platform_agent(
    db_session: AsyncSession,
    builder_rows: tuple[Solution, SolutionBuilderSession],
) -> None:
    solution, session = builder_rows

    outcome = await BuilderAgentTurnService(db_session).run_agent_turn(
        solution.id,
        session_id=session.id,
        requested_by=session.user_id,
        user_message="Explain the workspace.",
    )

    assert outcome.final_text == "Done."
    assert outcome.revision_created is False
    agent = await db_session.get(Agent, builder_agent_id(solution.id))
    assert agent is not None
    assert agent.solution_id == solution.id
    assert agent.bundle_path == BUILDER_SKILL_BUNDLE_PATH
    assert agent.system_tools == BUILDER_AGENT_SYSTEM_TOOLS
    assert agent.system_prompt.startswith("---")
    conversation = await db_session.get(Conversation, session.conversation_id)
    assert conversation.agent_id == agent.id
    assert FakeAgentExecutor.captured_agent is agent


@pytest.mark.asyncio
async def test_changed_turn_queues_the_immutable_revision(
    db_session: AsyncSession,
    builder_rows: tuple[Solution, SolutionBuilderSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    solution, session = builder_rows
    FakeAgentExecutor.mutate = True
    queued: list[UUID] = []

    async def _enqueue(_db, queued_solution_id, *, turn, revision_id):
        assert queued_solution_id == solution.id
        assert turn.output_revision_id == revision_id
        queued.append(revision_id)

    monkeypatch.setattr(
        agent_turns_module,
        "enqueue_builder_turn_deploy",
        _enqueue,
    )

    outcome = await BuilderAgentTurnService(db_session).run_agent_turn(
        solution.id,
        session_id=session.id,
        requested_by=session.user_id,
        user_message="Add code.",
    )

    assert outcome.revision_created is True
    assert queued == [outcome.turn.output_revision_id]


@pytest.mark.asyncio
async def test_executor_error_fails_closed(
    db_session: AsyncSession,
    builder_rows: tuple[Solution, SolutionBuilderSession],
) -> None:
    solution, session = builder_rows
    FakeAgentExecutor.fail = True

    with pytest.raises(BuilderModelUnavailable, match="model unavailable"):
        await BuilderAgentTurnService(db_session).run_agent_turn(
            solution.id,
            session_id=session.id,
            requested_by=session.user_id,
            user_message="Build.",
        )

    rows = (
        await db_session.execute(
            select(Agent).where(Agent.solution_id == solution.id)
        )
    ).scalars().all()
    assert len(rows) == 1
