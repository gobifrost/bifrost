"""Callback-router tests for job-bound sandbox builds."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.solution_builder import (
    BuildJobStatusUpdate,
    BuildOutputEntry,
)
from src.models.contracts.sandbox_runner import (
    SandboxBuilderTurnCompletion,
    SandboxOpenAIChatCompletionRequest,
)
from src.models.enums import MessageRole
from src.models.orm.agents import Agent, Conversation
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_build_jobs import SolutionBuildJob
from src.models.orm.solution_builder import SolutionBuilderSession, SolutionBuilderTurn
from src.routers.sandbox_jobs import (
    complete_build,
    complete_turn,
    get_input,
    get_turn_harness_state,
    get_turn_context,
    put_turn_harness_state,
    stream_turn_llm_openai,
)
from src.services.builder.capabilities import (
    SANDBOX_JOB_OPERATIONS,
    SandboxJobCapability,
)


def _capability(job_id):
    return SandboxJobCapability(
        job_id=job_id,
        job_type="solution.build",
        dispatch_attempt=2,
        operations=SANDBOX_JOB_OPERATIONS["solution.build"],
        token_id="token-id",
    )


def _turn_capability(job_id):
    return SandboxJobCapability(
        job_id=job_id,
        job_type="solution.builder.turn",
        dispatch_attempt=3,
        operations=SANDBOX_JOB_OPERATIONS["solution.builder.turn"],
        token_id="token-id",
    )


@pytest.mark.asyncio
async def test_turn_context_restores_skill_and_negotiated_tool_contract():
    job_id = uuid4()
    session_id = uuid4()
    conversation_id = uuid4()
    agent_id = uuid4()
    solution_id = uuid4()
    base_revision_id = uuid4()
    turn = SimpleNamespace(
        id=job_id,
        session_id=session_id,
        base_revision_id=base_revision_id,
        status="running",
    )
    session = SimpleNamespace(
        id=session_id,
        solution_id=solution_id,
        conversation_id=conversation_id,
    )
    conversation = SimpleNamespace(id=conversation_id, agent_id=agent_id)
    agent = SimpleNamespace(
        id=agent_id,
        llm_model="openrouter/example",
        bundle_path="skills/bifrost-build",
        max_iterations=12,
        max_token_budget=42_000,
        system_tools=["read_file", "write_file"],
    )
    message = SimpleNamespace(
        role=MessageRole.USER,
        content="Build a portal",
        tool_calls=None,
        tool_call_id=None,
        tool_name=None,
    )
    db = AsyncMock()

    async def get(model, identifier, **_kwargs):
        values = {
            (SolutionBuilderTurn, job_id): turn,
            (SolutionBuilderSession, session_id): session,
            (Conversation, conversation_id): conversation,
            (Agent, agent_id): agent,
        }
        return values.get((model, identifier))

    db.get.side_effect = get
    result = MagicMock()
    result.scalars.return_value.all.return_value = [message]
    db.execute.return_value = result

    with patch(
        "src.routers.sandbox_jobs.build_agent_system_prompt",
        return_value="Skill instructions",
    ):
        context = await get_turn_context(
            job_id,
            db,
            _turn_capability(job_id),
        )

    assert context.system_prompt == "Skill instructions"
    assert context.bundle_path == "skills/bifrost-build"
    assert context.system_tools == ["read_file", "write_file"]
    assert context.model == "openrouter/example"
    assert context.messages[0].content == "Build a portal"


@pytest.mark.asyncio
async def test_turn_harness_state_streams_latest_accepted_session_state() -> None:
    job_id = uuid4()
    session_id = uuid4()
    solution_id = uuid4()
    previous_turn_id = uuid4()
    turn = SimpleNamespace(
        id=job_id,
        session_id=session_id,
        status="running",
        resume_from_turn_id=None,
    )
    session = SimpleNamespace(id=session_id, solution_id=solution_id)
    previous = SimpleNamespace(id=previous_turn_id)
    db = AsyncMock()

    async def get(model, identifier, **_kwargs):
        if model is SolutionBuilderTurn and identifier == job_id:
            return turn
        if model is SolutionBuilderSession and identifier == session_id:
            return session
        return None

    async def chunks():
        yield b"state"

    db.get.side_effect = get
    db.scalar.return_value = previous
    storage = MagicMock()
    storage.exists_accepted = AsyncMock(return_value=True)
    storage.iter_accepted.return_value = chunks()

    with patch(
        "src.routers.sandbox_jobs.BuilderHarnessStateStorage",
        return_value=storage,
    ):
        response = await get_turn_harness_state(
            job_id,
            db,
            _turn_capability(job_id),
        )

    body = b"".join([chunk async for chunk in response.body_iterator])
    assert response.media_type == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    assert body == b"state"
    storage.exists_accepted.assert_awaited_once_with(
        solution_id,
        session_id,
        previous_turn_id,
    )


@pytest.mark.asyncio
async def test_resumed_turn_streams_checkpoint_workspace_and_harness_state() -> None:
    job_id = uuid4()
    session_id = uuid4()
    solution_id = uuid4()
    checkpoint_turn_id = uuid4()
    base_revision_id = uuid4()
    turn = SimpleNamespace(
        id=job_id,
        session_id=session_id,
        base_revision_id=base_revision_id,
        resume_from_turn_id=checkpoint_turn_id,
        status="running",
    )
    checkpoint = SimpleNamespace(
        id=checkpoint_turn_id,
        session_id=session_id,
        checkpoint_sha256="a" * 64,
    )
    session = SimpleNamespace(id=session_id, solution_id=solution_id)
    db = AsyncMock()

    async def get(model, identifier, **_kwargs):
        values = {
            (SolutionBuilderTurn, job_id): turn,
            (SolutionBuilderTurn, checkpoint_turn_id): checkpoint,
            (SolutionBuilderSession, session_id): session,
        }
        return values.get((model, identifier))

    async def workspace_chunks():
        yield b"workspace"

    async def harness_chunks():
        yield b"harness"

    db.get.side_effect = get
    artifact_storage = MagicMock()
    artifact_storage.iter_checkpoint.return_value = workspace_chunks()
    harness_storage = MagicMock()
    harness_storage.exists_accepted = AsyncMock(return_value=True)
    harness_storage.iter_accepted.return_value = harness_chunks()

    with (
        patch(
            "src.routers.sandbox_jobs.BuilderTurnArtifactStorage",
            return_value=artifact_storage,
        ),
        patch(
            "src.routers.sandbox_jobs.BuilderHarnessStateStorage",
            return_value=harness_storage,
        ),
    ):
        workspace_response = await get_input(
            job_id,
            db,
            _turn_capability(job_id),
        )
        harness_response = await get_turn_harness_state(
            job_id,
            db,
            _turn_capability(job_id),
        )

    assert b"".join(
        [chunk async for chunk in workspace_response.body_iterator]
    ) == b"workspace"
    assert b"".join(
        [chunk async for chunk in harness_response.body_iterator]
    ) == b"harness"
    artifact_storage.iter_checkpoint.assert_called_once_with(
        solution_id,
        session_id,
        checkpoint_turn_id,
    )
    harness_storage.exists_accepted.assert_awaited_once_with(
        solution_id,
        session_id,
        checkpoint_turn_id,
    )
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_turn_harness_state_upload_is_attempt_scoped_and_bounded() -> None:
    job_id = uuid4()
    turn = SimpleNamespace(id=job_id, status="running")
    db = AsyncMock()
    db.get.return_value = turn

    async def stream():
        yield b"state"

    request = SimpleNamespace(stream=stream)
    storage = MagicMock()
    storage.write_staged = AsyncMock(return_value=("a" * 64, 5))
    with patch(
        "src.routers.sandbox_jobs.BuilderHarnessStateStorage",
        return_value=storage,
    ):
        response = await put_turn_harness_state(
            job_id,
            request,
            db,
            _turn_capability(job_id),
        )

    assert response == {"sha256": "a" * 64, "size": 5}
    storage.write_staged.assert_awaited_once()
    args, kwargs = storage.write_staged.await_args
    assert args[:2] == (job_id, 3)
    assert kwargs["max_bytes"] == 52_428_800


@pytest.mark.asyncio
async def test_openai_harness_gateway_requires_streaming() -> None:
    job_id = uuid4()

    with pytest.raises(HTTPException) as raised:
        await stream_turn_llm_openai(
            job_id,
            SandboxOpenAIChatCompletionRequest(
                model="builder",
                messages=[{"role": "user", "content": "Build it"}],
                stream=False,
            ),
            AsyncMock(),
            _turn_capability(job_id),
        )

    assert raised.value.status_code == 400


@pytest.mark.asyncio
async def test_openai_harness_gateway_returns_unbuffered_event_stream() -> None:
    job_id = uuid4()

    async def events():
        yield b"data: [DONE]\n\n"

    start = AsyncMock(return_value=events())
    with patch("src.routers.sandbox_jobs.start_builder_openai_stream", start):
        response = await stream_turn_llm_openai(
            job_id,
            SandboxOpenAIChatCompletionRequest(
                model="builder",
                messages=[{"role": "user", "content": "Build it"}],
            ),
            AsyncMock(),
            _turn_capability(job_id),
        )

    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    start.assert_awaited_once()


@pytest.mark.asyncio
async def test_successful_build_verifies_manifest_and_finishes_platform_job():
    job_id = uuid4()
    app_id = uuid4()
    platform_job = SimpleNamespace(id=job_id, status="waiting")
    build_job = SimpleNamespace(
        id=job_id,
        app_id=app_id,
        status="running",
        error=None,
        log_excerpt=None,
        output_manifest=None,
        last_progress_at=None,
        completed_at=None,
    )
    db = AsyncMock()

    async def get(model, _identifier, **_kwargs):
        return platform_job if model is PlatformJob else build_job

    db.get.side_effect = get
    storage = MagicMock()
    storage.verify_manifest = AsyncMock()
    finish = AsyncMock(return_value=True)
    manifest = [
        BuildOutputEntry(path="assets/index.js", sha256="a" * 64, size=42)
    ]

    with (
        patch("src.routers.sandbox_jobs.StagedBuildArtifactStorage", return_value=storage),
        patch("src.services.platform_jobs.finish_external_platform_job", finish),
    ):
        response = await complete_build(
            job_id,
            BuildJobStatusUpdate(
                status="succeeded",
                output_manifest=manifest,
                log_excerpt="built",
            ),
            db,
            _capability(job_id),
        )

    assert response.status_code == 204
    storage.verify_manifest.assert_awaited_once_with(
        app_id,
        [{"path": "assets/index.js", "sha256": "a" * 64, "size": 42}],
    )
    assert build_job.status == "succeeded"
    db.commit.assert_awaited_once()
    finish.assert_awaited_once()
    assert finish.await_args.kwargs["status"] == "succeeded"


@pytest.mark.asyncio
async def test_successful_build_requires_a_verified_manifest():
    job_id = uuid4()
    db = AsyncMock()

    async def get(model, _identifier, **_kwargs):
        if model is PlatformJob:
            return SimpleNamespace(id=job_id, status="waiting")
        if model is SolutionBuildJob:
            return SimpleNamespace(id=job_id, app_id=uuid4(), status="running")
        return None

    db.get.side_effect = get

    with pytest.raises(HTTPException) as raised:
        await complete_build(
            job_id,
            BuildJobStatusUpdate(status="succeeded", output_manifest=None),
            db,
            _capability(job_id),
        )

    assert raised.value.status_code == 400
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_transient_build_failure_requeues_instead_of_finishing():
    job_id = uuid4()
    platform_job = SimpleNamespace(id=job_id, status="waiting")
    build_job = SimpleNamespace(id=job_id, app_id=uuid4(), status="running")
    db = AsyncMock()

    async def get(model, _identifier, **_kwargs):
        return platform_job if model is PlatformJob else build_job

    db.get.side_effect = get
    retry = AsyncMock(return_value=True)
    with patch(
        "src.services.builder.build_requests.retry_external_build_completion",
        retry,
    ):
        response = await complete_build(
            job_id,
            BuildJobStatusUpdate(
                status="failed",
                error="Durable Object reset because its code was updated.",
                retryable=True,
            ),
            db,
            _capability(job_id),
        )

    assert response.status_code == 204
    retry.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_transient_turn_failure_requeues_instead_of_finishing():
    job_id = uuid4()
    platform_job = SimpleNamespace(id=job_id, status="waiting")
    turn = SimpleNamespace(id=job_id, status="running")
    db = AsyncMock()

    async def get(model, _identifier, **_kwargs):
        return platform_job if model is PlatformJob else turn

    db.get.side_effect = get
    turn_service = MagicMock()
    turn_service.retry_external_agent_turn = AsyncMock(return_value=platform_job)
    turn_service.finish_failed_agent_turn = AsyncMock()
    with patch(
        "src.routers.sandbox_jobs.BuilderAgentTurnService",
        return_value=turn_service,
    ):
        response = await complete_turn(
            job_id,
            SandboxBuilderTurnCompletion(
                status="failed",
                error="Durable Object reset because its code was updated.",
                retryable=True,
            ),
            db,
            _turn_capability(job_id),
        )

    assert response.status_code == 204
    turn_service.retry_external_agent_turn.assert_awaited_once()
    turn_service.finish_failed_agent_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_turn_failure_preserves_privacy_safe_harness_diagnostics():
    job_id = uuid4()
    platform_job = SimpleNamespace(id=job_id, status="waiting")
    turn = SimpleNamespace(id=job_id, status="running")
    db = AsyncMock()

    async def get(model, _identifier, **_kwargs):
        return platform_job if model is PlatformJob else turn

    db.get.side_effect = get
    turn_service = MagicMock()
    turn_service.finish_failed_agent_turn = AsyncMock()
    with patch(
        "src.routers.sandbox_jobs.BuilderAgentTurnService",
        return_value=turn_service,
    ):
        response = await complete_turn(
            job_id,
            SandboxBuilderTurnCompletion(
                status="failed",
                error="model failed",
                checkpoint_output_sha256="b" * 64,
                harness_diagnostics={
                    "message_count": 3,
                    "assistant_message_count": 2,
                    "tool_call_count": 4,
                    "tool_error_count": 1,
                    "tools": [
                        {"name": "write", "count": 4, "error_count": 1}
                    ],
                },
            ),
            db,
            _turn_capability(job_id),
        )

    assert response.status_code == 204
    call = turn_service.finish_failed_agent_turn.await_args.kwargs
    assert call["harness_diagnostics"]["tool_call_count"] == 4
    assert call["harness_diagnostics"]["tools"] == [
        {"name": "write", "count": 4, "error_count": 1}
    ]
    assert call["checkpoint_output_sha256"] == "b" * 64


@pytest.mark.asyncio
async def test_late_cancel_callback_promotes_newly_staged_checkpoint() -> None:
    job_id = uuid4()
    platform_job = SimpleNamespace(id=job_id, status="cancelled")
    turn = SimpleNamespace(
        id=job_id,
        status="cancelled",
        checkpoint_sha256=None,
    )
    db = AsyncMock()

    async def get(model, _identifier, **_kwargs):
        return platform_job if model is PlatformJob else turn

    db.get.side_effect = get
    turn_service = MagicMock()
    turn_service.preserve_agent_turn_checkpoint = AsyncMock()
    with patch(
        "src.routers.sandbox_jobs.BuilderAgentTurnService",
        return_value=turn_service,
    ):
        response = await complete_turn(
            job_id,
            SandboxBuilderTurnCompletion(
                status="cancelled",
                error="job cancelled",
                checkpoint_output_sha256="c" * 64,
            ),
            db,
            _turn_capability(job_id),
        )

    assert response.status_code == 204
    turn_service.preserve_agent_turn_checkpoint.assert_awaited_once_with(
        turn_id=job_id,
        dispatch_attempt=3,
        output_sha256="c" * 64,
    )
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_completion_is_idempotent_for_matching_terminal_projection():
    job_id = uuid4()
    db = AsyncMock()

    async def get(model, _identifier, **_kwargs):
        if model is PlatformJob:
            return SimpleNamespace(id=job_id, status="succeeded")
        return SimpleNamespace(id=job_id, app_id=uuid4(), status="succeeded")

    db.get.side_effect = get
    response = await complete_build(
        job_id,
        BuildJobStatusUpdate(status="succeeded", output_manifest=None),
        db,
        _capability(job_id),
    )

    assert response.status_code == 204
    db.commit.assert_not_awaited()
