"""
Redis Client for Execution Management

Provides:
1. Pending execution storage (API writes, Worker reads)
2. Sync execution results via BLPOP/RPUSH pattern
3. Cancellation flag management

Execution Flow:
1. API writes pending execution to Redis
2. API publishes to RabbitMQ
3. Worker reads pending execution from Redis
4. Worker writes to PostgreSQL and executes
5. For sync: Worker pushes result, API's BLPOP returns
"""

import json
import logging
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any, Awaitable, TypedDict, cast

import redis.asyncio as redis

from src.config import get_settings
from src.core.log_safety import log_safe

logger = logging.getLogger(__name__)

# Redis key prefixes
RESULT_KEY_PREFIX = "bifrost:result:"
PENDING_KEY_PREFIX = "bifrost:exec:"
PENDING_KEY_SUFFIX = ":pending"
ENDPOINT_WORKFLOW_CACHE_PREFIX = "bifrost:endpoint:workflow:"
WORKFLOW_METADATA_CACHE_PREFIX = "bifrost:workflow:"

# Cache TTLs
ENDPOINT_WORKFLOW_CACHE_TTL_SECONDS = 300  # 5 minutes (by name, for endpoints)
WORKFLOW_METADATA_CACHE_TTL_SECONDS = 300  # 5 minutes (by id, for execution)

# Default timeout for sync execution (5 minutes)
DEFAULT_TIMEOUT_SECONDS = 300

# Result TTL for auto-cleanup (60 seconds after push)
RESULT_TTL_SECONDS = 60

# Pending execution TTL (1 hour safety for orphaned entries)
PENDING_EXECUTION_TTL_SECONDS = 3600


class PendingExecution(TypedDict):
    """Schema for pending execution data stored in Redis."""
    execution_id: str
    workflow_id: str | None  # UUID from database (None for inline code)
    script_name: str | None  # Name for inline code execution
    parameters: dict[str, Any]
    org_id: str | None
    user_id: str
    user_name: str
    user_email: str
    form_id: str | None
    api_key_id: str | None  # Workflow ID whose API key triggered this (for audit trail)
    startup: dict[str, Any] | None  # Launch workflow results (available via context.startup)
    sync: bool  # If True, worker pushes result to Redis for sync execution
    is_platform_admin: bool  # Whether the caller is a platform admin
    event: dict[str, Any] | None  # EventContext fields if event-triggered; None otherwise
    created_at: str  # ISO format
    cancelled: bool


class RedisClient:
    """
    Redis client wrapper for execution management.

    Provides:
    - Pending execution: set/get/delete/cancel pending executions
    - Sync results: push_result/wait_for_result via BLPOP
    - Cancellation: set_cancel_flag for running executions
    """

    def __init__(self):
        self._redis: redis.Redis | None = None

    async def _get_redis(self) -> redis.Redis:
        """Get or create Redis connection."""
        if self._redis is None:
            settings = get_settings()
            self._redis = redis.from_url(
                settings.redis_url,
                decode_responses=True,
            )
        return self._redis

    # =========================================================================
    # Pending Execution Methods (API writes, Worker reads)
    # =========================================================================

    async def set_pending_execution(
        self,
        execution_id: str,
        workflow_id: str | None,
        parameters: dict[str, Any],
        org_id: str | None,
        user_id: str,
        user_name: str,
        user_email: str,
        form_id: str | None = None,
        script_name: str | None = None,
        startup: dict[str, Any] | None = None,
        api_key_id: str | None = None,
        sync: bool = False,
        is_platform_admin: bool = False,
        event: dict[str, Any] | None = None,
    ) -> None:
        """
        Store pending execution in Redis.

        Called by API before publishing to RabbitMQ.
        Worker will read this data when it picks up the job.

        Args:
            execution_id: Unique execution ID (UUID)
            workflow_id: UUID of workflow to execute (None for inline code)
            parameters: Workflow input parameters
            org_id: Organization ID (None for GLOBAL scope)
            user_id: User ID who initiated execution
            user_name: Display name of user
            user_email: Email of user
            form_id: Optional form ID if triggered by form
            script_name: Optional script name for inline code execution
            startup: Optional startup data from launch workflow (available via context.startup)
            api_key_id: Optional workflow ID whose API key triggered this execution
            sync: If True, worker will push result to Redis for sync execution
        """
        redis_client = await self._get_redis()
        key = f"{PENDING_KEY_PREFIX}{execution_id}{PENDING_KEY_SUFFIX}"

        data: PendingExecution = {
            "execution_id": execution_id,
            "workflow_id": workflow_id,
            "script_name": script_name,
            "parameters": parameters,
            "org_id": org_id,
            "user_id": user_id,
            "user_name": user_name,
            "user_email": user_email,
            "form_id": form_id,
            "api_key_id": api_key_id,
            "startup": startup,
            "sync": sync,
            "is_platform_admin": is_platform_admin,
            "event": event,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "cancelled": False,
        }

        try:
            await redis_client.setex(
                key,
                PENDING_EXECUTION_TTL_SECONDS,
                json.dumps(data),
            )
            logger.debug(f"Stored pending execution: {key}")
        except Exception as e:
            logger.error(f"Failed to store pending execution: {e}")
            raise

    async def get_pending_execution(
        self,
        execution_id: str,
    ) -> PendingExecution | None:
        """
        Get pending execution data from Redis.

        Called by Worker when it picks up a job from RabbitMQ.

        Args:
            execution_id: Execution ID

        Returns:
            PendingExecution dict or None if not found
        """
        redis_client = await self._get_redis()
        key = f"{PENDING_KEY_PREFIX}{execution_id}{PENDING_KEY_SUFFIX}"

        try:
            data = await redis_client.get(key)
            if data is None:
                logger.warning(f"Pending execution not found: {execution_id}")
                return None
            return json.loads(data)
        except Exception as e:
            logger.error(f"Failed to get pending execution: {e}")
            raise

    async def delete_pending_execution(self, execution_id: str) -> None:
        """
        Delete pending execution from Redis.

        Called by Worker after writing to PostgreSQL.

        Args:
            execution_id: Execution ID
        """
        redis_client = await self._get_redis()
        key = f"{PENDING_KEY_PREFIX}{execution_id}{PENDING_KEY_SUFFIX}"

        try:
            await redis_client.delete(key)
            logger.debug(f"Deleted pending execution: {key}")
        except Exception as e:
            logger.error(f"Failed to delete pending execution: {e}")
            raise

    async def set_pending_cancelled(self, execution_id: str) -> bool:
        """
        Mark a pending execution as cancelled.

        Called by API when user cancels before worker picks up.
        Worker checks this flag before starting execution.

        Args:
            execution_id: Execution ID

        Returns:
            True if execution was found and marked cancelled, False if not found
        """
        redis_client = await self._get_redis()
        key = f"{PENDING_KEY_PREFIX}{execution_id}{PENDING_KEY_SUFFIX}"

        try:
            data = await redis_client.get(key)
            if data is None:
                return False

            pending = json.loads(data)
            pending["cancelled"] = True

            # Preserve remaining TTL
            ttl = await redis_client.ttl(key)
            if ttl > 0:
                await redis_client.setex(key, ttl, json.dumps(pending))
            else:
                await redis_client.setex(
                    key, PENDING_EXECUTION_TTL_SECONDS, json.dumps(pending)
                )

            logger.info(f"Marked pending execution as cancelled: {log_safe(execution_id)}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel pending execution: {e}")
            raise

    async def is_pending_cancelled(self, execution_id: str) -> bool:
        """
        Check if a pending execution is cancelled.

        Called by Worker before starting execution.

        Args:
            execution_id: Execution ID

        Returns:
            True if cancelled, False otherwise
        """
        pending = await self.get_pending_execution(execution_id)
        if pending is None:
            return False
        return pending.get("cancelled", False)

    async def update_pending_execution(
        self,
        execution_id: str,
        updates: dict[str, Any],
    ) -> bool:
        """
        Update fields in a pending execution record.

        Called by Worker to add additional context (workflow_name, etc.)
        that needs to be available when async results are processed.

        Args:
            execution_id: Execution ID
            updates: Dict of fields to update

        Returns:
            True if execution was found and updated, False if not found
        """
        redis_client = await self._get_redis()
        key = f"{PENDING_KEY_PREFIX}{execution_id}{PENDING_KEY_SUFFIX}"

        try:
            data = await redis_client.get(key)
            if data is None:
                return False

            pending = json.loads(data)
            pending.update(updates)

            # Preserve remaining TTL
            ttl = await redis_client.ttl(key)
            if ttl > 0:
                await redis_client.setex(key, ttl, json.dumps(pending))
            else:
                await redis_client.setex(
                    key, PENDING_EXECUTION_TTL_SECONDS, json.dumps(pending)
                )

            logger.debug(f"Updated pending execution: {execution_id} with {list(updates.keys())}")
            return True
        except Exception as e:
            logger.error(f"Failed to update pending execution: {e}")
            raise

    # =========================================================================
    # Sync Execution Results (BLPOP/RPUSH pattern)
    # =========================================================================

    async def push_result(
        self,
        execution_id: str,
        status: str,
        result: Any = None,
        error: str | None = None,
        error_type: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """
        Push execution result to Redis for sync callers.

        Called by Worker after workflow execution completes.

        Args:
            execution_id: Execution ID
            status: Execution status (Success, Failed, etc.)
            result: Workflow result data
            error: Error message if failed
            error_type: Error type if failed
            duration_ms: Execution duration in milliseconds
        """
        redis_client = await self._get_redis()
        key = f"{RESULT_KEY_PREFIX}{execution_id}"

        payload = {
            "status": status,
            "result": result,
            "error": error,
            "error_type": error_type,
            "duration_ms": duration_ms,
        }

        try:
            # Push result to list
            # Cast needed: redis-py returns Union[Awaitable[int], int] but we're async
            # Use default=str to handle datetime, UUID, Decimal, etc.
            await cast(Awaitable[int], redis_client.rpush(key, json.dumps(payload, default=str)))
            # Set TTL for auto-cleanup
            await cast(Awaitable[bool], redis_client.expire(key, RESULT_TTL_SECONDS))
            logger.debug(f"Pushed result to Redis: {key}")
        except Exception as e:
            logger.error(f"Failed to push result to Redis: {e}")
            raise

    async def wait_for_result(
        self,
        execution_id: str,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> dict[str, Any] | None:
        """
        Wait for execution result from Redis.

        Called by API for sync execution requests.

        Args:
            execution_id: Execution ID
            timeout_seconds: Max time to wait (default: 300s)

        Returns:
            Result dict or None if timeout
        """
        redis_client = await self._get_redis()
        key = f"{RESULT_KEY_PREFIX}{execution_id}"

        try:
            # BLPOP blocks until value available or timeout
            # Cast needed: redis-py returns Union[Awaitable[list], list] but we're async
            result = await cast(
                Awaitable[list[str] | None],
                redis_client.blpop([key], timeout=timeout_seconds)
            )

            if result is None:
                logger.warning(f"Timeout waiting for result: {execution_id}")
                return None

            # result is tuple (key, value)
            _, value = result
            return json.loads(value)

        except Exception as e:
            logger.error(f"Error waiting for result: {e}")
            raise

    # =========================================================================
    # Agent Run Cancellation (mirrors execution cancel flags)
    # =========================================================================

    async def set_agent_run_cancel_flag(self, run_id: str) -> None:
        """
        Set the cancellation flag for an agent run.

        The executor checks this flag between iterations and tool calls.
        The consumer's cancel watcher also checks it to force-cancel stuck runs.

        Args:
            run_id: Agent run ID to cancel
        """
        redis_client = await self._get_redis()
        key = f"bifrost:agent_run:{run_id}:cancel"
        try:
            await redis_client.setex(key, 3600, "1")
            logger.debug(f"Set agent run cancel flag: {log_safe(key)}")
        except Exception as e:
            logger.error(f"Failed to set agent run cancel flag: {e}")
            raise

    async def check_agent_run_cancel_flag(self, run_id: str) -> bool:
        """
        Check if an agent run has been flagged for cancellation.

        Args:
            run_id: Agent run ID to check

        Returns:
            True if cancelled, False otherwise
        """
        redis_client = await self._get_redis()
        key = f"bifrost:agent_run:{run_id}:cancel"
        try:
            result = await redis_client.get(key)
            return result is not None
        except Exception as e:
            logger.warning(f"Failed to check agent run cancel flag: {e}")
            return False

    # =========================================================================
    # Execution Cancellation
    # =========================================================================

    async def set_cancel_flag(self, execution_id: str) -> None:
        """
        Set the cancellation flag for an execution.

        The execution pool checks this flag periodically and will terminate
        the worker process when it's set.

        Args:
            execution_id: Execution ID to cancel
        """
        redis_client = await self._get_redis()
        key = f"bifrost:exec:{execution_id}:cancel"
        try:
            # Set flag with 1 hour TTL (should be cleaned up much sooner)
            await redis_client.setex(key, 3600, "1")
            logger.debug(f"Set cancel flag: {log_safe(key)}")
        except Exception as e:
            logger.error(f"Failed to set cancel flag: {e}")
            raise

    async def publish_cancel_event(self, execution_id: str) -> None:
        """
        Publish a cancellation event to the process pool via pub/sub.

        This notifies the ProcessPoolManager to immediately kill the worker
        process handling this execution, rather than waiting for the next
        cancel flag check.

        Args:
            execution_id: Execution ID to cancel
        """
        redis_client = await self._get_redis()
        channel = "bifrost:cancel"
        message = json.dumps({"execution_id": execution_id})
        try:
            await redis_client.publish(channel, message)
            logger.debug(f"Published cancel event for execution: {log_safe(execution_id)}")
        except Exception as e:
            logger.error(f"Failed to publish cancel event: {e}")
            # Don't raise - the cancel flag is set as a fallback

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None

    # =========================================================================
    # Endpoint Workflow Cache (for fast webhook execution)
    # =========================================================================

    async def get_endpoint_workflow_cache(self, workflow_id: str) -> dict[str, Any] | None:
        """
        Get cached endpoint workflow metadata.

        Used by endpoints router to skip module loading for metadata validation.
        Returns: {workflow_id, file_path, execution_mode, timeout_seconds, allowed_methods}

        Args:
            workflow_id: Workflow UUID string

        Returns:
            Cached metadata dict or None if not cached
        """
        redis_client = await self._get_redis()
        key = f"{ENDPOINT_WORKFLOW_CACHE_PREFIX}{workflow_id}"

        try:
            data = await redis_client.get(key)
            if data is None:
                return None
            return json.loads(data)
        except Exception as e:
            logger.warning(f"Failed to get endpoint workflow cache for {log_safe(workflow_id)}: {log_safe(e)}")
            return None

    async def set_endpoint_workflow_cache(
        self,
        workflow_id: str,
        file_path: str,
        execution_mode: str,
        timeout_seconds: int,
        allowed_methods: list[str],
    ) -> None:
        """
        Cache endpoint workflow metadata.

        Called after loading workflow metadata to speed up future requests.

        Args:
            workflow_id: Workflow UUID string (cache key)
            file_path: Relative file path (e.g., "workflows/my_workflow.py")
            execution_mode: "sync" or "async"
            timeout_seconds: Execution timeout
            allowed_methods: List of allowed HTTP methods ["GET", "POST", etc.]
        """
        redis_client = await self._get_redis()
        key = f"{ENDPOINT_WORKFLOW_CACHE_PREFIX}{workflow_id}"

        data = {
            "workflow_id": workflow_id,
            "file_path": file_path,
            "execution_mode": execution_mode,
            "timeout_seconds": timeout_seconds,
            "allowed_methods": allowed_methods,
        }

        try:
            await redis_client.setex(
                key,
                ENDPOINT_WORKFLOW_CACHE_TTL_SECONDS,
                json.dumps(data),
            )
            logger.debug(f"Cached endpoint workflow: {log_safe(workflow_id)}")
        except Exception as e:
            logger.warning(f"Failed to cache endpoint workflow {log_safe(workflow_id)}: {log_safe(e)}")
            # Don't raise - cache failure shouldn't fail the request

    async def invalidate_endpoint_workflow_cache(self, workflow_id: str) -> None:
        """
        Invalidate cached endpoint workflow metadata.

        Called when workflow file is modified or deleted.

        Args:
            workflow_id: Workflow UUID string to invalidate
        """
        redis_client = await self._get_redis()
        key = f"{ENDPOINT_WORKFLOW_CACHE_PREFIX}{workflow_id}"

        try:
            await redis_client.delete(key)
            logger.debug(f"Invalidated endpoint workflow cache: {workflow_id}")
        except Exception as e:
            logger.warning(f"Failed to invalidate endpoint workflow cache {workflow_id}: {e}")

    async def invalidate_all_endpoint_workflow_caches(self) -> int:
        """
        Invalidate all endpoint workflow caches.

        Used when bulk operations affect multiple workflows.

        Returns:
            Number of cache entries invalidated
        """
        redis_client = await self._get_redis()
        pattern = f"{ENDPOINT_WORKFLOW_CACHE_PREFIX}*"
        deleted = 0

        try:
            cursor = 0
            while True:
                cursor, keys = await redis_client.scan(cursor, match=pattern, count=100)
                if keys:
                    await redis_client.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break

            if deleted > 0:
                logger.info(f"Invalidated {deleted} endpoint workflow caches")
            return deleted
        except Exception as e:
            logger.warning(f"Failed to invalidate all endpoint workflow caches: {e}")
            return 0

    # =========================================================================
    # Workflow Metadata Cache (for fast execution - cached by workflow_id)
    # =========================================================================

    async def get_workflow_metadata_cache(self, workflow_id: str) -> dict[str, Any] | None:
        """
        Get cached workflow metadata by ID.

        Used by execution service to skip DB lookup for workflow metadata.
        Returns: {id, name, file_path, timeout_seconds, time_saved, value, execution_mode}

        Args:
            workflow_id: Workflow UUID

        Returns:
            Cached metadata dict or None if not cached
        """
        redis_client = await self._get_redis()
        key = f"{WORKFLOW_METADATA_CACHE_PREFIX}{workflow_id}"

        try:
            data = await redis_client.get(key)
            if data is None:
                return None
            return json.loads(data)
        except Exception as e:
            logger.warning(f"Failed to get workflow metadata cache for {workflow_id}: {e}")
            return None

    async def set_workflow_metadata_cache(
        self,
        workflow_id: str,
        name: str,
        file_path: str,
        timeout_seconds: int,
        time_saved: int,
        value: float,
        execution_mode: str,
    ) -> None:
        """
        Cache workflow metadata.

        Called after loading workflow from DB to speed up future lookups.

        Args:
            workflow_id: Workflow UUID
            name: Workflow name
            file_path: Relative file path
            timeout_seconds: Execution timeout
            time_saved: ROI time saved value
            value: ROI monetary value
            execution_mode: "sync" or "async"
        """
        redis_client = await self._get_redis()
        key = f"{WORKFLOW_METADATA_CACHE_PREFIX}{workflow_id}"

        normalized_value = float(value) if isinstance(value, Decimal) else value

        data = {
            "id": workflow_id,
            "name": name,
            "file_path": file_path,
            "timeout_seconds": timeout_seconds,
            "time_saved": time_saved,
            "value": normalized_value,
            "execution_mode": execution_mode,
        }

        try:
            await redis_client.setex(
                key,
                WORKFLOW_METADATA_CACHE_TTL_SECONDS,
                json.dumps(data),
            )
            logger.debug(f"Cached workflow metadata: {workflow_id}")
        except Exception as e:
            logger.warning(f"Failed to cache workflow metadata {workflow_id}: {e}")

    async def invalidate_workflow_metadata_cache(self, workflow_id: str) -> None:
        """
        Invalidate cached workflow metadata.

        Called when workflow is modified or deleted.

        Args:
            workflow_id: Workflow UUID to invalidate
        """
        redis_client = await self._get_redis()
        key = f"{WORKFLOW_METADATA_CACHE_PREFIX}{workflow_id}"

        try:
            await redis_client.delete(key)
            logger.debug(f"Invalidated workflow metadata cache: {workflow_id}")
        except Exception as e:
            logger.warning(f"Failed to invalidate workflow metadata cache {workflow_id}: {e}")

    # =========================================================================
    # General Purpose Methods (for session management, etc.)
    # =========================================================================

    async def get(self, key: str) -> str | None:
        """Get a value by key."""
        redis_client = await self._get_redis()
        return await redis_client.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        """Set a value with TTL."""
        redis_client = await self._get_redis()
        await redis_client.setex(key, ttl, value)

    async def delete(self, key: str) -> int:
        """Delete a key. Returns number of keys deleted."""
        redis_client = await self._get_redis()
        return await redis_client.delete(key)

    async def scan(
        self, cursor: int, match: str | None = None, count: int = 10
    ) -> tuple[int, list[str]]:
        """Scan keys matching pattern."""
        redis_client = await self._get_redis()
        return await redis_client.scan(cursor, match=match, count=count)


# Singleton instance
_redis_client: RedisClient | None = None


def get_redis_client() -> RedisClient:
    """Get singleton Redis client instance."""
    global _redis_client
    if _redis_client is None:
        _redis_client = RedisClient()
    return _redis_client


async def close_redis_client() -> None:
    """Close Redis client."""
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None
