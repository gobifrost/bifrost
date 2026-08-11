from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import MessageRole
from src.models.orm.agents import Agent, Conversation, Message
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_builder import (
    SolutionBuilderProject,
    SolutionBuilderSession,
    SolutionSourceRevision,
)
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.services.builder.agent_turns import (
    BuilderAgentTurnService,
    BuilderTurnCompletionFenced,
)
from src.services.builder.scaffold import (
    BUILDER_AGENT_MAX_ITERATIONS,
    BUILDER_AGENT_MAX_TOKEN_BUDGET,
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
    assert agent.max_iterations == BUILDER_AGENT_MAX_ITERATIONS
    assert agent.max_token_budget == BUILDER_AGENT_MAX_TOKEN_BUDGET
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


@pytest.mark.asyncio
async def test_enqueue_turn_can_explicitly_resume_a_failed_checkpoint(
    db_session: AsyncSession,
    builder_rows: tuple[Solution, SolutionBuilderSession, User],
) -> None:
    solution, session, user = builder_rows
    service = BuilderAgentTurnService(db_session)
    interrupted = await service.enqueue_agent_turn(
        solution.id,
        session_id=session.id,
        requested_by=user.id,
        user_message="Build the first draft.",
    )
    interrupted.turn.status = "failed"
    interrupted.turn.checkpoint_sha256 = "a" * 64
    interrupted.platform_job.status = "failed"
    await db_session.commit()

    resumed = await service.enqueue_agent_turn(
        solution.id,
        session_id=session.id,
        requested_by=user.id,
        user_message="Continue from the saved checkpoint.",
        resume_from_turn_id=interrupted.turn.id,
    )

    assert resumed.turn.resume_from_turn_id == interrupted.turn.id
    assert resumed.turn.base_revision_id == interrupted.turn.base_revision_id
    assert resumed.turn.status == "queued"


@pytest.mark.asyncio
async def test_enqueue_turn_refuses_checkpoint_after_solution_revision_changed(
    db_session: AsyncSession,
    builder_rows: tuple[Solution, SolutionBuilderSession, User],
) -> None:
    solution, session, user = builder_rows
    service = BuilderAgentTurnService(db_session)
    interrupted = await service.enqueue_agent_turn(
        solution.id,
        session_id=session.id,
        requested_by=user.id,
        user_message="Build the first draft.",
    )
    interrupted.turn.status = "failed"
    interrupted.turn.checkpoint_sha256 = "a" * 64
    interrupted.platform_job.status = "failed"
    base = await db_session.get(
        SolutionSourceRevision,
        interrupted.turn.base_revision_id,
    )
    project = await db_session.get(SolutionBuilderProject, solution.id)
    assert base is not None
    assert project is not None
    newer = SolutionSourceRevision(
        id=uuid4(),
        solution_id=solution.id,
        parent_revision_id=base.id,
        created_by=user.id,
        source_sha256=base.source_sha256,
        size_bytes=base.size_bytes,
        summary="newer revision",
    )
    db_session.add(newer)
    await db_session.flush()
    project.current_revision_id = newer.id
    await db_session.commit()

    with pytest.raises(BuilderTurnConflict, match="changed after this checkpoint"):
        await service.enqueue_agent_turn(
            solution.id,
            session_id=session.id,
            requested_by=user.id,
            user_message="Resume stale work.",
            resume_from_turn_id=interrupted.turn.id,
        )


@pytest.mark.asyncio
async def test_finalize_turn_accepts_harness_state_with_workspace_result(
    db_session: AsyncSession,
    builder_rows: tuple[Solution, SolutionBuilderSession, User],
    fake_revision_storage: dict[str, bytes],
) -> None:
    solution, session, user = builder_rows
    service = BuilderAgentTurnService(db_session)
    queued = await service.enqueue_agent_turn(
        solution.id,
        session_id=session.id,
        requested_by=user.id,
        user_message="Keep the existing workspace.",
    )
    base = fake_revision_storage[str(queued.turn.base_revision_id)]
    artifact = MagicMock()

    async def copy_output(destination: Path) -> None:
        destination.write_bytes(base)

    artifact.copy_to_path = AsyncMock(side_effect=copy_output)
    artifact.delete = AsyncMock()
    harness = MagicMock()
    harness.promote = AsyncMock()
    harness.delete_staged = AsyncMock()

    with (
        patch(
            "src.services.builder.agent_turns.BuilderTurnArtifactStorage",
            return_value=artifact,
        ),
        patch(
            "src.services.builder.agent_turns.BuilderHarnessStateStorage",
            return_value=harness,
        ),
        patch(
            "src.services.platform_jobs.stage_external_platform_job_completion",
            AsyncMock(return_value=queued.platform_job),
        ),
        patch(
            "src.services.platform_jobs.publish_platform_job_update",
            AsyncMock(),
        ),
    ):
        completed = await service.finalize_agent_turn(
            solution.id,
            turn_id=queued.turn.id,
            dispatch_attempt=4,
            output_sha256=hashlib.sha256(base).hexdigest(),
            final_text="No file changes were needed.",
            tool_call_count=1,
            model="builder-model",
        )

    assert completed.revision_created is False
    harness.promote.assert_awaited_once_with(
        solution_id=solution.id,
        session_id=session.id,
        turn_id=queued.turn.id,
        dispatch_attempt=4,
    )
    harness.delete_staged.assert_awaited_once_with(queued.turn.id, 4)


@pytest.mark.asyncio
async def test_finalize_changed_turn_compensates_when_completion_is_fenced(
    db_session: AsyncSession,
    builder_rows: tuple[Solution, SolutionBuilderSession, User],
) -> None:
    solution, session, user = builder_rows
    service = BuilderAgentTurnService(db_session)
    queued = await service.enqueue_agent_turn(
        solution.id,
        session_id=session.id,
        requested_by=user.id,
        user_message="Change the workspace.",
    )
    solution_id = solution.id
    session_id = session.id
    turn_id = queued.turn.id
    base_revision_id = queued.turn.base_revision_id
    output = b"changed workspace"
    artifact = MagicMock()

    async def copy_output(destination: Path) -> None:
        destination.write_bytes(output)

    artifact.copy_to_path = AsyncMock(side_effect=copy_output)
    artifact.delete = AsyncMock()
    harness = MagicMock()
    harness.promote = AsyncMock()
    harness.delete_accepted = AsyncMock()
    harness.delete_staged = AsyncMock()
    revision_storage = MagicMock()
    revision_storage.delete = AsyncMock()
    revision_id = uuid4()

    async def materialize_changed(*_args, **_kwargs):
        base = await db_session.get(SolutionSourceRevision, base_revision_id)
        project = await db_session.get(SolutionBuilderProject, solution.id)
        assert base is not None
        assert project is not None
        db_session.add(
            SolutionSourceRevision(
                id=revision_id,
                solution_id=solution.id,
                parent_revision_id=base.id,
                created_by=user.id,
                source_sha256="f" * 64,
                size_bytes=len(output),
                summary="changed",
            )
        )
        await db_session.flush()
        project.current_revision_id = revision_id
        queued.turn.output_revision_id = revision_id
        queued.turn.status = "succeeded"
        await db_session.flush()
        materialized = MagicMock()
        materialized.turn = queued.turn
        materialized.revision_created = True
        return materialized

    service.turns.materialize_external_output = AsyncMock(
        side_effect=materialize_changed
    )
    with (
        patch(
            "src.services.builder.agent_turns.BuilderTurnArtifactStorage",
            return_value=artifact,
        ),
        patch(
            "src.services.builder.agent_turns.BuilderHarnessStateStorage",
            return_value=harness,
        ),
        patch(
            "src.services.builder.agent_turns.SolutionRevisionStorage",
            return_value=revision_storage,
        ),
        patch(
            "src.services.platform_jobs.stage_external_platform_job_completion",
            AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(BuilderTurnCompletionFenced):
            await service.finalize_agent_turn(
                solution.id,
                turn_id=turn_id,
                dispatch_attempt=2,
                output_sha256=hashlib.sha256(output).hexdigest(),
                final_text="Done",
                tool_call_count=1,
            )

    assert await db_session.get(SolutionSourceRevision, revision_id) is None
    restored_project = await db_session.get(SolutionBuilderProject, solution_id)
    restored_turn = await db_session.get(type(queued.turn), turn_id)
    assert restored_project is not None
    assert restored_project.current_revision_id == base_revision_id
    assert restored_turn is not None
    assert restored_turn.status == "queued"
    revision_storage.delete.assert_awaited_once_with(revision_id)
    harness.delete_accepted.assert_awaited_once_with(
        solution_id,
        session_id,
        turn_id,
    )
    artifact.delete.assert_awaited_once()
    harness.delete_staged.assert_awaited_once_with(turn_id, 2)


@pytest.mark.asyncio
async def test_preserve_turn_checkpoint_accepts_matching_workspace_and_harness(
    db_session: AsyncSession,
    builder_rows: tuple[Solution, SolutionBuilderSession, User],
) -> None:
    solution, session, user = builder_rows
    service = BuilderAgentTurnService(db_session)
    queued = await service.enqueue_agent_turn(
        solution.id,
        session_id=session.id,
        requested_by=user.id,
        user_message="Build a draft.",
    )
    payload = b"checkpoint workspace"
    artifact = MagicMock()

    async def copy_output(destination: Path) -> None:
        destination.write_bytes(payload)

    artifact.copy_to_path = AsyncMock(side_effect=copy_output)
    artifact.promote_checkpoint = AsyncMock()
    harness = MagicMock()
    harness.promote = AsyncMock()

    with (
        patch(
            "src.services.builder.agent_turns.BuilderTurnArtifactStorage",
            return_value=artifact,
        ),
        patch(
            "src.services.builder.agent_turns.BuilderHarnessStateStorage",
            return_value=harness,
        ),
    ):
        turn = await service.preserve_agent_turn_checkpoint(
            turn_id=queued.turn.id,
            dispatch_attempt=3,
            output_sha256=hashlib.sha256(payload).hexdigest(),
        )

    assert turn.checkpoint_available
    harness.promote.assert_awaited_once_with(
        solution_id=solution.id,
        session_id=session.id,
        turn_id=queued.turn.id,
        dispatch_attempt=3,
    )
    artifact.promote_checkpoint.assert_awaited_once_with(
        solution_id=solution.id,
        session_id=session.id,
    )


@pytest.mark.asyncio
async def test_failed_turn_removes_checkpoint_when_completion_is_fenced(
    db_session: AsyncSession,
    builder_rows: tuple[Solution, SolutionBuilderSession, User],
) -> None:
    solution, session, user = builder_rows
    service = BuilderAgentTurnService(db_session)
    queued = await service.enqueue_agent_turn(
        solution.id,
        session_id=session.id,
        requested_by=user.id,
        user_message="Build a recoverable draft.",
    )
    solution_id = solution.id
    session_id = session.id
    turn_id = queued.turn.id
    output = b"recoverable checkpoint"
    artifact = MagicMock()

    async def copy_output(destination: Path) -> None:
        destination.write_bytes(output)

    artifact.copy_to_path = AsyncMock(side_effect=copy_output)
    artifact.promote_checkpoint = AsyncMock()
    artifact.delete_checkpoint = AsyncMock()
    artifact.delete = AsyncMock()
    harness = MagicMock()
    harness.promote = AsyncMock()
    harness.delete_accepted = AsyncMock()
    harness.delete_staged = AsyncMock()

    with (
        patch(
            "src.services.builder.agent_turns.BuilderTurnArtifactStorage",
            return_value=artifact,
        ),
        patch(
            "src.services.builder.agent_turns.BuilderHarnessStateStorage",
            return_value=harness,
        ),
        patch(
            "src.services.platform_jobs.stage_external_platform_job_completion",
            AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(BuilderTurnCompletionFenced):
            await service.finish_failed_agent_turn(
                turn_id=turn_id,
                dispatch_attempt=3,
                status="failed",
                error="Runner stopped",
                checkpoint_output_sha256=hashlib.sha256(output).hexdigest(),
            )

    restored_turn = await db_session.get(type(queued.turn), turn_id)
    assert restored_turn is not None
    assert restored_turn.status == "queued"
    assert restored_turn.checkpoint_sha256 is None
    artifact.delete_checkpoint.assert_awaited_once_with(
        solution_id,
        session_id,
        turn_id,
    )
    harness.delete_accepted.assert_awaited_once_with(
        solution_id,
        session_id,
        turn_id,
    )
    artifact.delete.assert_awaited_once()
    harness.delete_staged.assert_awaited_once_with(turn_id, 3)
