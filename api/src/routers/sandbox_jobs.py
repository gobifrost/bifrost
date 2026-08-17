"""Job-bound callback API used by external sandbox runner attempts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
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
    SandboxJobCancelled,
    SandboxJobProgressUpdate,
)
from src.models.contracts.agents import ChatStreamChunk, ToolCall, ToolResult
from src.models.enums import MessageRole
from src.models.orm.agents import Agent, Conversation, Message, MessageAttachment
from src.models.contracts.solution_builder import BuildJobStatusUpdate
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_build_jobs import SolutionBuildJob
from src.models.orm.solution_builder import (
    SolutionBuilderSession,
    SolutionBuilderTurn,
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
from src.services.builder.fs_tools import WorkspaceViolation
from src.services.builder.revision_storage import SolutionRevisionStorage
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
from src.services.chat_attachments import (
    ChatAttachmentError,
    ChatAttachmentService,
    is_binary_model_input,
    validate_model_input_capabilities,
)
from src.services.execution.agent_helpers import build_agent_system_prompt
from src.services.llm import get_llm_client
from src.services.mcp_server.tools.builder_workspace import BUILDER_WORKSPACE_TOOL_IDS

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


async def _load_turn_runtime(
    db: AsyncSession,
    job_id: UUID,
) -> tuple[SolutionBuilderTurn, SolutionBuilderSession, Conversation, Agent]:
    turn = await db.get(SolutionBuilderTurn, job_id)
    if turn is None or turn.status not in {"queued", "running", "cancelled"}:
        raise HTTPException(status_code=409, detail="Builder turn is not running")
    session = await _turn_session(db, turn)
    conversation = (
        await db.execute(
            select(Conversation)
            .where(Conversation.id == session.conversation_id)
            .options(
                selectinload(Conversation.agent).selectinload(Agent.tools),
                selectinload(Conversation.agent).selectinload(
                    Agent.delegated_agents
                ),
                selectinload(Conversation.agent).selectinload(Agent.roles),
                selectinload(Conversation.agent).selectinload(Agent.owner),
                selectinload(Conversation.agent).selectinload(
                    Agent.mcp_connections
                ),
                selectinload(Conversation.user),
            )
        )
    ).scalar_one_or_none()
    if conversation is None or conversation.agent is None:
        raise HTTPException(status_code=409, detail="Builder conversation is unavailable")
    return turn, session, conversation, conversation.agent


async def _resolved_turn_tools(
    db: AsyncSession,
    *,
    agent: Agent,
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
    if turn.resume_from_turn_id is not None:
        resume_from = await db.get(SolutionBuilderTurn, turn.resume_from_turn_id)
        if (
            resume_from is None
            or resume_from.session_id != session.id
            or resume_from.checkpoint_sha256 is None
        ):
            raise HTTPException(status_code=409, detail="Builder checkpoint is missing")
        return StreamingResponse(
            BuilderTurnArtifactStorage(
                resume_from.id,
                1,
            ).iter_checkpoint(
                session.solution_id,
                session.id,
                resume_from.id,
            ),
            media_type="application/zip",
        )
    return StreamingResponse(
        SolutionRevisionStorage(session.solution_id).iter_chunks(turn.base_revision_id),
        media_type="application/zip",
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
    from src.services.mcp_server.tools.skill_assets import READ_SKILL_ASSET_TOOL_ID

    sandbox_tools = frozenset(BUILDER_WORKSPACE_TOOL_IDS) | {
        READ_SKILL_ASSET_TOOL_ID
    }
    model = public_config.resolve_builder_model()
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
        max_iterations=agent.max_iterations or 50,
        max_token_budget=agent.max_token_budget or 100_000,
        tools=[
            SandboxBuilderToolDefinition(
                name=definition.name,
                description=definition.description,
                parameters=definition.parameters,
                execution=(
                    "sandbox" if definition.name in sandbox_tools else "bifrost"
                ),
            )
            for definition in definitions
        ],
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
    definition = next(
        (definition for definition in definitions if definition.name == body.name),
        None,
    )
    if definition is None:
        raise HTTPException(status_code=403, detail="Tool is not available to this agent")
    from src.services.mcp_server.tools.skill_assets import READ_SKILL_ASSET_TOOL_ID

    execution = (
        "sandbox"
        if body.name
        in (frozenset(BUILDER_WORKSPACE_TOOL_IDS) | {READ_SKILL_ASSET_TOOL_ID})
        else "bifrost"
    )
    message_id, execution_id = _tool_identity(
        turn.id,
        capability.dispatch_attempt,
        body.tool_call_id,
    )
    existing = await db.get(Message, message_id)
    tool_call = ToolCall(
        id=body.tool_call_id,
        name=body.name,
        arguments=body.arguments,
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
    from src.services.mcp_server.tools.skill_assets import READ_SKILL_ASSET_TOOL_ID

    if message.tool_name not in (
        frozenset(BUILDER_WORKSPACE_TOOL_IDS) | {READ_SKILL_ASSET_TOOL_ID}
    ):
        raise HTTPException(status_code=403, detail="Tool must execute in Bifrost")
    executor, _public_config, definitions = await _resolved_turn_tools(
        db,
        agent=agent,
        conversation=conversation,
    )
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
                        duration_ms=body.duration_ms,
                        conversation_id=conversation.id,
                        message_id=assistant_message_id,
                        organization_id=agent.organization_id,
                        user_id=conversation.user_id,
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
