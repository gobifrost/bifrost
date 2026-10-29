"""
Chat Router

Chat conversations and messaging for AI agents.
HTTP endpoints for conversations and messages.

For real-time streaming, use the WebSocket endpoint at /ws/connect
(see websocket.py) with chat:{conversation_id} channel subscription.
"""

import json
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.services.authorization.enforce import Caller, load_caller
from src.services.authorization.agent_runtime import (
    AgentRuntimeAuthorizationError,
    authorize_agent_execution,
)
from src.models.contracts.agents import (
    ChatRequest,
    ChatResponse,
    ChatRunCancelResponse,
    ChatRunCreateRequest,
    ChatRunCreateResponse,
    ChatRunStateResponse,
    ChatModelProfilePublic,
    ChatModelProfilesResponse,
    ConversationCreate,
    ConversationPublic,
    ConversationSummary,
    MessagePublic,
    AttachmentPublic,
    AttachmentUploadResponse,
    ChatArtifactPublic,
    ChatArtifactUpdate,
    ToolCall,
)
from src.models.enums import MessageRole
from src.models.orm import Artifact, Agent, Conversation, Message, MessageAttachment
from src.services.ai_model_service import AIModelService
from src.services.chat_attachments import (
    MAX_FILES_PER_MESSAGE,
    ChatAttachmentError,
    ChatAttachmentService,
)
from src.services.chat_runs import (
    cancel_chat_run,
    create_chat_run,
    get_chat_state,
    send_chat_message,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["Chat"])


async def _chat_caller(db: DbSession, user: CurrentActiveUser) -> tuple[Caller, UUID]:
    """Load the original caller for ownership; principal remains effects-only."""
    caller = await load_caller(db, user)
    if caller.ctx is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Chat caller is unavailable")
    return caller, caller.ctx.user_id


def _chat_file_kind(
    message_role: MessageRole | None,
) -> Literal["attachment", "artifact"]:
    """Classify files from their Chat binding, independent of their storage key."""
    return "attachment" if message_role in {None, MessageRole.USER} else "artifact"


@router.get("/model-profiles")
async def get_model_profiles(db: DbSession, user: CurrentActiveUser) -> ChatModelProfilesResponse:
    """Return administrator-governed reusable model profiles available in Chat."""
    del user
    service = AIModelService(db)
    profiles, default_profile_id = await service.list_chat_profiles()
    return ChatModelProfilesResponse(
        profiles=[
            ChatModelProfilePublic(
                id=profile.id,
                name=profile.name,
                label=profile.name,
                capabilities=await service.profile_capabilities(profile),
            )
            for profile in profiles
        ],
        default_profile_id=default_profile_id,
    )


# =============================================================================
# Conversation CRUD
# =============================================================================


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    request: ConversationCreate,
    db: DbSession,
    user: CurrentActiveUser,
) -> ConversationPublic:
    """Create a new conversation, optionally with an agent."""
    caller, owner_id = await _chat_caller(db, user)
    agent = None
    agent_name = None

    # If agent_id provided, verify agent exists and user has access
    if request.agent_id:
        result = await db.execute(
            select(Agent)
            .where(Agent.id == request.agent_id)
            .where(Agent.is_active.is_(True))
        )
        agent = result.scalar_one_or_none()

        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent {request.agent_id} not found",
            )

        try:
            caller, agent = await authorize_agent_execution(db, caller, agent.id)
        except AgentRuntimeAuthorizationError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc

        agent_name = agent.name

    # Create conversation (agent_id can be None for agentless chat)
    conversation_id = uuid4()
    now = datetime.now(timezone.utc)

    conversation = Conversation(
        id=conversation_id,
        agent_id=agent.id if agent else None,
        user_id=owner_id,
        channel=request.channel.value,
        title=request.title,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(conversation)
    await db.flush()

    return ConversationPublic(
        id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=conversation.user_id,
        channel=conversation.channel,
        title=conversation.title,
        is_active=conversation.is_active,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=0,
        agent_name=agent_name,
    )


@router.get("/conversations")
async def list_conversations(
    db: DbSession,
    user: CurrentActiveUser,
    agent_id: UUID | None = None,
    active_only: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[ConversationSummary]:
    """List user's conversations."""
    _, owner_id = await _chat_caller(db, user)
    stmt = (
        select(Conversation)
        .options(selectinload(Conversation.agent))
        .where(Conversation.user_id == owner_id)
    )

    if active_only:
        stmt = stmt.where(Conversation.is_active.is_(True))

    if agent_id:
        stmt = stmt.where(Conversation.agent_id == agent_id)

    stmt = stmt.order_by(Conversation.updated_at.desc()).limit(limit).offset(offset)

    result = await db.execute(stmt)
    conversations = result.scalars().all()

    summaries = []
    for conv in conversations:
        # Get last message preview
        last_msg_result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.sequence.desc())
            .limit(1)
        )
        last_msg = last_msg_result.scalar_one_or_none()

        summaries.append(ConversationSummary(
            id=conv.id,
            agent_id=conv.agent_id,
            agent_name=conv.agent.name if conv.agent else None,
            title=conv.title,
            updated_at=conv.updated_at,
            last_message_preview=last_msg.content[:100] if last_msg and last_msg.content else None,
        ))

    return summaries


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> ConversationPublic:
    """Get conversation details."""
    _, owner_id = await _chat_caller(db, user)
    result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.agent), selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == owner_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    # Get message count
    count_result = await db.execute(
        select(func.count(Message.id))
        .where(Message.conversation_id == conversation_id)
    )
    message_count = count_result.scalar() or 0

    # Get last message time
    last_msg_result = await db.execute(
        select(Message.created_at)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.sequence.desc())
        .limit(1)
    )
    last_message_at = last_msg_result.scalar_one_or_none()

    return ConversationPublic(
        id=conversation.id,
        agent_id=conversation.agent_id,
        user_id=conversation.user_id,
        channel=conversation.channel,
        title=conversation.title,
        is_active=conversation.is_active,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=message_count,
        last_message_at=last_message_at,
        agent_name=conversation.agent.name if conversation.agent else None,
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> None:
    """Delete a conversation (soft delete)."""
    _, owner_id = await _chat_caller(db, user)
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == owner_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    conversation.is_active = False
    conversation.updated_at = datetime.now(timezone.utc)
    await db.flush()


@router.post("/runs", status_code=status.HTTP_201_CREATED, response_model=ChatRunCreateResponse)
async def submit_chat_run(
    request: ChatRunCreateRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> ChatRunCreateResponse:
    """Persist a user message, queue a durable chat run, and return its snapshot."""
    caller, _ = await _chat_caller(db, user)
    return await create_chat_run(db, caller, request)


@router.get("/conversations/{conversation_id}/state", response_model=ChatRunStateResponse)
async def get_chat_conversation_state(
    conversation_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> ChatRunStateResponse:
    """Return the authoritative chat conversation snapshot for reconnects."""
    caller, _ = await _chat_caller(db, user)
    return await get_chat_state(db, caller, conversation_id)


@router.post("/runs/{run_id}/cancel", response_model=ChatRunCancelResponse)
async def cancel_chat_run_route(
    run_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> ChatRunCancelResponse:
    """Cancel a durable chat run."""
    caller, _ = await _chat_caller(db, user)
    return await cancel_chat_run(db, caller, run_id)


# =============================================================================
# Messages
# =============================================================================


@router.get("/artifacts")
async def list_chat_artifacts(
    db: DbSession,
    user: CurrentActiveUser,
    limit: int = 200,
) -> list[ChatArtifactPublic]:
    """List the current user's durable generated and uploaded Chat files."""
    _, owner_id = await _chat_caller(db, user)
    bounded_limit = min(max(limit, 1), 500)
    result = await db.execute(
        select(Artifact)
        .where(Artifact.created_by_user_id == owner_id)
        .order_by(Artifact.created_at.desc())
        .limit(bounded_limit)
    )
    artifacts = list(result.scalars().all())
    bindings: dict[UUID, tuple[MessageAttachment, str | None, MessageRole | None]] = {}
    if artifacts:
        binding_result = await db.execute(
            select(MessageAttachment, Conversation.title, Message.role)
            .join(Conversation, Conversation.id == MessageAttachment.conversation_id)
            .outerjoin(Message, Message.id == MessageAttachment.message_id)
            .where(
                MessageAttachment.artifact_id.in_(
                    [artifact.id for artifact in artifacts]
                )
            )
            .where(MessageAttachment.message_id.is_not(None))
            .where(Conversation.user_id == owner_id)
            .order_by(MessageAttachment.created_at.desc())
        )
        for attachment, title, message_role in binding_result.all():
            bindings.setdefault(
                attachment.artifact_id,
                (attachment, title, message_role),
            )
    return [
        ChatArtifactPublic(
            id=artifact.id,
            conversation_id=(
                bindings[artifact.id][0].conversation_id
                if artifact.id in bindings
                else None
            ),
            message_id=(
                bindings[artifact.id][0].message_id if artifact.id in bindings else None
            ),
            conversation_title=(
                bindings[artifact.id][1] if artifact.id in bindings else None
            ),
            filename=artifact.filename,
            content_type=artifact.content_type,
            size_bytes=artifact.size_bytes,
            kind=_chat_file_kind(
                bindings[artifact.id][2] if artifact.id in bindings else None
            ),
            created_at=artifact.created_at,
        )
        for artifact in artifacts
    ]


@router.patch("/artifacts/{attachment_id}")
async def rename_chat_artifact(
    attachment_id: UUID,
    request: ChatArtifactUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> ChatArtifactPublic:
    """Rename a Chat file without changing its immutable storage object."""
    _, owner_id = await _chat_caller(db, user)
    artifact = (
        await db.execute(
            select(Artifact)
            .where(Artifact.id == attachment_id)
            .where(Artifact.created_by_user_id == owner_id)
        )
    ).scalar_one_or_none()
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    if Path(request.filename).suffix.casefold() != Path(artifact.filename).suffix.casefold():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Keep the file's existing extension when renaming it.",
        )
    artifact.filename = request.filename
    await db.execute(
        update(MessageAttachment)
        .where(MessageAttachment.artifact_id == artifact.id)
        .values(filename=request.filename)
    )
    await db.flush()
    binding = (
        await db.execute(
            select(MessageAttachment, Conversation.title, Message.role)
            .join(Conversation, Conversation.id == MessageAttachment.conversation_id)
            .outerjoin(Message, Message.id == MessageAttachment.message_id)
            .where(MessageAttachment.artifact_id == artifact.id)
            .where(Conversation.user_id == owner_id)
            .limit(1)
        )
    ).one_or_none()
    attachment = binding[0] if binding else None
    return ChatArtifactPublic(
        id=artifact.id,
        conversation_id=attachment.conversation_id if attachment else None,
        message_id=attachment.message_id if attachment else None,
        conversation_title=binding[1] if binding else None,
        filename=artifact.filename,
        content_type=artifact.content_type,
        size_bytes=artifact.size_bytes,
        kind=_chat_file_kind(binding[2] if binding else None),
        created_at=artifact.created_at,
    )


@router.delete("/artifacts/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_artifact(
    attachment_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> Response:
    """Delete one Chat file owned by the current user."""
    _, owner_id = await _chat_caller(db, user)
    artifact = await db.scalar(
        select(Artifact)
        .where(Artifact.id == attachment_id)
        .where(Artifact.created_by_user_id == owner_id)
    )
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

    from src.services.artifacts import ArtifactService

    await ArtifactService(db).delete(artifact)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/conversations/{conversation_id}/attachments")
async def upload_attachments(
    conversation_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    files: list[UploadFile] = File(...),
) -> AttachmentUploadResponse:
    """Validate and store files for the next message in this conversation."""
    _, owner_id = await _chat_caller(db, user)
    conversation = (
        await db.execute(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .where(Conversation.user_id == owner_id)
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    if len(files) > MAX_FILES_PER_MESSAGE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Attach no more than {MAX_FILES_PER_MESSAGE} files per message.",
        )

    service = ChatAttachmentService(db)
    stored: list[MessageAttachment] = []
    try:
        for upload in files:
            stored.append(
                await service.store(
                    conversation_id=conversation_id,
                    filename=upload.filename or "attachment",
                    content_type=upload.content_type or "application/octet-stream",
                    content=await upload.read(),
                )
            )
    except ChatAttachmentError as exc:
        from src.services.file_storage.service import get_file_storage_service

        storage = get_file_storage_service(db)
        for attachment in stored:
            await storage.delete_raw_from_s3(attachment.s3_key)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AttachmentUploadResponse(
        attachments=[
            AttachmentPublic(
                id=attachment.artifact_id,
                filename=attachment.filename,
                content_type=attachment.content_type,
                size_bytes=attachment.size_bytes,
                kind="attachment",
            )
            for attachment in stored
        ]
    )


@router.delete(
    "/conversations/{conversation_id}/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_unbound_attachment(
    conversation_id: UUID,
    attachment_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> Response:
    """Delete an uploaded attachment that was never bound to a message."""
    _, owner_id = await _chat_caller(db, user)
    attachment = (
        await db.execute(
            select(MessageAttachment)
            .join(Conversation, Conversation.id == MessageAttachment.conversation_id)
            .where(MessageAttachment.artifact_id == attachment_id)
            .where(MessageAttachment.conversation_id == conversation_id)
            .where(MessageAttachment.message_id.is_(None))
            .where(Conversation.user_id == owner_id)
        )
    ).scalar_one_or_none()
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    from src.services.artifacts import ArtifactService

    artifact = await db.get(Artifact, attachment.artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found"
        )
    await ArtifactService(db).delete(artifact)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/conversations/{conversation_id}/attachments/{attachment_id}/content")
async def get_attachment_content(
    conversation_id: UUID,
    attachment_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    download: bool = False,
    preview: bool = False,
) -> Response:
    """Preview or download an attachment after enforcing conversation ownership."""
    _, owner_id = await _chat_caller(db, user)
    attachment = (
        await db.execute(
            select(MessageAttachment)
            .join(Conversation, Conversation.id == MessageAttachment.conversation_id)
            .where(MessageAttachment.artifact_id == attachment_id)
            .where(MessageAttachment.conversation_id == conversation_id)
            .where(Conversation.user_id == owner_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    from src.services.artifacts import ArtifactService, is_browser_active_content_type

    artifact = await db.get(Artifact, attachment.artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found"
        )
    content = await ArtifactService(db).read(artifact)
    if preview:
        from shared.artifact_preview import preview_office_artifact

        preview_html = await asyncio.to_thread(
            preview_office_artifact, content, attachment.content_type
        )
        if preview_html is not None:
            return Response(
                content=preview_html,
                media_type="text/html",
                headers={
                    "Content-Security-Policy": (
                        "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
                    ),
                    "X-Content-Type-Options": "nosniff",
                },
            )
    disposition = (
        "attachment"
        if download or is_browser_active_content_type(attachment.content_type)
        else "inline"
    )
    encoded_filename = quote(attachment.filename, safe="")
    return Response(
        content=content,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": (
                f"{disposition}; filename*=UTF-8''{encoded_filename}"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    limit: int = 100,
    before_sequence: int | None = None,
) -> list[MessagePublic]:
    """Get messages in a conversation."""
    _, owner_id = await _chat_caller(db, user)
    # Verify conversation belongs to user
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == owner_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    # Get messages
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
    )

    if before_sequence is not None:
        stmt = stmt.where(Message.sequence < before_sequence)

    bounded_limit = min(max(limit, 1), 200)
    stmt = stmt.order_by(Message.sequence.desc()).limit(bounded_limit)

    result = await db.execute(stmt)
    messages = list(reversed(result.scalars().all()))

    attachments_by_message: dict[UUID, list[MessageAttachment]] = {}
    message_ids = [message.id for message in messages]
    if message_ids:
        attachment_result = await db.execute(
            select(MessageAttachment)
            .where(MessageAttachment.message_id.in_(message_ids))
            .order_by(MessageAttachment.created_at)
        )
        for attachment in attachment_result.scalars().all():
            if attachment.message_id is not None:
                attachments_by_message.setdefault(attachment.message_id, []).append(attachment)

    return [
        MessagePublic(
            id=m.id,
            conversation_id=m.conversation_id,
            role=m.role,
            content=m.content,
            attachments=[
                AttachmentPublic(
                    id=attachment.artifact_id,
                    filename=attachment.filename,
                    content_type=attachment.content_type,
                    size_bytes=attachment.size_bytes,
                    kind=_chat_file_kind(m.role),
                )
                for attachment in attachments_by_message.get(m.id, [])
            ],
            tool_calls=[
                ToolCall(
                    id=tc["id"],
                    name=tc["name"],
                    arguments=json.loads(tc.get("arguments", "{}"))
                    if isinstance(tc.get("arguments"), str)
                    else tc.get("arguments", {}),
                )
                for tc in (m.tool_calls or [])
            ] if m.tool_calls else None,
            tool_call_id=m.tool_call_id,
            tool_name=m.tool_name,
            execution_id=m.execution_id,
            tool_state=cast(Literal["running", "completed", "error"] | None, m.tool_state),
            tool_result=m.tool_result,
            tool_input=m.tool_input,
            token_count_input=m.token_count_input,
            token_count_output=m.token_count_output,
            model=m.model,
            duration_ms=m.duration_ms,
            sequence=m.sequence,
            created_at=m.created_at,
        )
        for m in messages
    ]


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: UUID,
    request: ChatRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> ChatResponse:
    """
    Send a message to a conversation (non-streaming).

    For streaming responses, use the WebSocket endpoint.
    """
    # The turn is a chat run on a worker, like POST /api/chat/runs; this waits for it.
    caller, _ = await _chat_caller(db, user)
    return await send_chat_message(db, caller, conversation_id, request)
