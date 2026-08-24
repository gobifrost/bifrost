from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.contracts.sandbox_runner import SandboxBuilderWorkspaceBuildRequest
from src.models.enums import MessageRole
from src.models.orm.agents import Message
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_builder import (
    SolutionBuilderProject,
    SolutionBuilderSession,
    SolutionSourceRevision,
    SolutionBuilderTurn,
)
from src.routers import sandbox_jobs


class _Rows:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


@pytest.mark.asyncio
async def test_turn_runtime_uses_agentless_builder_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = uuid4()
    session_id = uuid4()
    solution_id = uuid4()
    conversation_id = uuid4()
    requester_id = uuid4()
    solution = SimpleNamespace(
        id=solution_id,
        name="Private Builder",
        organization_id=uuid4(),
        owner_user_id=requester_id,
    )
    project = SimpleNamespace(target_kind="solution")
    turn = SimpleNamespace(
        id=job_id,
        session_id=session_id,
        status="running",
        requested_by=requester_id,
    )
    session = SimpleNamespace(
        id=session_id,
        solution_id=solution_id,
        conversation_id=conversation_id,
    )
    conversation = SimpleNamespace(
        id=conversation_id,
        user=SimpleNamespace(id=requester_id),
    )
    db = AsyncMock()

    async def get(model, key, *args, **kwargs):
        del args, kwargs
        if model is SolutionBuilderTurn and key == job_id:
            return turn
        if model is SolutionBuilderSession and key == session_id:
            return session
        if model is SolutionBuilderProject and key == solution_id:
            return project
        raise AssertionError(f"unexpected get({model}, {key})")

    db.get.side_effect = get
    db.execute.return_value = _Rows(conversation)
    authorization = SimpleNamespace(effective_capabilities=frozenset())
    authorize = AsyncMock(
        return_value=SimpleNamespace(
            solution=solution,
            project=project,
            authorization=authorization,
        )
    )
    profile = SimpleNamespace(
        id=uuid4(),
        name="Maintained Builder",
        bundle_path="skills/bifrost-build",
    )
    monkeypatch.setattr(sandbox_jobs, "authorize_builder_project", authorize)
    monkeypatch.setattr(
        sandbox_jobs,
        "build_builder_runtime_profile",
        lambda authorized_solution, *, target_kind, authorization: profile,
    )

    loaded_turn, loaded_session, loaded_conversation, loaded_profile = (
        await sandbox_jobs._load_turn_runtime(db, job_id)
    )

    assert loaded_turn is turn
    assert loaded_session is session
    assert loaded_conversation is conversation
    assert loaded_profile is profile
    authorize.assert_awaited_once_with(
        db,
        solution_id=solution_id,
        requester_user_id=requester_id,
        action=sandbox_jobs.SolutionAction.EDIT,
        required_capabilities=(
            "builder.execute",
            "solutions.readwrite",
            "solutions.build.execute",
            "solutions.deploy.execute",
        ),
    )


def test_sandbox_runner_keeps_skill_asset_reads_in_bifrost() -> None:
    assert sandbox_jobs._sandbox_tool_execution("read_file") == "sandbox"
    assert sandbox_jobs._sandbox_tool_execution("test_solution_build") == "sandbox"
    assert (
        sandbox_jobs._sandbox_tool_execution("bifrost_read_agent_skill_file")
        == "bifrost"
    )
    assert sandbox_jobs._sandbox_tool_execution("bifrost_create_agent") == "bifrost"


@pytest.mark.asyncio
async def test_workspace_build_message_is_fenced_to_current_execution() -> None:
    conversation_id = uuid4()
    message_id = uuid4()
    execution_id = uuid4()
    message = SimpleNamespace(
        conversation_id=conversation_id,
        execution_id=str(execution_id),
        role=MessageRole.TOOL_CALL,
        tool_name="test_solution_build",
        tool_call_id="call-1",
        tool_state="running",
    )
    db = AsyncMock()
    db.get.return_value = message

    current = await sandbox_jobs._current_workspace_build_message(
        db,
        conversation=SimpleNamespace(id=conversation_id),
        message_id=message_id,
        execution_id=execution_id,
    )

    assert current is message
    db.get.assert_awaited_once_with(Message, message_id)

    with pytest.raises(sandbox_jobs.HTTPException, match="not current"):
        await sandbox_jobs._current_workspace_build_message(
            db,
            conversation=SimpleNamespace(id=conversation_id),
            message_id=message_id,
            execution_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_workspace_build_uses_canonical_build_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = uuid4()
    execution_id = uuid4()
    message_id = uuid4()
    solution_id = uuid4()
    requester_id = uuid4()
    turn = SimpleNamespace(requested_by=requester_id)
    session = SimpleNamespace(solution_id=solution_id)
    conversation = SimpleNamespace(id=uuid4())
    db = AsyncMock()

    async def get(model, key, *args, **kwargs):
        del args, kwargs
        if model is PlatformJob and key == job_id:
            return SimpleNamespace(status="running")
        raise AssertionError(f"unexpected get({model}, {key})")

    db.get.side_effect = get
    monkeypatch.setattr(
        sandbox_jobs,
        "_load_turn_runtime",
        AsyncMock(return_value=(turn, session, conversation, SimpleNamespace())),
    )
    monkeypatch.setattr(
        sandbox_jobs,
        "_current_workspace_build_message",
        AsyncMock(return_value=SimpleNamespace()),
    )

    async def archive_chunks() -> AsyncIterator[bytes]:
        yield b"archive"

    artifact_storage = SimpleNamespace(iter_tool_workspace=lambda _id: archive_chunks())
    monkeypatch.setattr(
        sandbox_jobs,
        "BuilderTurnArtifactStorage",
        lambda *_args: artifact_storage,
    )
    hydrate = AsyncMock(return_value=[])
    monkeypatch.setattr(sandbox_jobs, "hydrate_builder_turn_workspace", hydrate)
    build = AsyncMock(
        return_value=SimpleNamespace(
            as_dict=lambda: {
                "valid": True,
                "app_count": 1,
                "compiled_app_count": 1,
                "prebuilt_app_count": 0,
                "build_job_ids": [str(uuid4())],
            }
        )
    )
    monkeypatch.setattr(sandbox_jobs, "test_solution_workspace_build", build)
    required: list[str] = []
    capability = SimpleNamespace(
        job_type="solution.builder.turn",
        dispatch_attempt=3,
        require=required.append,
    )

    result = await sandbox_jobs.test_tool_workspace_build(
        job_id,
        execution_id,
        SandboxBuilderWorkspaceBuildRequest(
            message_id=message_id,
            output_sha256="a" * 64,
        ),
        db,
        cast(Any, capability),
    )

    assert required == [
        sandbox_jobs.SANDBOX_TOOL_EXECUTE,
        sandbox_jobs.SANDBOX_OUTPUT_WRITE,
    ]
    assert result.structured_content is not None
    assert result.structured_content["valid"] is True
    hydrate.assert_awaited_once()
    build.assert_awaited_once_with(
        hydrate.await_args.kwargs["destination"],
        solution_id=solution_id,
        requested_by=requester_id,
    )


@pytest.mark.asyncio
async def test_turn_archive_source_uses_base_revision_digest() -> None:
    solution_id = uuid4()
    session = SimpleNamespace(id=uuid4(), solution_id=solution_id)
    revision_id = uuid4()
    turn = SimpleNamespace(
        base_revision_id=revision_id,
        resume_from_turn_id=None,
    )
    db = AsyncMock()

    async def get(model, key, *args, **kwargs):
        del args, kwargs
        if model is SolutionSourceRevision and key == revision_id:
            return SimpleNamespace(
                id=revision_id,
                solution_id=solution_id,
                source_sha256="b" * 64,
            )
        raise AssertionError(f"unexpected get({model}, {key})")

    db.get.side_effect = get

    source = await sandbox_jobs._turn_archive_source(db, turn, session)

    assert source.kind == "revision"
    assert source.solution_id == solution_id
    assert source.session_id == session.id
    assert source.archive_id == revision_id
    assert source.expected_sha256 == "b" * 64


@pytest.mark.asyncio
async def test_turn_archive_source_uses_resume_checkpoint_digest() -> None:
    session = SimpleNamespace(id=uuid4(), solution_id=uuid4())
    checkpoint_id = uuid4()
    turn = SimpleNamespace(
        base_revision_id=uuid4(),
        resume_from_turn_id=checkpoint_id,
    )
    db = AsyncMock()

    async def get(model, key, *args, **kwargs):
        del args, kwargs
        if model is SolutionBuilderTurn and key == checkpoint_id:
            return SimpleNamespace(
                id=checkpoint_id,
                session_id=session.id,
                checkpoint_sha256="c" * 64,
            )
        raise AssertionError(f"unexpected get({model}, {key})")

    db.get.side_effect = get

    source = await sandbox_jobs._turn_archive_source(db, turn, session)

    assert source.kind == "checkpoint"
    assert source.solution_id == session.solution_id
    assert source.session_id == session.id
    assert source.archive_id == checkpoint_id
    assert source.expected_sha256 == "c" * 64
