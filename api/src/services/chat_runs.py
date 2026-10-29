"""Durable chat run control-plane helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from shared import access_checks
from shared.run_lineage import principal_lineage
from src.core.cache.redis_client import get_redis
from src.core.db_deps import DbSession
from src.core.pubsub import publish_chat_run_event, replay_chat_run_events
from src.jobs.schedulers.execution_cleanup import DEFAULT_AGENT_RUN_TIMEOUT_SECONDS
from src.models.contracts.agents import (
    ChatRequest,
    ChatResponse,
    ChatRunCancelResponse,
    ChatRunCreateRequest,
    ChatRunCreateResponse,
    ChatRunEventPublic,
    ChatRunPublic,
    ChatRunStateResponse,
    ChatStreamChunk,
    ConversationPublic,
    MessagePublic,
    ToolCall,
)
from src.models.enums import MessageRole
from src.models.orm import Agent, AgentRun, Conversation, Message, MessageAttachment
from src.services.authorization.enforce import Caller
from src.services.authorization.agent_runtime import (
    AgentRuntimeAuthorizationError,
    authorize_agent_execution,
    authorize_agentless_ai,
)
from src.services.artifacts import artifact_ref
from src.services.chat_attachments import ChatAttachmentError, ChatAttachmentService
from src.services.chat_errors import public_chat_error_message
from src.services.execution.agent_run_service import (
    enqueue_agent_run,
    wait_for_agent_run_result,
)


def _conversation_public(conversation: Conversation, message_count: int) -> ConversationPublic:
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
        agent_name=conversation.agent.name if conversation.agent else None,
    )


def _run_public(run: AgentRun | None) -> ChatRunPublic | None:
    if run is None:
        return None
    public_run = ChatRunPublic.model_validate(run)
    if public_run.error is not None:
        public_run.error = public_chat_error_message(public_run.status)
    return public_run


def _owner_id(caller: Caller) -> UUID:
    if caller.ctx is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Chat caller is unavailable")
    return caller.ctx.user_id


async def _load_conversation_or_404(db: DbSession, conversation_id: UUID, caller: Caller) -> Conversation:
    result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.agent))
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == _owner_id(caller))
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    return conversation


async def _load_authorized_agent(
    db: DbSession,
    caller: Caller,
    agent_id: UUID,
) -> tuple[Caller, Agent]:
    try:
        caller, agent = await authorize_agent_execution(db, caller, agent_id)
    except AgentRuntimeAuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    access_checks.note_launch(
        "agents.execute", agent.organization_id, caller.ctx.home_organization_id,
        subject=f"agent:{agent.id}",
    )
    return caller, agent


async def _persist_user_message(
    db: DbSession,
    conversation: Conversation,
    *,
    content: str,
    user_message_id: UUID,
    attachment_ids: list[UUID],
) -> Message:
    max_sequence_result = await db.execute(
        select(func.coalesce(func.max(Message.sequence), 0)).where(
            Message.conversation_id == conversation.id
        )
    )
    next_sequence = int(max_sequence_result.scalar() or 0) + 1

    message = Message(
        id=user_message_id,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=content,
        sequence=next_sequence,
        local_id=str(user_message_id),
    )
    db.add(message)
    if attachment_ids:
        await ChatAttachmentService(db).bind(
            attachment_ids=attachment_ids,
            message_id=message.id,
            conversation_id=conversation.id,
        )
    conversation.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return message


async def create_chat_run(
    db: DbSession,
    caller: Caller,
    request: ChatRunCreateRequest,
    *,
    sync: bool = False,
) -> ChatRunCreateResponse:
    """Create or resume a chat run submission.

    ``sync`` has the run push its result for :func:`wait_for_agent_run_result`.
    """
    client_run_id = request.client_run_id or uuid4()
    existing_run = await db.get(AgentRun, client_run_id)
    if existing_run is not None:
        if existing_run.trigger_type != "chat" or existing_run.conversation_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Run id is already in use.",
            )
        conversation = await _load_conversation_or_404(
            db,
            existing_run.conversation_id,
            caller,
        )
        stored_message_id = (existing_run.input or {}).get("user_message_id")
        if not stored_message_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Existing chat run has no user message.",
            )
        stored_input = existing_run.input or {}
        submitted_identity_matches = (
            (
                request.conversation_id is None
                or existing_run.conversation_id == request.conversation_id
            )
            and (
                request.user_message_id is None
                or stored_message_id == str(request.user_message_id)
            )
            and stored_input.get("content") == request.content
            and stored_input.get("attachment_ids", [])
            == [str(attachment_id) for attachment_id in request.attachment_ids]
            and stored_input.get("model_profile_id")
            == (
                str(request.model_profile_id)
                if request.model_profile_id is not None
                else None
            )
            and (
                request.agent_id is None
                or stored_input.get("agent_id") == str(request.agent_id)
            )
        )
        if not submitted_identity_matches:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Run id is already associated with a different chat submission.",
            )
        return ChatRunCreateResponse(
            run_id=existing_run.id,
            conversation=_conversation_public(
                conversation,
                message_count=await _conversation_message_count(db, conversation.id),
            ),
            user_message=await _latest_message_public(
                db,
                conversation.id,
                UUID(stored_message_id),
            ),
            status=existing_run.status,
            idempotent=True,
        )

    user_message_id = request.user_message_id or uuid4()
    conversation: Conversation | None = None
    if request.conversation_id is not None:
        result = await db.execute(
            select(Conversation)
            .options(selectinload(Conversation.agent))
            .where(Conversation.id == request.conversation_id)
        )
        existing_conversation = result.scalar_one_or_none()
        if existing_conversation is not None:
            if existing_conversation.user_id != _owner_id(caller):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Conversation {request.conversation_id} not found",
                )
            conversation = existing_conversation

    if conversation is None:
        conversation_id = request.conversation_id or uuid4()
        requested_agent = None
        if request.agent_id is not None:
            caller, requested_agent = await _load_authorized_agent(
                db, caller, request.agent_id
            )
        conversation = Conversation(
            id=conversation_id,
            agent_id=requested_agent.id if requested_agent else None,
            user_id=_owner_id(caller),
            channel="chat",
            title=None,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        conversation.agent = requested_agent
        db.add(conversation)
        await db.flush()
    else:
        if request.agent_id is not None and conversation.agent_id not in {
            None,
            request.agent_id,
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conversation is already bound to a different agent.",
            )
        target_agent_id = request.agent_id or conversation.agent_id
        if target_agent_id is not None:
            caller, authorized_agent = await _load_authorized_agent(
                db, caller, target_agent_id
            )
            if conversation.agent_id is None:
                conversation.agent_id = authorized_agent.id
                conversation.agent = authorized_agent
    if conversation.agent_id is None:
        try:
            caller = await authorize_agentless_ai(db, caller)
        except AgentRuntimeAuthorizationError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        access_checks.note_launch("ai.execute", None, caller.ctx.home_organization_id, subject="ai:chat")

    existing_message = await db.get(Message, user_message_id)
    if existing_message is not None:
        if (
            existing_message.conversation_id != conversation.id
            or existing_message.role != MessageRole.USER
            or existing_message.content != request.content
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User message id is already in use.",
            )
        user_message = existing_message
    else:
        try:
            user_message = await _persist_user_message(
                db,
                conversation,
                content=request.content,
                user_message_id=user_message_id,
                attachment_ids=request.attachment_ids,
            )
        except ChatAttachmentError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    await db.commit()

    async def publish_queued(run_id: str) -> None:
        await publish_chat_run_event(
            conversation_id=conversation.id,
            run_id=run_id,
            kind="run_status",
            status="queued",
            payload=ChatStreamChunk(
                type="run_status",
                conversation_id=str(conversation.id),
                user_message_id=str(user_message.id),
                local_id=str(user_message.id),
                run_status="queued",
            ),
        )

    try:
        run_id = await enqueue_agent_run(
            agent_id=str(conversation.agent_id) if conversation.agent_id else None,
            trigger_type="chat",
            trigger_source=str(client_run_id),
            input_data={
                "conversation_id": str(conversation.id),
                "client_run_id": str(client_run_id),
                "user_message_id": str(user_message.id),
                "content": request.content,
                "attachment_ids": [
                    str(attachment_id) for attachment_id in request.attachment_ids
                ],
                "model_profile_id": (
                    str(request.model_profile_id)
                    if request.model_profile_id
                    else None
                ),
                "agent_id": (
                    str(conversation.agent_id) if conversation.agent_id else None
                ),
            },
            org_id=(
                str(caller.principal.runtime_authority.organization_id)
                if caller.principal.runtime_authority is not None
                and caller.principal.runtime_authority.organization_id is not None
                else str(caller.ctx.home_organization_id)
                if caller.ctx.home_organization_id is not None
                else None
            ),
            caller_user_id=str(caller.principal.user_id),
            caller_email=caller.principal.email,
            caller_name=caller.principal.name,
            caller_is_superuser=caller.principal.is_superuser,
            caller_is_external=caller.principal.is_external,
            caller_is_provider_org=caller.principal.is_provider_org,
            caller_roles=caller.principal.roles,
            conversation_id=str(conversation.id),
            sync=sync,
            run_id=str(client_run_id),
            before_queue_publish=publish_queued,
            lineage=await principal_lineage(db, caller.principal),
        )
    except Exception:
        await publish_chat_run_event(
            conversation_id=conversation.id,
            run_id=client_run_id,
            kind="error",
            status="failed",
            payload=ChatStreamChunk(
                type="error",
                conversation_id=str(conversation.id),
                run_status="failed",
                error="Chat run could not be queued",
            ),
        )
        raise

    return ChatRunCreateResponse(
        run_id=UUID(run_id),
        conversation=_conversation_public(
            conversation,
            message_count=await _conversation_message_count(db, conversation.id),
        ),
        user_message=await _latest_message_public(
            db,
            conversation.id,
            user_message.id,
        ),
        status="queued",
        idempotent=False,
    )


async def send_chat_message(
    db: DbSession,
    caller: Caller,
    conversation_id: UUID,
    request: ChatRequest,
) -> ChatResponse:
    """Run one chat turn to its end and answer with its reply (non-streaming).

    The turn is a chat run like any other (:func:`create_chat_run`): an
    ``agent.run`` dispatch that executes on a worker. This waits for the
    run's sync result, then reads the reply the run persisted.
    """
    conversation = await _load_conversation_or_404(db, conversation_id, caller)
    timeout = (
        conversation.agent.max_run_timeout
        if conversation.agent is not None and conversation.agent.max_run_timeout
        else DEFAULT_AGENT_RUN_TIMEOUT_SECONDS
    )
    created = await create_chat_run(
        db,
        caller,
        ChatRunCreateRequest(
            conversation_id=conversation.id,
            content=request.message,
            attachment_ids=request.attachment_ids,
            model_profile_id=request.model_profile_id,
        ),
        sync=True,
    )
    result = await wait_for_agent_run_result(str(created.run_id), timeout=timeout)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Chat run timed out",
        )
    if result.get("status") != "completed":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=public_chat_error_message(result.get("status")),
        )
    return await _chat_turn_response(db, conversation.id, created.user_message.sequence)


async def _chat_turn_response(
    db: DbSession,
    conversation_id: UUID,
    user_message_sequence: int,
) -> ChatResponse:
    """The reply to the user message at ``user_message_sequence``: the turn's
    final assistant message, the tool calls it made and the artifacts bound
    to its messages."""
    result = await db.execute(
        select(Message)
        .options(selectinload(Message.attachments).selectinload(MessageAttachment.artifact))
        .where(Message.conversation_id == conversation_id)
        .where(Message.sequence > user_message_sequence)
        .order_by(Message.sequence)
    )
    turn = list(result.scalars().all())
    reply = next(
        (message for message in reversed(turn) if message.role == MessageRole.ASSISTANT),
        None,
    )
    if reply is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Chat run completed without a reply",
        )
    tool_calls = [
        ToolCall(id=message.tool_call_id, name=message.tool_name, arguments=message.tool_input or {})
        for message in turn
        if message.role == MessageRole.TOOL_CALL
        and message.tool_call_id is not None
        and message.tool_name is not None
    ]
    return ChatResponse(
        message_id=reply.id,
        content=reply.content or "",
        tool_calls=tool_calls or None,
        artifacts=[
            artifact_ref(attachment.artifact)
            for message in turn
            for attachment in message.attachments
        ],
        token_count_input=reply.token_count_input,
        token_count_output=reply.token_count_output,
        duration_ms=reply.duration_ms,
    )


async def get_chat_state(
    db: DbSession,
    caller: Caller,
    conversation_id: UUID,
) -> ChatRunStateResponse:
    conversation = await _load_conversation_or_404(db, conversation_id, caller)
    message_count = await _conversation_message_count(db, conversation.id)
    messages = await _conversation_messages(db, conversation.id)
    active_run = await _latest_run(db, conversation.id)
    events = await replay_chat_run_events(conversation.id, limit=20_000)
    latest_sequence = max((int(event.get("sequence") or 0) for event in events), default=0)
    return ChatRunStateResponse(
        conversation=_conversation_public(conversation, message_count),
        active_run=_run_public(active_run),
        messages=messages,
        events=[_coerce_chat_event(event) for event in events],
        latest_sequence=latest_sequence,
        context_usage=(conversation.extra_data or {}).get("context_usage"),
    )


async def cancel_chat_run(
    db: DbSession,
    caller: Caller,
    run_id: UUID,
) -> ChatRunCancelResponse:
    query = (
        select(AgentRun)
        .join(Conversation, Conversation.id == AgentRun.conversation_id)
        .options(selectinload(AgentRun.agent))
        .where(AgentRun.id == run_id)
        .where(Conversation.user_id == _owner_id(caller))
    )
    result = await db.execute(query)
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chat run {run_id} not found",
        )

    if run.status in {"completed", "failed", "cancelled", "timeout", "budget_exceeded"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel chat run with status '{run.status}'",
        )

    if run.status == "cancelling":
        return ChatRunCancelResponse(run_id=run.id, status="cancelling")

    if run.status == "queued":
        run.status = "cancelled"
        run.completed_at = datetime.now(timezone.utc)
    else:
        async with get_redis() as redis:
            await redis.setex(f"bifrost:agent_run:{run_id}:cancel", 3600, "1")
        run.status = "cancelling"
        run.cancel_requested_at = datetime.now(timezone.utc)

    await db.commit()

    if run.conversation_id is None:
        raise RuntimeError("Chat run has no conversation")

    await publish_chat_run_event(
        conversation_id=run.conversation_id,
        run_id=run.id,
        kind="run_status",
        status=run.status,
        payload=ChatStreamChunk(
            type="run_status",
            conversation_id=str(run.conversation_id),
            run_status=run.status,
        ),
    )

    return ChatRunCancelResponse(run_id=run.id, status=run.status)


async def _conversation_message_count(db: DbSession, conversation_id: UUID) -> int:
    result = await db.execute(
        select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
    )
    return int(result.scalar() or 0)


async def _conversation_messages(db: DbSession, conversation_id: UUID) -> list[MessagePublic]:
    result = await db.execute(
        select(Message)
        .options(selectinload(Message.attachments))
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.sequence.desc())
        .limit(200)
    )
    messages = list(reversed(result.scalars().all()))
    return [MessagePublic.model_validate(message) for message in messages]


async def _latest_message_public(
    db: DbSession,
    conversation_id: UUID,
    message_id: UUID,
) -> MessagePublic:
    result = await db.execute(
        select(Message)
        .options(selectinload(Message.attachments))
        .where(Message.conversation_id == conversation_id)
        .where(Message.id == message_id)
    )
    message = result.scalar_one_or_none()
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message {message_id} not found",
        )
    return MessagePublic.model_validate(message)


async def _latest_run(db: DbSession, conversation_id: UUID) -> AgentRun | None:
    result = await db.execute(
        select(AgentRun)
        .options(selectinload(AgentRun.agent))
        .where(AgentRun.conversation_id == conversation_id)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _coerce_chat_event(event: dict) -> ChatRunEventPublic:
    payload = event.get("payload") or {}
    payload_chunk = ChatStreamChunk.model_validate(payload)
    return ChatRunEventPublic(
        protocol_version=int(event.get("protocol_version") or 1),
        event_id=UUID(str(event["event_id"])),
        sequence=int(event.get("sequence") or 0),
        conversation_id=UUID(str(event["conversation_id"])),
        run_id=UUID(str(event["run_id"])),
        occurred_at=datetime.fromisoformat(str(event["occurred_at"])),
        kind=str(event.get("kind") or payload_chunk.type),
        status=str(event.get("status") or "unknown"),
        payload=payload_chunk,
    )
