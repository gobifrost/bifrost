"""
WebSocket Router

Provides real-time updates via WebSocket connections.
Replaces Azure Web PubSub with native FastAPI WebSockets.
"""

import asyncio
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.auth import UserPrincipal, get_current_user_ws
from src.core.database import get_db_context
from src.core.log_safety import log_safe
from src.core.pubsub import manager
from src.models import Conversation, Execution
from src.models.orm import Agent
from src.models.orm.applications import Application

logger = logging.getLogger(__name__)


async def can_access_conversation(user: UserPrincipal, conversation_id: str) -> tuple[bool, Conversation | None]:
    """
    Check if user can access a conversation.

    Args:
        user: The authenticated user
        conversation_id: The conversation ID to check access for

    Returns:
        Tuple of (has_access, conversation_object)
    """
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        return False, None

    async with get_db_context() as db:
        result = await db.execute(
            select(Conversation)
            .options(
                selectinload(Conversation.agent).selectinload(Agent.tools),
                selectinload(Conversation.agent).selectinload(Agent.delegated_agents),
                selectinload(Conversation.user),
            )
            .where(Conversation.id == conv_uuid)
            .where(Conversation.user_id == user.user_id)
            .where(Conversation.is_active.is_(True))
        )
        conversation = result.scalar_one_or_none()

        if conversation is None:
            return False, None

        return True, conversation


async def can_access_execution(user: UserPrincipal, execution_id: str) -> bool:
    """
    Check if user can access an execution (owner or superuser).

    Args:
        user: The authenticated user
        execution_id: The execution ID to check access for

    Returns:
        True if user can access, False otherwise
    """
    # Superusers can access any execution
    if user.is_superuser:
        return True

    # Embed users: check Redis key linking their session (jti) to the execution
    if user.embed and user.jti:
        from src.core.cache.keys import embed_execution_key
        from src.core.cache.redis_client import get_redis

        async with get_redis() as r:
            return bool(await r.exists(embed_execution_key(user.jti, execution_id)))

    try:
        execution_uuid = UUID(execution_id)
    except ValueError:
        return False

    async with get_db_context() as db:
        result = await db.execute(
            select(Execution.executed_by).where(Execution.id == execution_uuid)
        )
        row = result.scalar_one_or_none()

        if row is None:
            # Execution doesn't exist - allow subscription anyway
            # (they won't receive anything, and this avoids timing attacks)
            return True

        return row == user.user_id


async def can_access_app(user: UserPrincipal, app_id: str) -> bool:
    """
    Check if user can access an application.

    Access is granted if:
    - User is a superuser (platform admin)
    - App is global (organization_id is NULL)
    - App belongs to user's organization

    Args:
        user: The authenticated user
        app_id: The application ID to check access for

    Returns:
        True if user can access, False otherwise
    """
    # Superusers can access any app
    if user.is_superuser:
        return True

    try:
        app_uuid = UUID(app_id)
    except ValueError:
        return False

    async with get_db_context() as db:
        result = await db.execute(
            select(Application.organization_id).where(Application.id == app_uuid)
        )
        # Note: scalar_one_or_none returns None if no row, or the column value (which may also be None for global apps)
        # We need to check if the row exists first
        row_result = result.one_or_none()

        if row_result is None:
            # App doesn't exist - allow subscription anyway
            # (they won't receive anything, and this avoids timing attacks)
            return True

        org_id = row_result[0]

        # Global app (organization_id is NULL) - accessible to all authenticated users
        if org_id is None:
            return True

        # Org-scoped app - check if user is in the same org
        return org_id == user.organization_id


router = APIRouter(prefix="/ws", tags=["WebSocket"])


@router.websocket("/connect")
async def websocket_connect(
    websocket: WebSocket,
    channels: Annotated[list[str], Query()] = [],
):
    """
    WebSocket endpoint for real-time updates.

    Connect and subscribe to channels:
    - execution:{execution_id} - Execution updates and logs
    - user:{user_id} - User notifications
    - system - System broadcasts

    Query params:
        channels: List of channels to subscribe to

    Example:
        ws://localhost:8000/ws/connect?channels=execution:abc-123&channels=user:user-456

    Messages are JSON with structure:
        {
            "type": "execution_update" | "execution_log" | "notification" | "system_event",
            ...payload
        }
    """
    # Authenticate via header (query params not supported for security)
    user = await get_current_user_ws(websocket)

    if not user:
        # Must accept before closing, otherwise client sees HTTP 403
        await websocket.accept()
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Filter channels - users can only subscribe to their own user channel
    # and execution channels (we'll validate execution access separately)
    allowed_channels = []
    for channel in channels:
        if channel.startswith("user:"):
            # Users can only subscribe to their own notifications
            if channel == f"user:{user.user_id}":
                allowed_channels.append(channel)
        elif channel.startswith("execution:"):
            # Validate user has access to this execution
            execution_id = channel.split(":", 1)[1]
            if await can_access_execution(user, execution_id):
                allowed_channels.append(channel)
        elif channel == "package:install":
            # Package installation channel - shared, superusers only
            if user.is_superuser:
                allowed_channels.append(channel)
        elif channel.startswith("git:"):
            # Git job channels - ephemeral, job-specific UUIDs
            # Authorization: any authenticated user can subscribe
            # The job_id is a one-time UUID that only the requester knows
            # (returned by the API after queueing the job)
            allowed_channels.append(channel)
        elif channel.startswith("notification:"):
            # Notification channels - users can subscribe to their own
            if channel == f"notification:{user.user_id}":
                allowed_channels.append(channel)
            # Platform admins can subscribe to admin notifications
            elif channel == "notification:admins" and user.is_superuser:
                allowed_channels.append(channel)
        elif channel.startswith("chat:"):
            # Chat conversation channels - validate user owns the conversation
            conversation_id = channel.split(":", 1)[1]
            has_access, _ = await can_access_conversation(user, conversation_id)
            if has_access:
                allowed_channels.append(channel)
        elif channel.startswith("history:"):
            # History channels for real-time updates
            # history:user:{user_id} - Allow only for the user's own channel
            # history:GLOBAL - Allow only for platform admins
            if channel == f"history:user:{user.user_id}":
                allowed_channels.append(channel)
            elif channel == "history:GLOBAL" and user.is_superuser:
                allowed_channels.append(channel)
        elif channel.startswith("local-runner:"):
            # Local runner channels - users can subscribe to their own
            if channel == f"local-runner:{user.user_id}":
                allowed_channels.append(channel)
        elif channel.startswith("devrun:"):
            # Legacy dev run channels - users can subscribe to their own
            if channel == f"devrun:{user.user_id}":
                allowed_channels.append(channel)
        elif channel.startswith("cli-session:"):
            # CLI session channels - allow all (session ownership validated elsewhere)
            allowed_channels.append(channel)
        elif channel.startswith("cli-sessions:"):
            # CLI sessions list channel - users can subscribe to their own
            if channel == f"cli-sessions:{user.user_id}":
                allowed_channels.append(channel)
        elif channel.startswith("event-source:"):
            # Event source channels for real-time event updates
            # Platform admins can view all, org users can view their org's sources
            # Access is validated on event delivery, so we allow subscription
            allowed_channels.append(channel)
        elif channel.startswith("reindex:"):
            # Reindex job progress channels - platform admins only
            if user.is_superuser:
                allowed_channels.append(channel)
        elif channel.startswith("app:draft:"):
            # App Builder draft channels - validate user has access to the app
            app_id = channel.split(":", 2)[2]
            if await can_access_app(user, app_id):
                allowed_channels.append(channel)
        elif channel.startswith("app:live:"):
            # App Builder live channels - validate user has access to the app
            app_id = channel.split(":", 2)[2]
            if await can_access_app(user, app_id):
                allowed_channels.append(channel)
        elif channel == "file-activity":
            # File activity channel - platform admins only
            if user.is_superuser:
                allowed_channels.append(channel)
        elif channel.startswith("table:"):
            # Table channels — policy enforcement reintroduced in Tasks 11-12.
            # For now, any authenticated user may subscribe.
            allowed_channels.append(channel)
        elif channel == "system":
            allowed_channels.append(channel)
        elif channel.startswith("agent-run:"):
            # Agent run detail channels - any authenticated user can subscribe
            # Org-level access is enforced at the API query level
            allowed_channels.append(channel)
        elif channel == "agent-runs":
            # Agent run list channel for real-time updates
            allowed_channels.append(channel)
        elif channel.startswith("summary-backfill:"):
            # Summary backfill job progress — platform admins only
            if user.is_superuser:
                allowed_channels.append(channel)
        elif channel == "platform_workers":
            # Platform workers channel - diagnostics, platform admins only
            if user.is_superuser:
                allowed_channels.append(channel)

    # Always subscribe to user's own channel
    user_channel = f"user:{user.user_id}"
    if user_channel not in allowed_channels:
        allowed_channels.append(user_channel)

    # Track active chat tasks per conversation so they can be cancelled
    active_chat_tasks: dict[str, asyncio.Task] = {}
    pending_messages: dict[str, tuple[str, str | None]] = {}  # conversation_id -> (message, local_id)

    try:
        await manager.connect(websocket, allowed_channels)
        logger.info(f"WebSocket connected for user {user.user_id}, channels: {log_safe(allowed_channels)}")

        # Send connection confirmation
        await websocket.send_json({
            "type": "connected",
            "channels": allowed_channels,
            "userId": str(user.user_id)
        })

        # Keep connection alive and handle incoming messages
        while True:
            data = await websocket.receive_json()

            # Handle subscription changes
            if data.get("type") == "subscribe":
                new_channels = data.get("channels", [])
                for channel in new_channels:
                    # Validate and add subscription
                    if channel.startswith("execution:"):
                        # Validate execution access before subscribing
                        execution_id = channel.split(":", 1)[1]
                        if not await can_access_execution(user, execution_id):
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                            continue
                        if channel not in manager.connections:
                            manager.connections[channel] = set()
                        manager.connections[channel].add(websocket)
                        await websocket.send_json({
                            "type": "subscribed",
                            "channel": channel
                        })
                    elif channel.startswith("cli-session:"):
                        if channel not in manager.connections:
                            manager.connections[channel] = set()
                        manager.connections[channel].add(websocket)
                        await websocket.send_json({
                            "type": "subscribed",
                            "channel": channel
                        })
                    elif channel.startswith("event-source:"):
                        # Event source channels for real-time event updates
                        if channel not in manager.connections:
                            manager.connections[channel] = set()
                        manager.connections[channel].add(websocket)
                        await websocket.send_json({
                            "type": "subscribed",
                            "channel": channel
                        })
                    elif channel.startswith("history:"):
                        # History channels for real-time execution updates
                        # history:user:{user_id} - Allow only for the user's own channel
                        # history:GLOBAL - Allow only for platform admins
                        if channel == f"history:user:{user.user_id}" or (channel == "history:GLOBAL" and user.is_superuser):
                            if channel not in manager.connections:
                                manager.connections[channel] = set()
                            manager.connections[channel].add(websocket)
                            await websocket.send_json({
                                "type": "subscribed",
                                "channel": channel
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                    elif channel.startswith("app:draft:") or channel.startswith("app:live:"):
                        # App Builder channels - validate user has access to the app
                        app_id = channel.split(":", 2)[2]
                        if await can_access_app(user, app_id):
                            if channel not in manager.connections:
                                manager.connections[channel] = set()
                            manager.connections[channel].add(websocket)
                            await websocket.send_json({
                                "type": "subscribed",
                                "channel": channel
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                    elif channel == "package:install":
                        # Package installation channel - shared, superusers only
                        if user.is_superuser:
                            if channel not in manager.connections:
                                manager.connections[channel] = set()
                            manager.connections[channel].add(websocket)
                            await websocket.send_json({
                                "type": "subscribed",
                                "channel": channel
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                    elif channel.startswith("git:"):
                        # Git sync job channels - ephemeral, job-specific UUIDs
                        # Any authenticated user can subscribe (job ID is a secret token)
                        if channel not in manager.connections:
                            manager.connections[channel] = set()
                        manager.connections[channel].add(websocket)
                        await websocket.send_json({
                            "type": "subscribed",
                            "channel": channel
                        })
                    elif channel.startswith("agent-run:") or channel == "agent-runs":
                        # Agent run channels - any authenticated user can subscribe
                        if channel not in manager.connections:
                            manager.connections[channel] = set()
                        manager.connections[channel].add(websocket)
                        await websocket.send_json({
                            "type": "subscribed",
                            "channel": channel
                        })
                    elif channel.startswith("summary-backfill:"):
                        # Summary backfill job progress — platform admins only.
                        # Mirrors the initial-connect whitelist above; without this
                        # branch, late subscriptions from SummaryBackfillProgress
                        # silently no-op and no broadcast ever reaches the client
                        # (that's why cancel didn't dismiss and counters didn't tick).
                        if user.is_superuser:
                            if channel not in manager.connections:
                                manager.connections[channel] = set()
                            manager.connections[channel].add(websocket)
                            await websocket.send_json({
                                "type": "subscribed",
                                "channel": channel
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                    elif channel == "platform_workers":
                        # Platform workers channel - diagnostics, platform admins only
                        if user.is_superuser:
                            if channel not in manager.connections:
                                manager.connections[channel] = set()
                            manager.connections[channel].add(websocket)
                            await websocket.send_json({
                                "type": "subscribed",
                                "channel": channel
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "channel": channel,
                                "message": "Access denied"
                            })
                    elif channel.startswith("table:"):
                        # Table channels — policy enforcement reintroduced in Tasks 11-12.
                        # For now, any authenticated user may subscribe.
                        if channel not in manager.connections:
                            manager.connections[channel] = set()
                        manager.connections[channel].add(websocket)
                        await websocket.send_json({
                            "type": "subscribed",
                            "channel": channel
                        })

            elif data.get("type") == "unsubscribe":
                channel = data.get("channel")
                if channel and channel in manager.connections:
                    manager.connections[channel].discard(websocket)
                    await websocket.send_json({
                        "type": "unsubscribed",
                        "channel": channel
                    })

            elif data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

            elif data.get("type") == "chat":
                # Handle chat message - process and stream response
                conversation_id = data.get("conversation_id")
                message_text = data.get("message", "")
                local_id = data.get("local_id")  # Client-generated ID for dedup

                if not conversation_id or not message_text:
                    await websocket.send_json({
                        "type": "error",
                        "error": "Missing conversation_id or message"
                    })
                    continue

                # Validate access and get conversation
                has_access, conversation = await can_access_conversation(user, conversation_id)
                if not has_access or not conversation:
                    await websocket.send_json({
                        "type": "error",
                        "error": "Conversation not found or access denied"
                    })
                    continue

                # If a task is already running for this conversation, queue the
                # message instead of cancelling.  Cancelling mid-tool-call causes
                # interleaved messages that break the Anthropic API contract.
                existing_task = active_chat_tasks.get(conversation_id)
                if existing_task and not existing_task.done():
                    pending_messages[conversation_id] = (message_text, local_id)
                    continue

                # No running task — process immediately
                def _start_chat_task(cid: str, msg: str, lid: str | None) -> asyncio.Task:
                    t = asyncio.create_task(
                        _process_chat_message(
                            websocket=websocket,
                            user=user,
                            conversation_id=cid,
                            message=msg,
                            local_id=lid,
                        )
                    )
                    active_chat_tasks[cid] = t

                    def _on_task_done(_t: asyncio.Task, _cid: str = cid) -> None:
                        active_chat_tasks.pop(_cid, None)
                        queued = pending_messages.pop(_cid, None)
                        if queued:
                            q_msg, q_lid = queued
                            _start_chat_task(_cid, q_msg, q_lid)

                    t.add_done_callback(_on_task_done)
                    return t

                _start_chat_task(conversation_id, message_text, local_id)

            elif data.get("type") == "chat_stop":
                conversation_id = data.get("conversation_id")
                if conversation_id:
                    pending_messages.pop(conversation_id, None)
                    task = active_chat_tasks.pop(conversation_id, None)
                    if task and not task.done():
                        task.cancel()

    except WebSocketDisconnect:
        # Cancel all active chat tasks for this connection
        pending_messages.clear()
        for task in active_chat_tasks.values():
            if not task.done():
                task.cancel()
        active_chat_tasks.clear()
        manager.disconnect(websocket)
        logger.info(f"WebSocket disconnected for user {user.user_id}")
    except Exception as e:
        # Cancel all active chat tasks for this connection
        pending_messages.clear()
        for task in active_chat_tasks.values():
            if not task.done():
                task.cancel()
        active_chat_tasks.clear()
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


@router.websocket("/execution/{execution_id}")
async def websocket_execution(
    websocket: WebSocket,
    execution_id: str,
):
    """
    Convenience endpoint for subscribing to a single execution.

    Equivalent to connecting with channels=execution:{execution_id}
    """
    user = await get_current_user_ws(websocket)

    if not user:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Validate user has access to this execution
    if not await can_access_execution(user, execution_id):
        await websocket.close(code=4003, reason="Access denied")
        return

    channel = f"execution:{execution_id}"

    try:
        await manager.connect(websocket, [channel])
        logger.info(f"WebSocket connected to execution {log_safe(execution_id)}")

        await websocket.send_json({
            "type": "connected",
            "executionId": execution_id
        })

        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


async def _generate_conversation_title(
    db,
    conversation: Conversation,
    user_message: str,
) -> str | None:
    """
    Generate a concise title for a conversation using LLM.

    Returns the generated title or None if generation fails.
    """
    from src.services.llm import get_llm_client, LLMMessage

    try:
        llm_client = await get_llm_client(db)

        # Use a simple prompt to generate a title
        response = await llm_client.complete(
            messages=[
                LLMMessage(
                    role="system",
                    content="Generate a very short, concise title (3-6 words max) for a conversation that starts with the following message. Respond with ONLY the title, no quotes or punctuation at the end.",
                ),
                LLMMessage(
                    role="user",
                    content=user_message,
                ),
            ],
            max_tokens=1024,
        )

        if response.content:
            # Clean up the title - remove quotes, limit length
            title = response.content.strip().strip('"\'')
            # Truncate if too long (max 100 chars)
            if len(title) > 100:
                title = title[:97] + "..."
            return title

    except Exception as e:
        logger.warning(f"Failed to generate conversation title: {e}")

    return None


async def _process_chat_message(
    websocket: WebSocket,
    user: UserPrincipal,
    conversation_id: str,
    message: str,
    local_id: str | None = None,
) -> None:
    """
    Process a chat message and stream the response.

    Sends streaming chunks directly to the WebSocket, then broadcasts
    the final message to the chat channel for any other subscribers.

    DB connections are only held for short discrete operations (loading
    conversation, saving messages, etc.) — never during LLM streaming.

    Args:
        websocket: The WebSocket connection
        user: The authenticated user
        conversation_id: The conversation ID
        message: The user's message
    """
    from src.core.database import get_session_factory
    from src.services.agent_executor import AgentExecutor

    try:
        session_factory = get_session_factory()
        conv_uuid = UUID(conversation_id)

        # Load conversation in a short-lived session (released before streaming)
        async with session_factory() as db:
            result = await db.execute(
                select(Conversation)
                .options(
                    selectinload(Conversation.agent).selectinload(Agent.tools),
                    selectinload(Conversation.agent).selectinload(Agent.delegated_agents),
                    selectinload(Conversation.user),
                )
                .where(Conversation.id == conv_uuid)
            )
            conversation = result.scalar_one_or_none()

        if not conversation:
            await websocket.send_json({
                "type": "error",
                "conversation_id": conversation_id,
                "error": "Conversation not found"
            })
            return

        # Check if conversation needs a title (no title set yet)
        needs_title = conversation.title is None

        # Execute chat — executor manages its own short-lived sessions
        executor = AgentExecutor(session_factory)

        # Track streamed content so we can persist partial responses on cancellation
        streamed_content = ""
        assistant_message_id: str | None = None

        try:
            async for chunk in executor.chat(
                agent=conversation.agent,
                conversation=conversation,
                user_message=message,
                stream=True,
                local_id=local_id,
            ):
                # Track partial content from deltas
                if chunk.type == "delta" and chunk.content:
                    streamed_content += chunk.content
                elif chunk.type == "message_start" and chunk.assistant_message_id:
                    assistant_message_id = chunk.assistant_message_id
                elif chunk.type == "assistant_message_end":
                    # Text segment was saved by executor; reset for next segment
                    streamed_content = ""
                    assistant_message_id = None

                # Send chunk to WebSocket with conversation_id for client routing
                chunk_data = chunk.model_dump(exclude_none=True)
                chunk_data["conversation_id"] = conversation_id
                await websocket.send_json(chunk_data)
        except asyncio.CancelledError:
            logger.info(f"Chat processing cancelled for conversation {log_safe(conversation_id)}")

            # Save partial assistant response if we have streamed content
            # that hasn't been saved yet (no assistant_message_end was received)
            if streamed_content:
                from src.models.enums import MessageRole

                await executor._save_message(
                    conversation_id=UUID(conversation_id),
                    role=MessageRole.ASSISTANT,
                    content=streamed_content,
                    message_id=UUID(assistant_message_id) if assistant_message_id else None,
                )

            try:
                await websocket.send_json({
                    "type": "done",
                    "conversation_id": conversation_id,
                })
            except Exception:
                pass  # WebSocket may already be closed
            return

        # Generate title if this is a new conversation (no title yet)
        if needs_title:
            async with session_factory() as db:
                title = await _generate_conversation_title(db, conversation, message)
                if title:
                    conv = await db.get(Conversation, conv_uuid)
                    if conv:
                        conv.title = title
                    await db.commit()

            # Send title update to client AFTER commit
            if title:
                await websocket.send_json({
                    "type": "title_update",
                    "conversation_id": conversation_id,
                    "title": title,
                })

    except Exception as e:
        logger.error(f"Chat processing error: {e}", exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "conversation_id": conversation_id,
                "error": str(e)
            })
        except Exception:
            pass  # WebSocket may be closed
