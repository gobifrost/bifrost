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
from src.models.contracts.agents import (
    ChatRequest,
    ChatResponse,
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
from src.services.agent_executor import AgentExecutor
from src.services.ai_model_service import AIModelService
from src.services.chat_attachments import (
    MAX_FILES_PER_MESSAGE,
    ChatAttachmentError,
    ChatAttachmentService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["Chat"])


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
                capabilities=service.normalized_profile_capabilities(profile),
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

        # Check access based on agent's access level
        has_access = await _check_agent_access(db, user, agent)
        if not has_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have access to this agent",
            )

        agent_name = agent.name

    # Create conversation (agent_id can be None for agentless chat)
    conversation_id = uuid4()
    now = datetime.now(timezone.utc)

    conversation = Conversation(
        id=conversation_id,
        agent_id=agent.id if agent else None,
        user_id=user.user_id,
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
    stmt = (
        select(Conversation)
        .options(selectinload(Conversation.agent))
        .where(Conversation.user_id == user.user_id)
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
    result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.agent), selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == user.user_id)
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
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == user.user_id)
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
    bounded_limit = min(max(limit, 1), 500)
    result = await db.execute(
        select(Artifact)
        .where(Artifact.created_by_user_id == user.user_id)
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
            .where(Conversation.user_id == user.user_id)
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
    artifact = (
        await db.execute(
            select(Artifact)
            .where(Artifact.id == attachment_id)
            .where(Artifact.created_by_user_id == user.user_id)
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
            .where(Conversation.user_id == user.user_id)
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
    artifact = await db.scalar(
        select(Artifact)
        .where(Artifact.id == attachment_id)
        .where(Artifact.created_by_user_id == user.user_id)
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
    conversation = (
        await db.execute(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .where(Conversation.user_id == user.user_id)
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
    attachment = (
        await db.execute(
            select(MessageAttachment)
            .join(Conversation, Conversation.id == MessageAttachment.conversation_id)
            .where(MessageAttachment.artifact_id == attachment_id)
            .where(MessageAttachment.conversation_id == conversation_id)
            .where(MessageAttachment.message_id.is_(None))
            .where(Conversation.user_id == user.user_id)
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
    attachment = (
        await db.execute(
            select(MessageAttachment)
            .join(Conversation, Conversation.id == MessageAttachment.conversation_id)
            .where(MessageAttachment.artifact_id == attachment_id)
            .where(MessageAttachment.conversation_id == conversation_id)
            .where(Conversation.user_id == user.user_id)
            .limit(1)
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
    disposition = "attachment" if download else "inline"
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
    # Verify conversation belongs to user
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == user.user_id)
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

    stmt = stmt.order_by(Message.sequence.asc()).limit(limit)

    result = await db.execute(stmt)
    messages = result.scalars().all()

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
    # Verify conversation and optionally get agent
    result = await db.execute(
        select(Conversation)
        .options(
            selectinload(Conversation.agent).selectinload(Agent.tools),
            selectinload(Conversation.agent).selectinload(Agent.delegated_agents),
        )
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == user.user_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    if conversation.agent and not await _check_agent_access(db, user, conversation.agent):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this agent",
        )

    # Agent is now optional - agentless chat uses default system prompt

    # Execute chat — executor manages its own short-lived sessions
    from src.core.database import get_session_factory
    executor = AgentExecutor(get_session_factory())

    # Collect streaming response into a single response
    final_content = ""
    final_tool_calls = []
    final_message_id = None
    final_input_tokens = None
    final_output_tokens = None
    final_duration_ms = None
    final_artifacts = []

    async for chunk in executor.chat(
        agent=conversation.agent,  # May be None for agentless chat
        conversation=conversation,
        user_message=request.message,
        stream=False,
        user=user,
        attachment_ids=request.attachment_ids,
        model_profile_id=request.model_profile_id,
    ):
        if chunk.type == "delta" and chunk.content:
            final_content += chunk.content
        elif chunk.type == "tool_call" and chunk.tool_call:
            final_tool_calls.append(chunk.tool_call)
        elif chunk.type == "artifact_ready" and chunk.artifact:
            final_artifacts.append(chunk.artifact)
        elif chunk.type == "done":
            # For non-streaming, content is sent in the done chunk
            if chunk.content:
                final_content = chunk.content
            final_message_id = chunk.message_id
            final_input_tokens = chunk.token_count_input
            final_output_tokens = chunk.token_count_output
            final_duration_ms = chunk.duration_ms
        elif chunk.type == "error":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=chunk.error or "Unknown error during chat",
            )

    return ChatResponse(
        message_id=UUID(final_message_id) if final_message_id else uuid4(),
        content=final_content,
        tool_calls=final_tool_calls if final_tool_calls else None,
        artifacts=final_artifacts,
        token_count_input=final_input_tokens,
        token_count_output=final_output_tokens,
        duration_ms=final_duration_ms,
    )


# =============================================================================
# Helper Functions
# =============================================================================


async def _check_agent_access(db: DbSession, user, agent: Agent) -> bool:
    """Check if user has access to an agent.

    Delegates to ``AgentRepository.get(id=...)`` — the same gate the UI
    listing path and MCP tool access service use. Returns True iff the
    repo finds the agent in the user's scope (org + global cascade) AND
    the access_level / role check passes.

    Prior to this delegation, the function checked access_level and roles
    but had no org-scope check, allowing an Org A user with a same-named
    role to access an Org B agent if they knew its ID.
    """
    from src.repositories.agents import AgentRepository

    repo = AgentRepository(
        db,
        org_id=user.organization_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
        is_external=user.is_external,
    )
    accessible = await repo.get(id=agent.id)
    return accessible is not None
