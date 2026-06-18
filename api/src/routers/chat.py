"""
Chat Router

Chat conversations and messaging for AI agents.
HTTP endpoints for conversations and messages.

For real-time streaming, use the WebSocket endpoint at /ws/connect
(see websocket.py) with chat:{conversation_id} channel subscription.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.agents import (
    ArtifactDownloadResponse,
    ArtifactInfo,
    AttachmentPublic,
    AttachmentUploadResponse,
    ChatRequest,
    ChatResponse,
    CompactConversationResponse,
    ConversationCreate,
    ConversationPublic,
    ConversationSummary,
    ConversationUpdate,
    MessagePublic,
    SwitchBranchRequest,
    ToolCall,
)
from src.models.orm import Agent, Conversation, Message, MessageAttachment, Workspace
from src.services.agent_executor import AgentExecutor
from src.services.attachments import (
    MAX_FILES_PER_MESSAGE,
    AttachmentError,
    AttachmentService,
)
from src.services.workspace_service import can_access_workspace

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["Chat"])


# =============================================================================
# Picker context — what model the picker should show as default + allowlist
# =============================================================================


class ChatModelContext(BaseModel):
    """What the chat picker needs to render the right options.

    `allowed_chat_models`: the org's allowlist (empty list = "no narrowing,
    only the default model is selectable").
    `default_chat_model`: the resolved default model_id (org > role >
    workspace > user → first non-null). The picker shows this when the
    allowlist is empty.
    """

    allowed_chat_models: list[str]
    default_chat_model: str | None


@router.get("/model-context")
async def get_chat_model_context(
    db: DbSession,
    user: CurrentActiveUser,
) -> ChatModelContext:
    """Return the picker's effective allowlist + default for the current user."""
    from src.models.orm import Organization
    from src.models.orm.users import User as UserORM, UserRole as UserRoleORM
    from src.models.orm.users import Role as RoleORM

    org = (
        await db.get(Organization, user.organization_id)
        if user.organization_id
        else None
    )
    allowed = list(org.allowed_chat_models or []) if org else []

    # Walk the same default cascade as the resolver, picker-side: user >
    # role > org. We don't have a workspace here (no conv/workspace
    # context), so we stop at org. Conversation-level current_model lives
    # on the conversation row, not in this endpoint.
    me = await db.get(UserORM, user.user_id)
    default: str | None = (me.default_chat_model if me else None)
    if not default:
        role_rows = (
            await db.execute(
                select(RoleORM.default_chat_model)
                .join(UserRoleORM, UserRoleORM.role_id == RoleORM.id)
                .where(UserRoleORM.user_id == user.user_id)
                .where(RoleORM.default_chat_model.isnot(None))
            )
        ).all()
        if role_rows:
            default = role_rows[0][0]
    if not default and org:
        default = org.default_chat_model

    return ChatModelContext(
        allowed_chat_models=allowed,
        default_chat_model=default,
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

    # Resolve workspace: explicit if provided (must be accessible), else null
    # (general pool — the unscoped default chat list).
    workspace_id: UUID | None = None
    if request.workspace_id is not None:
        workspace_result = await db.execute(
            select(Workspace).where(Workspace.id == request.workspace_id)
        )
        workspace = workspace_result.scalar_one_or_none()
        if workspace is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workspace {request.workspace_id} not found",
            )
        if not await can_access_workspace(db, user, workspace):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this workspace",
            )
        workspace_id = workspace.id

    # Create conversation (agent_id can be None for agentless chat)
    conversation_id = uuid4()
    now = datetime.now(timezone.utc)

    conversation = Conversation(
        id=conversation_id,
        agent_id=agent.id if agent else None,
        user_id=user.user_id,
        workspace_id=workspace_id,
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
        workspace_id=conversation.workspace_id,
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
    workspace_id: UUID | None = None,
    pool: Literal["any", "general", "workspace"] | None = None,
    active_only: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> list[ConversationSummary]:
    """List the caller's conversations.

    Filtering options for the sidebar:
    - ``workspace_id={uuid}`` — only chats in that workspace.
    - ``pool=general`` — only chats in the general pool (workspace_id IS NULL).
    - ``pool=any`` (or omit both) — every chat the user has, regardless of
      workspace. Used by search.
    """
    stmt = (
        select(Conversation)
        .options(selectinload(Conversation.agent))
        .where(Conversation.user_id == user.user_id)
    )

    if active_only:
        stmt = stmt.where(Conversation.is_active.is_(True))

    if agent_id:
        stmt = stmt.where(Conversation.agent_id == agent_id)

    if workspace_id is not None:
        stmt = stmt.where(Conversation.workspace_id == workspace_id)
    elif pool == "general":
        stmt = stmt.where(Conversation.workspace_id.is_(None))
    # pool == "any" or None → no workspace filter.

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
            workspace_id=conv.workspace_id,
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
        workspace_id=conversation.workspace_id,
        active_leaf_message_id=conversation.active_leaf_message_id,
        instructions=conversation.instructions,
        channel=conversation.channel,
        title=conversation.title,
        is_active=conversation.is_active,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=message_count,
        last_message_at=last_message_at,
        agent_name=conversation.agent.name if conversation.agent else None,
    )


@router.patch("/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> ConversationPublic:
    """Update mutable fields on a conversation.

    Editable fields:
    - ``title``: inline rename in the sidebar. Replaces the auto-generated title.
    - ``workspace_id``: "Move to workspace" affordance. Null = general pool.
    - ``current_model``: per-conversation model selection set by the picker.
    - ``instructions``: per-conversation custom instructions appended to
      the system prompt. Null clears.

    Fields use ``model_dump(exclude_unset=True)`` semantics: a field absent
    from the request body is preserved; ``null`` in the body clears it.
    """
    result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.agent))
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == user.user_id)
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    update_fields = payload.model_dump(exclude_unset=True)
    if "title" in update_fields:
        conversation.title = update_fields["title"]

    if "workspace_id" in update_fields:
        new_workspace_id = update_fields["workspace_id"]
        if new_workspace_id is not None:
            ws_result = await db.execute(
                select(Workspace).where(Workspace.id == new_workspace_id)
            )
            target = ws_result.scalar_one_or_none()
            if target is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Workspace {new_workspace_id} not found",
                )
            if not await can_access_workspace(db, user, target):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have access to this workspace",
                )
        conversation.workspace_id = new_workspace_id

    if "current_model" in update_fields:
        conversation.current_model = update_fields["current_model"]

    if "instructions" in update_fields:
        conversation.instructions = update_fields["instructions"]

    conversation.updated_at = datetime.now(timezone.utc)
    await db.flush()

    # Compute counts for response shape parity with GET.
    count_result = await db.execute(
        select(func.count(Message.id)).where(
            Message.conversation_id == conversation_id
        )
    )
    message_count = count_result.scalar() or 0
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
        workspace_id=conversation.workspace_id,
        active_leaf_message_id=conversation.active_leaf_message_id,
        instructions=conversation.instructions,
        current_model=conversation.current_model,
        channel=conversation.channel,
        title=conversation.title,
        is_active=conversation.is_active,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=message_count,
        last_message_at=last_message_at,
        agent_name=conversation.agent.name if conversation.agent else None,
    )


@router.post("/conversations/{conversation_id}/active-leaf")
async def switch_active_leaf(
    conversation_id: UUID,
    payload: SwitchBranchRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> ConversationPublic:
    """Switch the conversation's active leaf — sibling navigation."""
    result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.agent))
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == user.user_id)
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    target = await db.get(Message, payload.message_id)
    if target is None or target.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not in this conversation",
        )

    conversation.active_leaf_message_id = target.id
    conversation.updated_at = datetime.now(timezone.utc)
    await db.flush()

    count_result = await db.execute(
        select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
    )
    message_count = count_result.scalar() or 0
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
        workspace_id=conversation.workspace_id,
        active_leaf_message_id=conversation.active_leaf_message_id,
        instructions=conversation.instructions,
        current_model=conversation.current_model,
        channel=conversation.channel,
        title=conversation.title,
        is_active=conversation.is_active,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=message_count,
        last_message_at=last_message_at,
        agent_name=conversation.agent.name if conversation.agent else None,
    )


@router.post("/conversations/{conversation_id}/compact")
async def compact_conversation(
    conversation_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> CompactConversationResponse:
    """Manually compact older turns (§4.3, "Compact older turns" button).

    Lossless: summarizes older turns into a persisted checkpoint that folds
    them in the model's working context only. The message rows are never
    modified — the user still sees the full conversation in scrollback.
    """
    from src.services.chat_compaction import compact_now

    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == user.user_id)
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    outcome = await compact_now(
        db,
        conversation_id,
        organization_id=user.organization_id,
    )
    await db.commit()
    return CompactConversationResponse(
        compacted=outcome.compacted,
        turns_compacted=outcome.turns_compacted,
        tokens_before=outcome.tokens_before,
        tokens_after=outcome.tokens_after,
        message=outcome.message,
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
# Attachments
# =============================================================================


@router.post("/conversations/{conversation_id}/attachments")
async def upload_attachments(
    conversation_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    files: list[UploadFile] = File(..., description="Files to attach (images, PDFs, CSVs, text)"),
) -> AttachmentUploadResponse:
    """Upload one or more files to a conversation.

    Files are validated, stored to S3, and (for PDF/CSV/text) text-extracted.
    They are created unbound; they bind to a user message when the next chat
    message referencing their IDs is sent.
    """
    # Ownership check — conversations are owned by their creating user.
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

    if len(files) > MAX_FILES_PER_MESSAGE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many files. Maximum is {MAX_FILES_PER_MESSAGE} per message.",
        )

    service = AttachmentService(db)
    stored: list[MessageAttachment] = []
    try:
        for f in files:
            content = await f.read()
            attachment = await service.store_upload(
                conversation_id=conversation_id,
                filename=f.filename or "attachment",
                content_type=f.content_type or "application/octet-stream",
                content=content,
            )
            stored.append(attachment)
    except AttachmentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    await db.commit()

    return AttachmentUploadResponse(
        attachments=[AttachmentPublic.model_validate(a) for a in stored]
    )


# =============================================================================
# Artifacts
# =============================================================================


@router.get("/conversations/{conversation_id}/artifacts/{artifact_id}/download")
async def download_artifact(
    conversation_id: UUID,
    artifact_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> ArtifactDownloadResponse:
    """Mint a scoped, expiring download URL for one artifact file.

    The URL is minted per request, never stored on the artifact and never
    returned by the tool that produced it. Ownership is enforced via the
    conversation (conversations are owned by their creating user).
    """
    from src.services.artifacts import DOWNLOAD_URL_TTL_SECONDS, ArtifactService

    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == user.user_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    service = ArtifactService(db)
    artifact = await service.get_for_download(
        artifact_id=artifact_id, conversation_id=conversation_id
    )
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )

    url = await service.mint_download_url(artifact)
    return ArtifactDownloadResponse(url=url, expires_in=DOWNLOAD_URL_TTL_SECONDS)


# =============================================================================
# Messages
# =============================================================================


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

    # Get messages with sibling metadata via window functions.
    # Note: NULL parent_message_id is treated as a single partition by Postgres,
    # which is the intended behavior. Editing the first user message creates a
    # second NULL-parent row in the same conversation — those rows ARE siblings
    # of each other (a fresh start on the conversation), and the window function
    # correctly counts them together.
    sibling_count_col = func.count("*").over(
        partition_by=Message.parent_message_id
    ).label("sibling_count")
    sibling_index_col = (
        func.row_number().over(
            partition_by=Message.parent_message_id,
            order_by=Message.sequence,
        ) - 1
    ).label("sibling_index")

    stmt = (
        select(Message, sibling_count_col, sibling_index_col)
        .where(Message.conversation_id == conversation_id)
    )

    if before_sequence is not None:
        stmt = stmt.where(Message.sequence < before_sequence)

    stmt = stmt.order_by(Message.sequence.asc()).limit(limit)

    rows = (await db.execute(stmt)).all()

    # Batch-load attachments for the returned messages (avoids N+1).
    message_ids = [m.id for m, _sc, _si in rows]
    attachments_by_message: dict[UUID, list[MessageAttachment]] = {}
    if message_ids:
        att_rows = (
            await db.execute(
                select(MessageAttachment)
                .where(MessageAttachment.message_id.in_(message_ids))
                .order_by(MessageAttachment.created_at)
            )
        ).scalars().all()
        for att in att_rows:
            if att.message_id is not None:
                attachments_by_message.setdefault(att.message_id, []).append(att)

    # Batch-load artifacts for the returned messages (avoids N+1).
    artifacts_by_message: dict[UUID, list[ArtifactInfo]] = {}
    if message_ids:
        from src.services.artifacts import build_artifact_infos
        from src.models.orm import MessageArtifact

        art_rows = (
            await db.execute(
                select(MessageArtifact)
                .where(MessageArtifact.message_id.in_(message_ids))
                .order_by(MessageArtifact.created_at)
            )
        ).scalars().all()
        grouped: dict[UUID, list[MessageArtifact]] = {}
        for art in art_rows:
            grouped.setdefault(art.message_id, []).append(art)
        for mid, art_list in grouped.items():
            artifacts_by_message[mid] = build_artifact_infos(art_list)

    return [
        MessagePublic(
            id=m.id,
            conversation_id=m.conversation_id,
            role=m.role,
            content=m.content,
            attachments=[
                AttachmentPublic.model_validate(a)
                for a in attachments_by_message.get(m.id, [])
            ],
            artifacts=artifacts_by_message.get(m.id, []),
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
            cost_tier=m.cost_tier,
            duration_ms=m.duration_ms,
            sequence=m.sequence,
            parent_message_id=m.parent_message_id,
            sibling_count=int(sib_count),
            sibling_index=int(sib_index),
            created_at=m.created_at,
        )
        for m, sib_count, sib_index in rows
    ]


@router.get("/conversations/{conversation_id}/export")
async def export_conversation(
    conversation_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    format: Literal["markdown", "json"] = "markdown",
) -> Response:
    """Export a conversation as Markdown (default) or JSON (§8.3).

    Read-only. Returns a downloadable attachment with a content-disposition
    filename derived from the conversation title. Markdown renders tool calls
    as collapsible blocks; JSON is the structured round-trip shape.
    """
    from src.services import chat_export

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

    messages_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.sequence.asc())
    )
    messages = list(messages_result.scalars().all())

    # Slug the title for the download filename; fall back to the id.
    raw = (conversation.title or "").strip()
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in raw).strip("-")
    stem = slug[:60] or f"conversation-{conversation_id}"

    if format == "json":
        body = chat_export.conversation_to_json(conversation, messages)
        media_type = "application/json"
        filename = f"{stem}.json"
    else:
        body = chat_export.conversation_to_markdown(conversation, messages)
        media_type = "text/markdown"
        filename = f"{stem}.md"

    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


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

    async for chunk in executor.chat(
        agent=conversation.agent,  # May be None for agentless chat
        conversation=conversation,
        user_message=request.message,
        stream=False,
        user=user,
        attachment_ids=request.attachment_ids or None,
    ):
        if chunk.type == "delta" and chunk.content:
            final_content += chunk.content
        elif chunk.type == "tool_call" and chunk.tool_call:
            final_tool_calls.append(chunk.tool_call)
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
