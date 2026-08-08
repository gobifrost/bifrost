from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import MessageRole
from src.models.orm.agents import Agent, Conversation, Message
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_builder import SolutionBuilderSession
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.services.builder.agent_turns import BuilderAgentTurnService
from src.services.builder.scaffold import (
    BUILDER_AGENT_SYSTEM_TOOLS,
    BUILDER_SKILL_BUNDLE_PATH,
    builder_agent_id,
)
from src.services.builder.turns import BuilderTurnConflict, BuilderTurnService


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


@pytest.fixture(autouse=True)
def fake_revision_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, bytes]:
    blobs: dict[str, bytes] = {}
    monkeypatch.setattr(
        BuilderTurnService,
        "_storage",
        lambda self, solution_id: FakeRevisionStorage(blobs),
    )
    return blobs


@pytest_asyncio.fixture
async def builder_rows(
    db_session: AsyncSession,
) -> tuple[Solution, SolutionBuilderSession, User]:
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
    await db_session.commit()
    return solution, session, user


@pytest.mark.asyncio
async def test_enqueue_turn_persists_prompt_agent_and_encrypted_platform_job(
    db_session: AsyncSession,
    builder_rows: tuple[Solution, SolutionBuilderSession, User],
) -> None:
    solution, session, user = builder_rows

    queued = await BuilderAgentTurnService(db_session).enqueue_agent_turn(
        solution.id,
        session_id=session.id,
        requested_by=user.id,
        user_message="Add an expense dashboard.",
    )

    assert queued.turn.status == "queued"
    assert queued.turn.id == queued.platform_job.id
    assert queued.platform_job.job_type == "solution.builder.turn"
    assert queued.platform_job.payload == {"protected": True}
    assert queued.platform_job.encrypted_payload is not None
    assert queued.platform_job.resource_lock_key == f"solution:{solution.id}"

    agent = await db_session.get(Agent, builder_agent_id(solution.id))
    assert agent is not None
    assert agent.solution_id == solution.id
    assert agent.bundle_path == BUILDER_SKILL_BUNDLE_PATH
    assert agent.system_tools == BUILDER_AGENT_SYSTEM_TOOLS
    assert agent.system_prompt.startswith("---")

    conversation = await db_session.get(Conversation, session.conversation_id)
    assert conversation is not None
    assert conversation.agent_id == agent.id
    messages = (
        (
            await db_session.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.sequence)
            )
        )
        .scalars()
        .all()
    )
    assert [(message.role, message.content) for message in messages] == [
        (MessageRole.USER, "Add an expense dashboard.")
    ]


@pytest.mark.asyncio
async def test_enqueue_turn_refuses_a_second_active_turn(
    db_session: AsyncSession,
    builder_rows: tuple[Solution, SolutionBuilderSession, User],
) -> None:
    solution, session, user = builder_rows
    service = BuilderAgentTurnService(db_session)
    await service.enqueue_agent_turn(
        solution.id,
        session_id=session.id,
        requested_by=user.id,
        user_message="First prompt",
    )

    with pytest.raises(BuilderTurnConflict, match="already active"):
        await service.enqueue_agent_turn(
            solution.id,
            session_id=session.id,
            requested_by=user.id,
            user_message="Second prompt",
        )

    jobs = (
        await db_session.execute(
            select(PlatformJob).where(
                PlatformJob.job_type == "solution.builder.turn",
                PlatformJob.resource_lock_key == f"solution:{solution.id}",
            )
        )
    ).scalars().all()
    assert len(jobs) == 1
