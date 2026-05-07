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

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.core.auth import CurrentActiveUser
from src.core.database import DbSession
from src.models.contracts.agents import (
    ChatRequest,
    ChatResponse,
    ConversationCreate,
    ConversationPublic,
    ConversationSummary,
    MessagePublic,
    ToolCall,
)
from src.models.orm import Agent, Conversation, Message
from src.services.agent_executor import AgentExecutor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["Chat"])


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

    return [
        MessagePublic(
            id=m.id,
            conversation_id=m.conversation_id,
            role=m.role,
            content=m.content,
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
    )
    accessible = await repo.get(id=agent.id)
    return accessible is not None
