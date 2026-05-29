"""
WebSocket PubSub Infrastructure

Provides real-time updates for:
- Execution status changes
- Log streaming
- System notifications

Uses Redis pub/sub for scalability across multiple API instances.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

if TYPE_CHECKING:
    from src.models.orm.agent_runs import AgentRun

from fastapi import WebSocket

from src.config import get_settings
from src.core.cache.redis_client import get_redis
from src.core.log_safety import log_safe
from src.core.redis_reconnect import ResilientPubSubListener

logger = logging.getLogger(__name__)


@dataclass
class ConnectionManager:
    """
    Manages WebSocket connections and Redis pub/sub subscriptions.

    Channels:
    - execution:{execution_id} - Execution status updates and logs
    - user:{user_id} - User-specific notifications
    - system - System-wide broadcasts
    """

    # Active WebSocket connections per channel
    connections: dict[str, set[WebSocket]] = field(default_factory=dict)
    # Resilient pub/sub listener for receiving messages
    _pubsub_listener: ResilientPubSubListener | None = None

    async def connect(self, websocket: WebSocket, channels: list[str]) -> None:
        """
        Accept WebSocket connection and subscribe to channels.

        Args:
            websocket: FastAPI WebSocket connection
            channels: List of channels to subscribe to
        """
        await websocket.accept()

        # Ensure Redis listener is running for cross-container messages
        # This fixes a race condition where the scheduler publishes progress
        # before the API's Redis listener is started
        if not self._pubsub_listener or not self._pubsub_listener.is_healthy():
            await self._init_redis()

        for channel in channels:
            if channel not in self.connections:
                self.connections[channel] = set()
            self.connections[channel].add(websocket)
            logger.debug(f"WebSocket connected to channel: {log_safe(channel)}")

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove WebSocket from all channels."""
        for channel, sockets in list(self.connections.items()):
            sockets.discard(websocket)
            if not sockets:
                del self.connections[channel]
        logger.debug("WebSocket disconnected")

    async def broadcast(self, channel: str, message: dict[str, Any]) -> None:
        """
        Broadcast message to all connections on a channel.
        Publishes to Redis for cross-instance delivery (including this instance).

        Args:
            channel: Channel name
            message: Message payload
        """
        # Publish to Redis - the Redis listener will deliver to local connections
        # This avoids double-delivery (once here, once from Redis listener)
        published = await self._publish_to_redis(channel, message)

        # Only send locally if Redis is unavailable (fallback mode)
        if not published:
            await self._send_local(channel, message)

    async def _send_local(self, channel: str, message: dict[str, Any]) -> None:
        """Send message to local WebSocket connections.

        For `table:` channels, the connection MUST have a per-message dispatcher
        attached (via the websocket router for policy-driven filtering). The
        dispatcher receives the raw message and decides what — if anything — to
        deliver to the client. Without a dispatcher, the connection simply does
        not receive table updates: subscribing to `table:` outside the
        policy-aware router is not supported.
        """
        if channel not in self.connections:
            return

        dead_connections = set()
        is_table_channel = channel.startswith("table:")
        message_json = json.dumps(message) if not is_table_channel else None

        for websocket in self.connections[channel]:
            try:
                if is_table_channel:
                    dispatcher = getattr(websocket, "_table_dispatcher", None)
                    if dispatcher is None:
                        continue
                    await dispatcher(channel, message)
                else:
                    assert message_json is not None
                    await websocket.send_text(message_json)
            except Exception:
                dead_connections.add(websocket)

        # Clean up dead connections
        for ws in dead_connections:
            self.disconnect(ws)

    async def _publish_to_redis(self, channel: str, message: dict[str, Any]) -> bool:
        """
        Publish message to Redis pub/sub.

        Returns:
            bool: True if successfully published, False otherwise
        """
        try:
            async with get_redis() as r:
                await r.publish(
                    f"bifrost:{channel}",
                    json.dumps(message),
                )
            return True
        except Exception as e:
            logger.warning(f"Failed to publish to Redis: {e}")
            return False

    async def _init_redis(self) -> None:
        """Initialize the Redis listener."""
        settings = get_settings()
        try:
            if self._pubsub_listener:
                await self._pubsub_listener.stop()

            # Create resilient listener for receiving messages
            async def on_message(channel: str, data: dict) -> None:
                # Strip "bifrost:" prefix from channel
                local_channel = channel.replace("bifrost:", "")
                await self._send_local(local_channel, data)

            self._pubsub_listener = ResilientPubSubListener(
                redis_url=settings.redis_url,
                patterns=["bifrost:*"],
                on_message=on_message,
            )
            await self._pubsub_listener.start()
            logger.info("Redis pub/sub initialized (with auto-reconnect)")
        except Exception as e:
            logger.warning(f"Failed to connect to Redis: {e}")
            self._pubsub_listener = None

    async def close(self) -> None:
        """Clean up connections."""
        if self._pubsub_listener:
            await self._pubsub_listener.stop()


# Global connection manager instance
manager = ConnectionManager()


# Convenience functions for common pubsub operations

async def publish_execution_update(
    execution_id: str | UUID,
    status: str,
    data: dict[str, Any] | None = None
) -> None:
    """
    Publish execution status update.

    Args:
        execution_id: Execution ID
        status: New status (Pending, Running, Success, Failed, etc.)
        data: Additional data (result, error, logs, etc.)
    """
    message = {
        "type": "execution_update",
        "executionId": str(execution_id),
        "status": status,
        **(data or {})
    }
    await manager.broadcast(f"execution:{execution_id}", message)


async def publish_execution_log(
    execution_id: str | UUID,
    level: str,
    message: str,
    data: dict[str, Any] | None = None
) -> None:
    """
    Publish execution log entry (async version).

    Args:
        execution_id: Execution ID
        level: Log level (debug, info, warning, error)
        message: Log message
        data: Additional log data
    """
    log_entry = {
        "type": "execution_log",
        "executionId": str(execution_id),
        "level": level,
        "message": message,
        "data": data
    }
    await manager.broadcast(f"execution:{execution_id}", log_entry)


async def publish_history_update(
    execution_id: str | UUID,
    status: str,
    executed_by: str | UUID | None,
    executed_by_name: str,
    workflow_name: str,
    org_id: str | UUID | None = None,
    started_at: Any = None,
    completed_at: Any = None,
    duration_ms: int | None = None,
) -> None:
    """
    Publish execution update to history channels.

    Broadcasts to:
    - history:user:{executed_by} - for the user who ran the execution
    - history:GLOBAL - for platform admins watching all executions

    Args:
        execution_id: Execution ID
        status: Execution status (Pending, Running, Success, Failed, etc.)
        executed_by: User ID who ran the execution
        executed_by_name: Display name of the user
        workflow_name: Name of the workflow
        org_id: Organization ID (if org-scoped)
        started_at: When the execution started
        completed_at: When the execution completed
        duration_ms: Execution duration in milliseconds
    """

    message = {
        "type": "history_update",
        "execution_id": str(execution_id),
        "workflow_name": workflow_name,
        "status": status,
        "executed_by": str(executed_by),
        "executed_by_name": executed_by_name,
        "org_id": str(org_id) if org_id else None,
        "started_at": started_at.isoformat() if isinstance(started_at, datetime) else started_at,
        "completed_at": completed_at.isoformat() if isinstance(completed_at, datetime) else completed_at,
        "duration_ms": duration_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Always publish to user's channel and global admin channel
    await manager.broadcast(f"history:user:{executed_by}", message)
    await manager.broadcast("history:GLOBAL", message)


# =============================================================================
# Agent Run Pub/Sub
# =============================================================================


async def publish_agent_run_update(
    run: "AgentRun",
    agent_name: str,
) -> None:
    """
    Publish agent run status update.

    Broadcasts to:
    - agent-run:{run_id} - for the detail page
    - agent-runs - for the list page
    """
    message = {
        "type": "agent_run_update",
        "run_id": str(run.id),
        "agent_id": str(run.agent_id),
        "agent_name": agent_name,
        "status": run.status,
        "trigger_type": run.trigger_type,
        "iterations_used": run.iterations_used or 0,
        "tokens_used": run.tokens_used or 0,
        "duration_ms": run.duration_ms,
        "error": run.error,
        "org_id": str(run.org_id) if run.org_id else None,
        "started_at": run.started_at.isoformat() if isinstance(run.started_at, datetime) else run.started_at,
        "completed_at": run.completed_at.isoformat() if isinstance(run.completed_at, datetime) else run.completed_at,
        "summary_status": getattr(run, "summary_status", None),
        "summary_error": getattr(run, "summary_error", None),
        "asked": getattr(run, "asked", None),
        "did": getattr(run, "did", None),
        "confidence": float(run.confidence) if run.confidence is not None else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast(f"agent-run:{run.id}", message)
    await manager.broadcast("agent-runs", message)


async def publish_summary_backfill_update(
    job_id: str | UUID,
    payload: dict[str, Any],
) -> None:
    """Broadcast a summary backfill job progress update.

    Broadcasts to ``summary-backfill:{job_id}``. Platform admins only
    (enforced in the websocket router's channel whitelist).
    """
    message = {
        "type": "summary_backfill_update",
        "job_id": str(job_id),
        **payload,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast(f"summary-backfill:{job_id}", message)


async def publish_agent_run_step(
    run_id: str | UUID,
    step: dict[str, Any],
) -> None:
    """
    Publish a new agent run step (for live detail page updates).

    Broadcasts to agent-run:{run_id} channel.
    """
    message = {
        "type": "agent_run_step",
        "run_id": str(run_id),
        "step": step,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast(f"agent-run:{run_id}", message)


async def publish_user_notification(
    user_id: str | UUID,
    notification_type: str,
    title: str,
    message: str,
    data: dict[str, Any] | None = None
) -> None:
    """
    Publish user notification.

    Args:
        user_id: User ID
        notification_type: Type (info, success, warning, error)
        title: Notification title
        message: Notification message
        data: Additional data
    """
    notification = {
        "type": "notification",
        "notificationType": notification_type,
        "title": title,
        "message": message,
        **(data or {})
    }
    await manager.broadcast(f"user:{user_id}", notification)


async def publish_system_event(
    event_type: str,
    data: dict[str, Any]
) -> None:
    """
    Publish system-wide event.

    Args:
        event_type: Event type
        data: Event data
    """
    event = {
        "type": "system_event",
        "eventType": event_type,
        **data
    }
    await manager.broadcast("system", event)


# =============================================================================
# Local Runner Pub/Sub (CLI<->Web Communication)
# =============================================================================


async def publish_local_runner_state_update(
    user_id: str | UUID,
    state: dict[str, Any] | None,
) -> None:
    """
    Publish local runner state update.

    Notifies the web UI when CLI registers workflows or state changes.

    Args:
        user_id: User ID
        state: Current local runner state (None if no active session)
    """
    message = {
        "type": "local_runner_state_update",
        "state": state,
    }
    await manager.broadcast(f"local-runner:{user_id}", message)


async def publish_cli_session_update(
    user_id: str | UUID,
    session_id: str,
    state: dict[str, Any] | None,
) -> None:
    """
    Publish CLI session state update.

    Notifies the web UI when CLI session state changes.

    Args:
        user_id: User ID
        session_id: CLI session ID
        state: Current CLI session state (None if session deleted)
    """
    message = {
        "type": "cli_session_update",
        "session_id": session_id,
        "state": state,
    }
    # Broadcast to both session-specific and user-level channels
    await manager.broadcast(f"cli-session:{session_id}", message)
    await manager.broadcast(f"cli-sessions:{user_id}", message)


# =============================================================================
# App Builder Pub/Sub
# =============================================================================
# These functions enable real-time updates for App Builder applications.
# - Draft mode viewers see changes instantly when MCP/editor makes modifications
# - Published app users see a "New Version Available" indicator when republished


async def publish_app_draft_update(
    app_id: str,
    user_id: str,
    user_name: str,
    entity_type: str,  # 'page' | 'component' | 'app'
    entity_id: str,
    page_id: str | None = None,
) -> None:
    """
    Broadcast draft changes to app:draft:{app_id} channel.

    Notifies draft mode viewers when pages, components, or app settings
    are modified by MCP tools or the visual editor.

    Args:
        app_id: Application ID
        user_id: User who made the change
        user_name: Display name of the user
        entity_type: Type of entity changed ('page', 'component', 'app')
        entity_id: ID of the changed entity
        page_id: Page ID (for component changes)
    """

    channel = f"app:draft:{app_id}"
    message = {
        "type": "app_draft_update",
        "appId": app_id,
        "entityType": entity_type,
        "entityId": entity_id,
        "pageId": page_id,
        "userId": user_id,
        "userName": user_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast(channel, message)


async def publish_app_code_file_update(
    app_id: str,
    user_id: str,
    user_name: str,
    path: str,
    source: str | None = None,
    compiled: str | None = None,
    action: str = "update",  # 'create', 'update', 'delete'
    bundle: dict | None = None,
    error: dict | None = None,
) -> None:
    """
    Broadcast code file changes with full content to app:draft:{app_id} channel.

    This specialized function includes the file content, enabling
    real-time preview updates without additional API calls.

    Args:
        app_id: Application ID
        user_id: User who made the change
        user_name: Display name of the user
        path: File path (e.g., 'pages/index', 'components/Button')
        source: Source code content (None for delete)
        compiled: Compiled JS content (None for delete or if not compiled)
        action: Type of change ('create', 'update', 'delete')
        bundle: Bundle manifest info after a successful rebuild.
            Shape: {"entry": str, "css": str | None, "duration_ms": int}.
            Clients use this as a signal to reload the bundle entry.
        error: Bundle build failure info.
            Shape: {"messages": [{"text", "file", "line", "column"}]}.
            Clients show this as a banner over the last-good bundle.
    """

    channel = f"app:draft:{app_id}"
    message = {
        "type": "app_code_file_update",
        "appId": app_id,
        "action": action,
        "path": path,
        "source": source,
        "compiled": compiled,
        "bundle": bundle,
        "error": error,
        "userId": user_id,
        "userName": user_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast(channel, message)


async def publish_app_published(
    app_id: str,
    user_id: str,
    user_name: str,
    new_version_id: str,
) -> None:
    """
    Broadcast publish event to app:live:{app_id} channel.

    Notifies live app viewers that a new version has been published,
    allowing them to see a "New Version Available" indicator.

    Args:
        app_id: Application ID
        user_id: User who published the app
        user_name: Display name of the user
        new_version_id: ID of the newly published version
    """

    channel = f"app:live:{app_id}"
    message = {
        "type": "app_published",
        "appId": app_id,
        "newVersionId": new_version_id,
        "userId": user_id,
        "userName": user_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast(channel, message)


# =============================================================================
# Git Desktop Operations Pub/Sub (API -> Scheduler Communication)
# =============================================================================


async def publish_git_operation(
    job_id: str,
    org_id: str,
    user_id: str,
    user_email: str,
    op_type: str,
    **kwargs: Any,
) -> None:
    """
    Request a git desktop operation from the scheduler.

    The scheduler listens on `bifrost:scheduler:git-op` and dispatches
    to the appropriate service method based on op_type.

    Args:
        job_id: Unique job ID for tracking
        org_id: Organization ID
        user_id: User who initiated the operation
        user_email: Email of the user
        op_type: Operation type (git_fetch, git_commit, git_pull, git_push, git_status, git_resolve, git_diff)
        **kwargs: Additional operation-specific data
    """
    message: dict[str, Any] = {
        "type": op_type,
        "jobId": job_id,
        "orgId": org_id,
        "userId": user_id,
        "userEmail": user_email,
        **kwargs,
    }
    await manager._publish_to_redis("scheduler:git-op", message)


async def publish_git_progress(
    job_id: str,
    phase: str,
    current: int = 0,
    total: int = 0,
) -> None:
    """
    Publish a git operation progress update to the frontend.

    Args:
        job_id: Unique job ID (matches the job that triggered the operation)
        phase: Human-readable phase string (e.g. "Fetching remote...")
        current: Current entity index (1-based) for progress percentage
        total: Total entities to import
    """
    message: dict[str, Any] = {
        "type": "git_progress",
        "jobId": job_id,
        "phase": phase,
        "current": current,
        "total": total,
    }
    await manager.broadcast(f"git:{job_id}", message)


async def publish_git_op_completed(
    job_id: str,
    status: str,
    result_type: str,
    data: dict[str, Any] | None = None,
    error: str | None = None,
    preview: dict[str, Any] | None = None,
    pulled: int = 0,
    pushed: int = 0,
    commit_sha: str | None = None,
    conflicts: list[dict[str, Any]] | None = None,
) -> None:
    """
    Publish git operation completion.

    Broadcasts to git:{job_id} channel with the result.
    Also stores in Redis for HTTP polling (5-minute TTL).

    Args:
        job_id: Unique job ID
        status: Completion status (success, failed, conflict)
        result_type: Which operation completed (fetch, commit, pull, push, status, resolve, diff)
        data: Result data dict
        error: Error message if failed
        preview: Sync preview data (for sync_preview ops, consumed by CLI)
        pulled: Number of files pulled (for sync_execute ops)
        pushed: Number of files pushed (for sync_execute ops)
        commit_sha: Commit SHA if created (for sync_execute ops)
        conflicts: List of merge conflict dicts (for sync ops with conflicts)
    """
    completion_message: dict[str, Any] = {
        "type": "git_op_complete",
        "jobId": job_id,
        "status": status,
        "resultType": result_type,
    }
    if data is not None:
        completion_message["data"] = data
    if error is not None:
        completion_message["error"] = error
    if preview is not None:
        completion_message["preview"] = preview
    if pulled:
        completion_message["pulled"] = pulled
    if pushed:
        completion_message["pushed"] = pushed
    if commit_sha is not None:
        completion_message["commit_sha"] = commit_sha
    if conflicts is not None:
        completion_message["conflicts"] = conflicts

    await manager.broadcast(f"git:{job_id}", completion_message)

    # Store result in Redis for HTTP polling
    try:
        from src.core.redis_client import get_redis_client

        redis_client = get_redis_client()
        if redis_client:
            result_key = f"bifrost:job:{job_id}"
            await redis_client.setex(
                result_key,
                300,  # 5 minutes TTL
                json.dumps(completion_message),
            )
    except Exception as e:
        logger.warning(f"Failed to store job result in Redis: {e}")


# =============================================================================
# Worker Monitoring Pub/Sub
# =============================================================================
# These functions enable real-time monitoring of worker processes.
# - Heartbeats: Periodic updates with process state, memory, executions
# - Events: Lifecycle events (online, offline, state changes)


async def publish_worker_heartbeat(heartbeat: dict[str, Any]) -> None:
    """
    Publish worker heartbeat to platform_workers channel and store in Redis.

    Contains detailed state about worker processes and their executions.
    Published every heartbeat interval (default 10s).

    The heartbeat is both:
    1. Broadcast to WebSocket subscribers for real-time updates
    2. Stored in Redis for API queries (with TTL)

    Args:
        heartbeat: Dict with worker_id, timestamp, processes, queue info
    """
    # Broadcast to WebSocket subscribers
    await manager.broadcast("platform_workers", heartbeat)

    # Store in Redis for API queries
    worker_id = heartbeat.get("worker_id")
    if worker_id:
        try:
            from src.core.redis_client import get_redis_client

            redis_client = get_redis_client()
            heartbeat_key = f"bifrost:pool:{worker_id}:heartbeat"
            # Store with TTL slightly longer than heartbeat interval
            await redis_client.setex(heartbeat_key, 60, json.dumps(heartbeat))
        except Exception as e:
            logger.warning(f"Failed to store heartbeat in Redis: {e}")


async def publish_worker_event(event: dict[str, Any]) -> None:
    """
    Publish worker lifecycle event to platform_workers channel.

    Events include:
    - worker_online: Worker registered and ready
    - worker_offline: Worker shutting down gracefully
    - process_state_changed: Worker process state changed
    - execution_stuck: Execution marked as stuck

    Args:
        event: Dict with type, worker_id, and event-specific data
    """
    await manager.broadcast("platform_workers", event)


async def publish_pool_config_changed(
    worker_id: str,
    old_min: int,
    old_max: int,
    new_min: int,
    new_max: int,
) -> None:
    """
    Publish pool configuration change event to platform_workers channel.

    Sent when min/max workers are updated via API or command.

    Args:
        worker_id: Worker/pool ID that was reconfigured
        old_min: Previous minimum workers
        old_max: Previous maximum workers
        new_min: New minimum workers
        new_max: New maximum workers
    """

    message = {
        "type": "pool_config_changed",
        "worker_id": worker_id,
        "old_min": old_min,
        "old_max": old_max,
        "new_min": new_min,
        "new_max": new_max,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast("platform_workers", message)


async def publish_pool_scaling(
    worker_id: str,
    action: str,
    processes_affected: int,
) -> None:
    """
    Publish pool scaling event to platform_workers channel.

    Sent when the pool scales up or down, or when processes are recycled.

    Args:
        worker_id: Worker/pool ID that is scaling
        action: Scaling action ('scale_up', 'scale_down', 'recycle_all')
        processes_affected: Number of processes affected by this action
    """

    message = {
        "type": "pool_scaling",
        "worker_id": worker_id,
        "action": action,
        "processes_affected": processes_affected,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast("platform_workers", message)


async def publish_reimport_request(job_id: str) -> None:
    """Publish a reimport request to the scheduler."""
    await manager._publish_to_redis("scheduler:reimport", {"action": "reimport", "job_id": job_id})


async def publish_embedding_reindex_request(notification_id: str) -> None:
    """Publish an embedding-reindex request to the scheduler.

    The scheduler reads the saved embedding config and re-embeds every
    knowledge_store row, pushing progress through the notification at
    `notification_id`.
    """
    await manager._publish_to_redis(
        "scheduler:embedding-reindex",
        {"action": "embedding_reindex", "notification_id": notification_id},
    )


async def publish_pool_progress(
    worker_id: str,
    action: str,
    current: int,
    total: int,
    message: str,
) -> None:
    """
    Publish real-time pool operation progress to platform_workers channel.

    Provides granular progress updates during pool operations like:
    - Scaling up: "Spawning process 3 of 4..."
    - Scaling down: "Terminating process 2 of 3..."
    - Recycling: "Recycling process 1 of 5..."

    Args:
        worker_id: Worker/pool ID performing the operation
        action: Operation type ('scale_up', 'scale_down', 'recycle_all')
        current: Current process number (1-indexed)
        total: Total processes to be affected
        message: Human-readable progress message
    """

    payload = {
        "type": "pool_progress",
        "worker_id": worker_id,
        "action": action,
        "current": current,
        "total": total,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.broadcast("platform_workers", payload)


# =============================================================================
# File Activity Broadcast (CLI push/watch awareness)
# =============================================================================


async def publish_file_activity(
    user_id: str,
    user_name: str,
    activity_type: str,  # "file_push" | "file_delete" | "entity_change" | "watch_start" | "watch_stop"
    prefix: str = "",
    file_count: int = 0,
    is_watch: bool = False,
    paths: list[str] | None = None,
    session_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    data: dict | None = None,
) -> None:
    """Broadcast file activity to admin-only file-activity channel."""
    payload: dict = {
        "type": activity_type,
        "user_id": user_id,
        "user_name": user_name,
        "prefix": prefix,
        "file_count": file_count,
        "is_watch": is_watch,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if paths is not None:
        payload["paths"] = paths
    if session_id is not None:
        payload["session_id"] = session_id
    if entity_type is not None:
        payload["entity_type"] = entity_type
    if entity_id is not None:
        payload["entity_id"] = entity_id
    if action is not None:
        payload["action"] = action
    if data is not None:
        payload["data"] = data
    await manager.broadcast("file-activity", payload)


# =============================================================================
# Table Pub/Sub
# =============================================================================


class _Publisher:
    """Thin publish helper that delegates to the global ConnectionManager.

    Exists so callers (and tests) have a stable seam — `pubsub.publisher.publish`
    — for emitting channel messages, independent of the concrete transport.
    """

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        await manager.broadcast(channel, payload)


publisher = _Publisher()


async def publish_document_change(
    table_id: str,
    action: Literal["insert", "update", "delete"],
    old_row: dict | None,
    new_row: dict | None,
) -> None:
    """Emit a document-change event with both pre/post row states.

    Subscribers compare old_row and new_row visibility to compute the
    four-way (still-visible / became-visible / no-longer-visible / still-hidden)
    fanout decision.
    """
    payload = {
        "type": "document_change",
        "table_id": table_id,
        "action": action,
        "old_row": old_row,
        "new_row": new_row,
    }
    channel = f"table:{table_id}"
    await publisher.publish(channel, payload=payload)


async def publish_policy_changed(table_id: str) -> None:
    """Notify subscribers that the table's policies were edited.

    The websocket layer re-runs subscription authorization on each message
    of this type and may emit subscription_revoked.
    """
    channel = f"table:{table_id}"
    await publisher.publish(channel, payload={"type": "policy_changed", "table_id": table_id})
