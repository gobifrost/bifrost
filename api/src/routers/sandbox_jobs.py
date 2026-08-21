"""Job-bound callback API used by external sandbox runner attempts."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import get_settings
from src.core.database import get_db
from src.models.contracts.sandbox_runner import (
    SandboxBuilderAttachment,
    SandboxBuilderEventBatch,
    SandboxBuilderMessage,
    SandboxBuilderModelConfig,
    SandboxBuilderTextSegment,
    SandboxBuilderToolDefinition,
    SandboxBuilderToolFinish,
    SandboxBuilderToolResponse,
    SandboxBuilderToolStart,
    SandboxBuilderTurnCompletion,
    SandboxBuilderTurnContext,
    SandboxBuilderWorkspaceBuildRequest,
    SandboxBuilderWorkspaceBuildResult,
    SandboxJobCancelled,
    SandboxJobProgressUpdate,
)
from src.models.contracts.agents import ChatStreamChunk, ToolCall, ToolResult
from src.models.enums import MessageRole
from src.models.orm.agents import Conversation, Message, MessageAttachment
from src.models.contracts.solution_builder import BuildJobStatusUpdate
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_build_jobs import SolutionBuildJob
from src.models.orm.solution_builder import (
    SolutionBuilderProject,
    SolutionBuilderSession,
    SolutionSourceRevision,
    SolutionBuilderTurn,
)
from src.services.agent_execution_profile import AgentExecutionProfile
from src.services.builder.agent_identity import (
    bind_builder_tool_arguments,
    build_builder_runtime_profile,
    sanitize_builder_tool_parameters,
)
from src.services.builder.capabilities import (
    SANDBOX_ARTIFACT_WRITE,
    SANDBOX_ATTACHMENT_READ,
    SANDBOX_CANCEL_READ,
    SANDBOX_COMPLETE_WRITE,
    SANDBOX_EVENT_WRITE,
    SANDBOX_INPUT_READ,
    SANDBOX_OUTPUT_WRITE,
    SANDBOX_PROGRESS_WRITE,
    SANDBOX_TOOL_EXECUTE,
    SandboxJobCapability,
    require_sandbox_job_capability,
)
from src.services.builder.agent_turns import (
    BuilderAgentTurnService,
    BuilderTurnCompletionFenced,
)
from src.services.builder.build_completion import (
    BuildCompletionConflict,
    BuildCompletionInvalid,
    BuildCompletionMissing,
    complete_build_attempt,
)
from src.services.builder.build_plane import BuildPlaneUnavailable
from src.services.builder.build_requests import BuildFailed
from src.services.builder.fs_tools import WorkspaceViolation
from src.services.builder.runtime_authorization import (
    BuilderRuntimeForbidden,
    authorize_builder_project,
)
from src.services.builder.solution_build_check import (
    SolutionBuildCheckError,
    bounded_build_log_excerpt,
    model_visible_build_failure,
    test_solution_workspace_build,
)
from src.services.builder.staged_artifacts import (
    BuildOutputTooLarge,
    StagedBuildArtifactStorage,
)
from src.services.builder.turn_artifacts import (
    BuilderTurnArtifactStorage,
    BuilderTurnOutputTooLarge,
)
from src.services.builder.turns import (
    BuilderProjectMissing,
    BuilderTurnConflict,
    WorkspaceInvalid,
)
from src.services.builder.workspace_archives import BuilderWorkspaceArchiveSource
from src.services.chat_attachments import (
    ChatAttachmentError,
    ChatAttachmentService,
    is_binary_model_input,
    validate_model_input_capabilities,
)
from src.services.execution.agent_helpers import build_agent_system_prompt
from src.services.llm import get_llm_client
from src.services.sandbox_runner_config import SandboxRunnerConfigService
from src.services.builder.workspace_tool_runtime import (
    CLOUDFLARE_WORKSPACE_COMMAND_TOOL_ID,
    TEST_SOLUTION_BUILD_TOOL_ID,
    error_result,
    success_result,
)
from src.services.mcp_server.tools.builder_workspace import BUILDER_WORKSPACE_TOOL_IDS
from src.services.solutions.access import SolutionAction
from shared.builder_workspace_archive import hydrate_builder_turn_workspace

router = APIRouter(
    prefix="/api/internal/sandbox/jobs",
    tags=["internal-sandbox-jobs"],
    include_in_schema=False,
)
Db = Annotated[AsyncSession, Depends(get_db)]


async def _capability(
    job_id: UUID,
    db: Db,
    authorization: Annotated[str | None, Header()] = None,
) -> SandboxJobCapability:
    return await require_sandbox_job_capability(job_id, db, authorization)


Capability = Annotated[SandboxJobCapability, Depends(_capability)]


def _assistant_message_id(turn_id: UUID, dispatch_attempt: int) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"bifrost-builder:{turn_id}:attempt:{dispatch_attempt}:assistant",
    )


def _sandbox_tool_execution(tool_name: str) -> Literal["sandbox", "bifrost"]:
    """Classify where an external runner should execute one Builder tool."""

    sandbox_tools = set(BUILDER_WORKSPACE_TOOL_IDS) | {
        CLOUDFLARE_WORKSPACE_COMMAND_TOOL_ID,
        TEST_SOLUTION_BUILD_TOOL_ID,
    }
    return "sandbox" if tool_name in sandbox_tools else "bifrost"


async def _cloudflare_workspace_command_enabled(db: AsyncSession) -> bool:
    config = await SandboxRunnerConfigService(db).get_config()
    return bool(config and config.provider == "cloudflare")


async def _with_cloudflare_workspace_command(
    db: AsyncSession,
    tool_definitions: list[SandboxBuilderToolDefinition],
) -> list[SandboxBuilderToolDefinition]:
    if await _cloudflare_workspace_command_enabled(db) and not any(
        definition.name == CLOUDFLARE_WORKSPACE_COMMAND_TOOL_ID
        for definition in tool_definitions
    ):
        return [
            *tool_definitions,
            _cloudflare_workspace_command_definition(),
        ]
    return tool_definitions


def _cloudflare_workspace_command_definition() -> SandboxBuilderToolDefinition:
    return SandboxBuilderToolDefinition(
        name=CLOUDFLARE_WORKSPACE_COMMAND_TOOL_ID,
        description=(
            "Run one bounded argv-only command inside the isolated, secretless "
            "Builder workspace sandbox. No shell is used; network is disabled "
            "unless this job explicitly allows hosts."
        ),
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 4096},
                    "minItems": 1,
                    "maxItems": 64,
                    "description": "Executable and arguments. Shell syntax is not accepted.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional workspace-relative directory.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 60,
                    "description": "Command timeout, capped at 60 seconds.",
                },
            },
            "required": ["argv"],
        },
        execution="sandbox",
    )


async def _load_turn_runtime(
    db: AsyncSession,
    job_id: UUID,
) -> tuple[
    SolutionBuilderTurn,
    SolutionBuilderSession,
    Conversation,
    AgentExecutionProfile,
]:
    turn = await db.get(SolutionBuilderTurn, job_id)
    if turn is None or turn.status not in {"queued", "running", "cancelled"}:
        raise HTTPException(status_code=409, detail="Builder turn is not running")
    session = await _turn_session(db, turn)
    conversation = (
        await db.execute(
            select(Conversation)
            .where(Conversation.id == session.conversation_id)
            .options(selectinload(Conversation.user))
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=409, detail="Builder conversation is unavailable")
    project = await db.get(SolutionBuilderProject, session.solution_id)
    if project is None:
        raise HTTPException(status_code=409, detail="Builder project is unavailable")
    if turn.requested_by is None:
        raise HTTPException(status_code=409, detail="Builder requester is unavailable")
    required_capabilities = ["builder.execute"]
    if project.target_kind == "solution":
        required_capabilities.extend(
            [
                "solutions.readwrite",
                "solutions.build.execute",
                "solutions.deploy.execute",
            ]
        )
    try:
        authorized = await authorize_builder_project(
            db,
            solution_id=session.solution_id,
            requester_user_id=turn.requested_by,
            action=SolutionAction.EDIT,
            required_capabilities=tuple(required_capabilities),
        )
    except BuilderRuntimeForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    profile = build_builder_runtime_profile(
        authorized.solution,
        target_kind=authorized.project.target_kind,
        authorization=authorized.authorization,
    )
    return turn, session, conversation, profile


async def _resolved_turn_tools(
    db: AsyncSession,
    *,
    agent: AgentExecutionProfile,
    conversation: Conversation,
):
    from src.core.database import get_session_factory
    from src.services.agent_executor import AgentExecutor
    from src.services.chat_artifacts import artifact_tool_definitions
    from src.services.llm_config_service import LLMConfigService

    public_config = await LLMConfigService(db).get_config()
    if public_config is None:
        raise HTTPException(status_code=409, detail="Builder model is not configured")
    model_capabilities = public_config.resolve_builder_capabilities()
    executor = AgentExecutor(get_session_factory(), model_profile="builder")
    definitions = await executor._get_agent_tools(  # noqa: SLF001 - shared internal boundary
        agent,
        caller_user_id=conversation.user_id,
    )
    if not model_capabilities.tool_calling:
        definitions = []
    else:
        existing = {definition.name for definition in definitions}
        definitions.extend(
            definition
            for definition in artifact_tool_definitions(
                image_generation_enabled=bool(
                    public_config.image_generation_model
                ),
                video_generation_enabled=bool(
                    public_config.video_generation_model
                ),
            )
            if definition.name not in existing
        )
    return executor, public_config, definitions


async def _broadcast_chunks(
    conversation_id: UUID,
    chunks: list[ChatStreamChunk],
) -> None:
    from src.core.pubsub import manager

    for chunk in chunks:
        payload = chunk.model_dump(mode="json", exclude_none=True)
        payload["conversation_id"] = str(conversation_id)
        await manager.broadcast(f"chat:{conversation_id}", payload)


def _require_build(capability: SandboxJobCapability) -> None:
    if capability.job_type != "solution.build":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This callback is not valid for the sandbox job type",
        )


def _require_turn(capability: SandboxJobCapability) -> None:
    if capability.job_type != "solution.builder.turn":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This callback is not valid for the sandbox job type",
        )


@router.get("/{job_id}/input")
async def get_input(
    job_id: UUID,
    db: Db,
    capability: Capability,
) -> StreamingResponse:
    capability.require(SANDBOX_INPUT_READ)
    if capability.job_type == "solution.build":
        build_job = await db.get(SolutionBuildJob, job_id)
        if build_job is None or build_job.status != "running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Build job is not running",
            )
        return StreamingResponse(
            StagedBuildArtifactStorage(job_id).open_input_stream(),
            media_type="application/zip",
        )
    _require_turn(capability)
    turn = await db.get(SolutionBuilderTurn, job_id)
    if turn is None or turn.status not in {"queued", "running"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Builder turn is not running",
        )
    if turn.base_revision_id is None:
        raise HTTPException(status_code=409, detail="Builder turn has no base revision")
    session = await _turn_session(db, turn)
    source = await _turn_archive_source(db, turn, session)
    return StreamingResponse(
        source.iter_chunks(),
        media_type="application/zip",
    )


async def _turn_archive_source(
    db: AsyncSession,
    turn: SolutionBuilderTurn,
    session: SolutionBuilderSession,
) -> BuilderWorkspaceArchiveSource:
    if turn.resume_from_turn_id is not None:
        resume_from = await db.get(SolutionBuilderTurn, turn.resume_from_turn_id)
        if (
            resume_from is None
            or resume_from.session_id != session.id
            or resume_from.checkpoint_sha256 is None
        ):
            raise HTTPException(status_code=409, detail="Builder checkpoint is missing")
        return BuilderWorkspaceArchiveSource(
            kind="checkpoint",
            solution_id=session.solution_id,
            session_id=session.id,
            archive_id=resume_from.id,
            expected_sha256=resume_from.checkpoint_sha256,
        )
    base = await db.get(SolutionSourceRevision, turn.base_revision_id)
    if base is None or base.solution_id != session.solution_id:
        raise HTTPException(status_code=409, detail="Builder base revision is missing")
    return BuilderWorkspaceArchiveSource(
        kind="revision",
        solution_id=session.solution_id,
        session_id=session.id,
        archive_id=turn.base_revision_id,
        expected_sha256=base.source_sha256,
    )


@router.get(
    "/{job_id}/context",
    response_model=SandboxBuilderTurnContext,
)
async def get_turn_context(
    job_id: UUID,
    db: Db,
    capability: Capability,
) -> SandboxBuilderTurnContext:
    capability.require(SANDBOX_INPUT_READ)
    _require_turn(capability)
    turn, session, conversation, agent = await _load_turn_runtime(db, job_id)
    if turn.base_revision_id is None or turn.user_message_id is None:
        raise HTTPException(status_code=409, detail="Builder turn context is incomplete")
    executor, public_config, definitions = await _resolved_turn_tools(
        db,
        agent=agent,
        conversation=conversation,
    )
    del executor
    llm_client = await get_llm_client(db)
    recent = (
        (
            await db.execute(
                select(Message)
                .options(selectinload(Message.attachments))
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.sequence.desc())
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    try:
        model_capabilities = public_config.resolve_builder_capabilities()
        validate_model_input_capabilities(
            [
                attachment
                for message in recent
                for attachment in message.attachments
            ],
            image_input=model_capabilities.image_input,
            pdf_input=model_capabilities.pdf_input,
            model_label="Builder model",
        )
    except ChatAttachmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    messages = [
        SandboxBuilderMessage(
            id=message.id,
            sequence=message.sequence,
            role=message.role.value,
            content=message.content,
            tool_calls=message.tool_calls,
            tool_call_id=message.tool_call_id,
            tool_name=message.tool_name,
            tool_input=message.tool_input,
            attachments=[
                SandboxBuilderAttachment(
                    id=attachment.artifact_id,
                    filename=attachment.filename,
                    content_type=attachment.content_type,
                    size_bytes=attachment.size_bytes,
                    extracted_text=attachment.extracted_text,
                    binary_model_input=is_binary_model_input(
                        attachment.content_type
                    ),
                )
                for attachment in message.attachments
            ],
        )
        for message in reversed(recent)
    ]
    model = public_config.resolve_builder_model()
    from src.services.agent_runtime import AgentRunBudget
    from src.services.agent_runtime.usage_governance import (
        build_runtime_usage_governance,
        runtime_usage_organization_id,
        runtime_usage_subject,
    )

    budget = AgentRunBudget(
        max_requests=agent.max_iterations or 50,
        max_total_tokens=agent.max_token_budget or 100_000,
    )
    runtime_organization_id = runtime_usage_organization_id(
        resource_organization_id=agent.organization_id,
        requester_organization_id=(
            conversation.user.organization_id if conversation.user is not None else None
        ),
        target_kind=getattr(agent, "target_kind", None),
    )
    usage_governance = await build_runtime_usage_governance(
        db,
        runtime_usage_subject(
            organization_id=runtime_organization_id,
            user_id=conversation.user_id,
            solution_id=session.solution_id,
        ),
    )
    budget = await usage_governance.constrain_budget(db, budget)
    tool_definitions = [
        SandboxBuilderToolDefinition(
            name=definition.name,
            description=definition.description,
            parameters=(
                sanitize_builder_tool_parameters(definition.parameters)
                if getattr(agent, "target_kind", None)
                in {"global_repo", "organization"}
                else definition.parameters
            ),
            execution=_sandbox_tool_execution(definition.name),
        )
        for definition in definitions
    ]
    tool_definitions = await _with_cloudflare_workspace_command(
        db,
        tool_definitions,
    )

    return SandboxBuilderTurnContext(
        solution_id=str(session.solution_id),
        session_id=str(session.id),
        turn_id=str(turn.id),
        conversation_id=str(conversation.id),
        user_message_id=turn.user_message_id,
        assistant_message_id=_assistant_message_id(
            turn.id,
            capability.dispatch_attempt,
        ),
        base_revision_id=str(turn.base_revision_id),
        system_prompt=build_agent_system_prompt(
            agent,
            execution_context={"mode": "chat"},
        ),
        bundle_path=agent.bundle_path,
        llm_config=SandboxBuilderModelConfig(
            provider=llm_client.config.provider,
            model=model,
            api_key=llm_client.config.api_key,
            endpoint=llm_client.config.endpoint,
            max_tokens=agent.llm_max_tokens or llm_client.config.max_tokens,
            extra_params=llm_client.config.extra_params,
        ),
        max_iterations=budget.max_requests if budget.max_requests is not None else 50,
        max_token_budget=(
            budget.max_total_tokens if budget.max_total_tokens is not None else 100_000
        ),
        runtime_governance=usage_governance.runner_snapshot(),
        tools=tool_definitions,
        messages=messages,
    )


@router.get("/{job_id}/attachments/{attachment_id}")
async def get_turn_attachment(
    job_id: UUID,
    attachment_id: UUID,
    db: Db,
    capability: Capability,
) -> Response:
    capability.require(SANDBOX_ATTACHMENT_READ)
    _require_turn(capability)
    _turn, _session, conversation, _agent = await _load_turn_runtime(db, job_id)
    attachment = (
        await db.execute(
            select(MessageAttachment).where(
                MessageAttachment.artifact_id == attachment_id,
                MessageAttachment.conversation_id == conversation.id,
            )
        )
    ).scalar_one_or_none()
    if attachment is None:
        raise HTTPException(status_code=404, detail="Builder attachment not found")
    loaded = await ChatAttachmentService(db).load_binary_input(attachment)
    return Response(content=loaded.data, media_type=loaded.content_type)


@router.post("/{job_id}/events", status_code=status.HTTP_204_NO_CONTENT)
async def post_turn_events(
    job_id: UUID,
    body: SandboxBuilderEventBatch,
    db: Db,
    capability: Capability,
) -> Response:
    capability.require(SANDBOX_EVENT_WRITE)
    _require_turn(capability)
    turn, _session, conversation, _agent = await _load_turn_runtime(db, job_id)
    chunks: list[ChatStreamChunk] = []
    for raw_event in body.events:
        chunk = ChatStreamChunk.model_validate(raw_event)
        if chunk.type not in {"message_start", "delta", "context_warning"}:
            raise HTTPException(
                status_code=422,
                detail=f"Sandbox cannot publish {chunk.type!r} events directly",
            )
        if chunk.type == "message_start" and (
            chunk.user_message_id != str(turn.user_message_id)
            or chunk.assistant_message_id
            != str(_assistant_message_id(turn.id, capability.dispatch_attempt))
        ):
            raise HTTPException(status_code=422, detail="Invalid Builder message IDs")
        chunks.append(chunk)
    await _broadcast_chunks(conversation.id, chunks)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{job_id}/assistant-segments")
async def post_assistant_segment(
    job_id: UUID,
    body: SandboxBuilderTextSegment,
    db: Db,
    capability: Capability,
) -> dict[str, str]:
    capability.require(SANDBOX_EVENT_WRITE)
    _require_turn(capability)
    _turn, _session, conversation, agent = await _load_turn_runtime(db, job_id)
    executor, public_config, _definitions = await _resolved_turn_tools(
        db,
        agent=agent,
        conversation=conversation,
    )
    message = await executor.save_assistant_segment(
        conversation=conversation,
        content=body.content,
        model=public_config.resolve_builder_model(),
    )
    await _broadcast_chunks(
        conversation.id,
        [
            ChatStreamChunk(
                type="assistant_message_end",
                message_id=str(message.id),
            )
        ],
    )
    return {"message_id": str(message.id)}


def _tool_identity(
    turn_id: UUID,
    dispatch_attempt: int,
    tool_call_id: str,
) -> tuple[UUID, str]:
    base = f"bifrost-builder:{turn_id}:attempt:{dispatch_attempt}:tool:{tool_call_id}"
    return uuid5(NAMESPACE_URL, base + ":message"), str(
        uuid5(NAMESPACE_URL, base + ":execution")
    )


async def _current_workspace_build_message(
    db: AsyncSession,
    *,
    conversation: Conversation,
    message_id: UUID,
    execution_id: UUID,
) -> Message:
    message = await db.get(Message, message_id)
    if (
        message is None
        or message.conversation_id != conversation.id
        or message.execution_id != str(execution_id)
        or message.role != MessageRole.TOOL_CALL
        or message.tool_name != TEST_SOLUTION_BUILD_TOOL_ID
        or not message.tool_call_id
        or message.tool_state in {"completed", "error"}
    ):
        raise HTTPException(status_code=409, detail="Tool call is not current")
    return message


@router.post(
    "/{job_id}/tools/start",
    response_model=SandboxBuilderToolResponse,
)
async def start_turn_tool(
    job_id: UUID,
    body: SandboxBuilderToolStart,
    db: Db,
    capability: Capability,
) -> SandboxBuilderToolResponse:
    capability.require(SANDBOX_TOOL_EXECUTE)
    _require_turn(capability)
    turn, _session, conversation, agent = await _load_turn_runtime(db, job_id)
    executor, _public_config, definitions = await _resolved_turn_tools(
        db,
        agent=agent,
        conversation=conversation,
    )
    definitions = await _with_cloudflare_workspace_command(db, definitions)
    definition = next(
        (definition for definition in definitions if definition.name == body.name),
        None,
    )
    if definition is None:
        raise HTTPException(status_code=403, detail="Tool is not available to this agent")
    arguments = bind_builder_tool_arguments(
        body.arguments,
        parameters=definition.parameters,
        target_kind=getattr(agent, "target_kind", None),
        organization_id=getattr(agent, "organization_id", None),
        authorization_boundary=getattr(agent, "authorization_boundary", None),
    )
    execution = _sandbox_tool_execution(body.name)
    message_id, execution_id = _tool_identity(
        turn.id,
        capability.dispatch_attempt,
        body.tool_call_id,
    )
    existing = await db.get(Message, message_id)
    tool_call = ToolCall(
        id=body.tool_call_id,
        name=body.name,
        arguments=arguments,
    )
    if existing is None:
        persisted_id, persisted_execution_id, chunks = await executor.begin_tool_call(
            conversation=conversation,
            tool_call=tool_call,
            execution_id=execution_id,
            message_id=message_id,
        )
        assert persisted_id == message_id
        assert persisted_execution_id == execution_id
        await _broadcast_chunks(conversation.id, chunks)
    elif (
        existing.conversation_id != conversation.id
        or existing.tool_call_id != body.tool_call_id
        or existing.tool_name != body.name
        or existing.execution_id != execution_id
    ):
        raise HTTPException(status_code=409, detail="Tool call identity conflict")
    elif existing.tool_state in {"completed", "error"}:
        result_message = (
            await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.role == MessageRole.TOOL,
                    Message.execution_id == execution_id,
                )
                .order_by(Message.sequence.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return SandboxBuilderToolResponse(
            execution=execution,
            message_id=message_id,
            execution_id=execution_id,
            model_content=result_message.content if result_message else None,
            result=existing.tool_result,
            error=(
                str(existing.tool_result.get("error"))
                if existing.tool_state == "error"
                and isinstance(existing.tool_result, dict)
                and existing.tool_result.get("error")
                else None
            ),
            duration_ms=existing.duration_ms,
        )

    if execution == "sandbox":
        return SandboxBuilderToolResponse(
            execution="sandbox",
            message_id=message_id,
            execution_id=execution_id,
        )

    user = conversation.user
    caller = (
        {
            "user_id": str(user.id),
            "email": user.email,
            "name": user.name,
            "organization_id": (
                str(user.organization_id) if user.organization_id else None
            ),
            "is_platform_admin": user.is_superuser,
        }
        if user is not None
        else None
    )
    result, model_content, chunks = await executor.execute_started_tool_call(
        agent=agent,
        conversation=conversation,
        tool_call=tool_call,
        message_id=message_id,
        execution_id=execution_id,
        caller_user_id=conversation.user_id,
        caller=caller,
    )
    await _broadcast_chunks(conversation.id, chunks)
    return SandboxBuilderToolResponse(
        execution="bifrost",
        message_id=message_id,
        execution_id=execution_id,
        model_content=model_content,
        result=result.result,
        error=result.error,
        duration_ms=result.duration_ms,
    )


@router.post(
    "/{job_id}/tools/finish",
    response_model=SandboxBuilderToolResponse,
)
async def finish_turn_tool(
    job_id: UUID,
    body: SandboxBuilderToolFinish,
    db: Db,
    capability: Capability,
) -> SandboxBuilderToolResponse:
    capability.require(SANDBOX_TOOL_EXECUTE)
    _require_turn(capability)
    _turn, _session, conversation, agent = await _load_turn_runtime(db, job_id)
    message = await db.get(Message, body.message_id)
    if (
        message is None
        or message.conversation_id != conversation.id
        or message.execution_id != body.execution_id
        or message.role.value != "tool_call"
        or not message.tool_call_id
        or not message.tool_name
    ):
        raise HTTPException(status_code=409, detail="Tool call is not current")
    if message.tool_name not in (
        set(BUILDER_WORKSPACE_TOOL_IDS)
        | {CLOUDFLARE_WORKSPACE_COMMAND_TOOL_ID, TEST_SOLUTION_BUILD_TOOL_ID}
    ):
        raise HTTPException(status_code=403, detail="Tool must execute in Bifrost")
    executor, _public_config, definitions = await _resolved_turn_tools(
        db,
        agent=agent,
        conversation=conversation,
    )
    if message.tool_name == CLOUDFLARE_WORKSPACE_COMMAND_TOOL_ID:
        if not await _cloudflare_workspace_command_enabled(db):
            raise HTTPException(
                status_code=403,
                detail="Tool is unavailable for the current runner",
            )
        definitions = await _with_cloudflare_workspace_command(db, definitions)
    if not any(definition.name == message.tool_name for definition in definitions):
        raise HTTPException(status_code=403, detail="Tool is not available to this agent")
    if message.tool_state in {"completed", "error"}:
        result_message = (
            await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.role == MessageRole.TOOL,
                    Message.execution_id == body.execution_id,
                )
                .order_by(Message.sequence.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return SandboxBuilderToolResponse(
            execution="sandbox",
            message_id=message.id,
            execution_id=body.execution_id,
            model_content=result_message.content if result_message else None,
            result=message.tool_result,
            error=(
                str(message.tool_result.get("error"))
                if message.tool_state == "error"
                and isinstance(message.tool_result, dict)
                and message.tool_result.get("error")
                else None
            ),
            duration_ms=message.duration_ms,
        )
    tool_call = ToolCall(
        id=message.tool_call_id,
        name=message.tool_name,
        arguments=message.tool_input or {},
    )
    result = ToolResult(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        result=body.result,
        error=body.error,
        duration_ms=body.duration_ms,
    )
    model_content, chunks = await executor.complete_tool_call(
        agent=agent,
        conversation=conversation,
        tool_call=tool_call,
        message_id=message.id,
        execution_id=body.execution_id,
        tool_result=result,
    )
    await _broadcast_chunks(conversation.id, chunks)
    return SandboxBuilderToolResponse(
        execution="sandbox",
        message_id=message.id,
        execution_id=body.execution_id,
        model_content=model_content,
        result=result.result,
        error=result.error,
        duration_ms=result.duration_ms,
    )


@router.put("/{job_id}/tools/{execution_id}/workspace")
async def put_tool_workspace(
    job_id: UUID,
    execution_id: UUID,
    message_id: UUID,
    request: Request,
    db: Db,
    capability: Capability,
) -> dict[str, str | int]:
    """Stage the exact workspace used by one sandbox build-check tool call."""

    capability.require(SANDBOX_TOOL_EXECUTE)
    capability.require(SANDBOX_OUTPUT_WRITE)
    _require_turn(capability)
    _turn, _session, conversation, _agent = await _load_turn_runtime(db, job_id)
    await _current_workspace_build_message(
        db,
        conversation=conversation,
        message_id=message_id,
        execution_id=execution_id,
    )
    try:
        digest, size = await BuilderTurnArtifactStorage(
            job_id,
            capability.dispatch_attempt,
        ).write_tool_workspace(
            execution_id,
            request.stream(),
            max_bytes=get_settings().builder_output_limit_bytes,
        )
    except BuilderTurnOutputTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return {"sha256": digest, "size": size}


@router.post(
    "/{job_id}/tools/{execution_id}/workspace-build",
    response_model=SandboxBuilderWorkspaceBuildResult,
)
async def test_tool_workspace_build(
    job_id: UUID,
    execution_id: UUID,
    body: SandboxBuilderWorkspaceBuildRequest,
    db: Db,
    capability: Capability,
) -> SandboxBuilderWorkspaceBuildResult:
    """Compile one fenced sandbox snapshot through the canonical build plane."""

    capability.require(SANDBOX_TOOL_EXECUTE)
    capability.require(SANDBOX_OUTPUT_WRITE)
    _require_turn(capability)
    turn, session, conversation, _agent = await _load_turn_runtime(db, job_id)
    await _current_workspace_build_message(
        db,
        conversation=conversation,
        message_id=body.message_id,
        execution_id=execution_id,
    )
    platform_job = await db.get(PlatformJob, job_id)
    if platform_job is None or platform_job.status not in {"running", "waiting"}:
        raise HTTPException(status_code=409, detail="Builder turn is no longer running")
    if turn.requested_by is None:
        raise HTTPException(status_code=409, detail="Builder requester is unavailable")

    artifacts = BuilderTurnArtifactStorage(job_id, capability.dispatch_attempt)
    with tempfile.TemporaryDirectory(prefix="bifrost-builder-tool-build-") as tmp:
        workspace = Path(tmp) / "workspace"
        try:
            await hydrate_builder_turn_workspace(
                artifacts.iter_tool_workspace(execution_id),
                expected_sha256=body.output_sha256,
                destination=workspace,
                solution_id=session.solution_id,
            )
        except (FileNotFoundError, ValueError, WorkspaceViolation) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        try:
            checked = await test_solution_workspace_build(
                workspace,
                solution_id=session.solution_id,
                requested_by=turn.requested_by,
            )
        except BuildFailed as exc:
            result = error_result(
                model_visible_build_failure(exc),
                {
                    "build_job_id": str(exc.job_id),
                    "build_status": exc.status,
                    "build_log_excerpt": bounded_build_log_excerpt(exc),
                },
            )
        except (BuildPlaneUnavailable, SolutionBuildCheckError, TimeoutError) as exc:
            result = error_result(str(exc))
        else:
            data = checked.as_dict()
            result = success_result(
                f"Production build passed for {data['compiled_app_count']} source app(s).",
                data,
            )

    return SandboxBuilderWorkspaceBuildResult(
        content=result.content,
        structured_content=result.structured_content,
    )


@router.put("/{job_id}/output")
async def put_turn_output(
    job_id: UUID,
    request: Request,
    db: Db,
    capability: Capability,
) -> dict[str, str | int]:
    capability.require(SANDBOX_OUTPUT_WRITE)
    _require_turn(capability)
    turn = await db.get(SolutionBuilderTurn, job_id)
    if turn is None or turn.status not in {"queued", "running", "cancelled"}:
        raise HTTPException(status_code=409, detail="Builder turn is not running")
    try:
        digest, size = await BuilderTurnArtifactStorage(
            job_id,
            capability.dispatch_attempt,
        ).write_output(
            request.stream(),
            max_bytes=get_settings().builder_output_limit_bytes,
        )
    except BuilderTurnOutputTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return {"sha256": digest, "size": size}


async def _turn_session(
    db: AsyncSession,
    turn: SolutionBuilderTurn,
) -> SolutionBuilderSession:
    session = await db.get(SolutionBuilderSession, turn.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Builder session not found")
    return session


@router.put("/{job_id}/artifacts/{rel_path:path}")
async def put_artifact(
    job_id: UUID,
    rel_path: str,
    request: Request,
    db: Db,
    capability: Capability,
) -> dict[str, str | int]:
    capability.require(SANDBOX_ARTIFACT_WRITE)
    _require_build(capability)
    build_job = await db.get(SolutionBuildJob, job_id)
    if build_job is None or build_job.status != "running" or build_job.app_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Build job is not running",
        )
    try:
        digest, size = await StagedBuildArtifactStorage(job_id).write_output(
            build_job.app_id,
            rel_path,
            request.stream(),
            get_settings().builder_output_limit_bytes,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except BuildOutputTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        ) from exc
    return {"sha256": digest, "size": size}


@router.post("/{job_id}/progress", status_code=status.HTTP_204_NO_CONTENT)
async def progress(
    job_id: UUID,
    body: SandboxJobProgressUpdate,
    db: Db,
    capability: Capability,
) -> Response:
    capability.require(SANDBOX_PROGRESS_WRITE)
    build_job = await db.get(SolutionBuildJob, job_id)
    if capability.job_type == "solution.build":
        if build_job is None or build_job.status != "running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Build job is not running",
            )
        build_job.last_progress_at = datetime.now(timezone.utc)
        await db.commit()

    from src.services.platform_jobs import update_external_platform_job_progress

    updated = await update_external_platform_job_progress(
        job_id,
        capability.dispatch_attempt,
        phase=body.phase,
        current=body.current,
        total=body.total,
        percent=body.percent,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sandbox job is no longer running",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{job_id}/cancelled", response_model=SandboxJobCancelled)
async def cancelled(
    job_id: UUID,
    db: Db,
    capability: Capability,
) -> SandboxJobCancelled:
    capability.require(SANDBOX_CANCEL_READ)
    job = await db.get(PlatformJob, job_id)
    return SandboxJobCancelled(
        cancelled=job is None or job.status in {"cancel_requested", "cancelled"}
    )


@router.post("/{job_id}/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete_sandbox_job(
    job_id: UUID,
    body: dict[str, object],
    db: Db,
    capability: Capability,
) -> Response:
    try:
        if capability.job_type == "solution.build":
            return await complete_build(
                job_id,
                BuildJobStatusUpdate.model_validate(body),
                db,
                capability,
            )
        _require_turn(capability)
        return await complete_turn(
            job_id,
            SandboxBuilderTurnCompletion.model_validate(body),
            db,
            capability,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def complete_build(
    job_id: UUID,
    body: BuildJobStatusUpdate,
    db: Db,
    capability: Capability,
) -> Response:
    capability.require(SANDBOX_COMPLETE_WRITE)
    _require_build(capability)
    try:
        await complete_build_attempt(
            db,
            job_id=job_id,
            dispatch_attempt=capability.dispatch_attempt,
            update=body,
        )
    except BuildCompletionMissing as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BuildCompletionInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BuildCompletionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def complete_turn(
    job_id: UUID,
    body: SandboxBuilderTurnCompletion,
    db: AsyncSession,
    capability: SandboxJobCapability,
) -> Response:
    capability.require(SANDBOX_COMPLETE_WRITE)
    _require_turn(capability)
    platform_job = await db.get(PlatformJob, job_id)
    turn = await db.get(SolutionBuilderTurn, job_id)
    if platform_job is None or turn is None:
        raise HTTPException(status_code=404, detail="Builder turn not found")
    service = BuilderAgentTurnService(db)
    if platform_job.status in {"succeeded", "failed", "cancelled"}:
        if platform_job.status == body.status:
            if (
                body.checkpoint_output_sha256 is not None
                and turn.checkpoint_sha256 is None
            ):
                await service.preserve_agent_turn_checkpoint(
                    turn_id=turn.id,
                    dispatch_attempt=capability.dispatch_attempt,
                    output_sha256=body.checkpoint_output_sha256,
                )
                await db.commit()
            return Response(status_code=204)
        raise HTTPException(status_code=409, detail="Builder turn already completed")
    if platform_job.status == "cancel_requested" and body.status != "cancelled":
        raise HTTPException(status_code=409, detail="Builder turn was cancelled")

    try:
        if body.status == "failed" and body.retryable:
            retried = await service.retry_external_agent_turn(
                turn_id=turn.id,
                dispatch_attempt=capability.dispatch_attempt,
                error=body.error or "External Builder runner failed transiently",
            )
            if retried is not None:
                return Response(status_code=204)
        if body.status == "succeeded":
            assert body.output_sha256 is not None
            assert body.final_text is not None
            _turn, session, conversation, agent = await _load_turn_runtime(db, job_id)
            from src.services.agent_runtime.usage_governance import (
                runtime_usage_organization_id,
            )

            runtime_organization_id = runtime_usage_organization_id(
                resource_organization_id=agent.organization_id,
                requester_organization_id=(
                    conversation.user.organization_id
                    if conversation.user is not None
                    else None
                ),
                target_kind=getattr(agent, "target_kind", None),
            )
            assistant_message_id = _assistant_message_id(
                turn.id,
                capability.dispatch_attempt,
            )
            if (
                body.assistant_message_id is not None
                and body.assistant_message_id != assistant_message_id
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Invalid Builder assistant message ID",
                )
            await service.finalize_agent_turn(
                session.solution_id,
                turn_id=turn.id,
                dispatch_attempt=capability.dispatch_attempt,
                output_sha256=body.output_sha256,
                final_text=body.final_text,
                tool_call_count=body.tool_call_count,
                model_request_count=body.model_request_count,
                model=body.model,
                token_count_input=body.token_count_input,
                token_count_output=body.token_count_output,
                assistant_message_id=assistant_message_id,
                duration_ms=body.duration_ms,
                harness_diagnostics=(
                    body.harness_diagnostics.model_dump(mode="json")
                    if body.harness_diagnostics is not None
                    else None
                ),
            )
            if body.provider and body.model:
                from src.core.database import get_session_factory
                from src.services.agent_executor import AgentExecutor

                try:
                    await AgentExecutor(get_session_factory()).record_usage(
                        provider=body.provider,
                        model=body.model,
                        input_tokens=body.token_count_input or 0,
                        output_tokens=body.token_count_output or 0,
                        cache_read_tokens=body.cache_read_tokens,
                        cache_write_tokens=body.cache_write_tokens,
                        provider_cost=body.provider_cost,
                        model_requests=body.model_request_count,
                        duration_ms=body.duration_ms,
                        conversation_id=conversation.id,
                        message_id=assistant_message_id,
                        organization_id=runtime_organization_id,
                        user_id=conversation.user_id,
                        solution_id=session.solution_id,
                    )
                    from src.services.usage_limits import (
                        PortableUsage,
                        UsageLimitSubject,
                        record_supported_period_usage,
                    )

                    await record_supported_period_usage(
                        db,
                        UsageLimitSubject(
                            organization_id=runtime_organization_id,
                            user_id=conversation.user_id,
                            solution_id=session.solution_id,
                        ),
                        PortableUsage(
                            runner_duration_ms=body.duration_ms or 0,
                            sandbox_compute_ms=body.sandbox_compute_ms or 0,
                        ),
                    )
                except Exception:
                    import logging

                    logging.getLogger(__name__).warning(
                        "Failed to record remote Builder AI usage",
                        exc_info=True,
                    )
            await _broadcast_chunks(
                conversation.id,
                [
                    ChatStreamChunk(
                        type="done",
                        content=body.final_text or None,
                        message_id=str(assistant_message_id),
                        token_count_input=body.token_count_input,
                        token_count_output=body.token_count_output,
                        duration_ms=body.duration_ms,
                    )
                ],
            )
        else:
            _turn, _session, conversation, _agent = await _load_turn_runtime(
                db,
                job_id,
            )
            await service.finish_failed_agent_turn(
                turn_id=turn.id,
                dispatch_attempt=capability.dispatch_attempt,
                status=body.status,
                error=body.error,
                harness_diagnostics=(
                    body.harness_diagnostics.model_dump(mode="json")
                    if body.harness_diagnostics is not None
                    else None
                ),
                checkpoint_output_sha256=body.checkpoint_output_sha256,
            )
            await _broadcast_chunks(
                conversation.id,
                [
                    ChatStreamChunk(
                        type="error",
                        error=body.error or "Builder turn was cancelled",
                    )
                ],
            )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="Builder output was not uploaded") from exc
    except (WorkspaceInvalid, WorkspaceViolation) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (BuilderProjectMissing, BuilderTurnConflict, BuilderTurnCompletionFenced) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


__all__ = ["router"]
