"""
Workflow Execution Consumer

Processes async workflow executions from RabbitMQ queue.

Architecture (Redis-first):
1. API stores pending execution in Redis, publishes to RabbitMQ
2. Consumer reads pending execution from Redis
3. Consumer creates PostgreSQL record when starting
4. Consumer routes execution to ProcessPoolManager
5. ProcessPoolManager executes in worker process, returns result via callback
6. Consumer handles result: updates DB, flushes logs, cleans up Redis

For sync execution requests (sync=True in message):
- Pushes result to Redis after completion
- API waits on Redis BLPOP for the result

Execution Model:
- All executions use ProcessPoolManager (process isolation)
- Worker processes are pooled and reused for efficiency
- Timeouts and crashes are handled by the pool manager
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from src.core.pubsub import publish_execution_update, publish_history_update
from src.core.redis_client import get_redis_client
from src.jobs.rabbitmq import BaseConsumer

logger = logging.getLogger(__name__)

# Queue name
QUEUE_NAME = "workflow-executions"


class WorkflowExecutionConsumer(BaseConsumer):
    """
    Consumer for workflow execution queue.

    Message format (minimal - context is in Redis):
    {
        "execution_id": "uuid",
        "workflow_id": "uuid" (optional, for workflow execution),
        "code": "base64-encoded-script" (optional, for inline scripts),
        "script_name": "name" (optional, for inline scripts),
        "sync": false (optional, if true pushes result to Redis for API)
    }

    Full execution context is read from Redis pending execution.
    """

    def __init__(self):
        from src.config import get_settings
        from src.services.execution.process_pool import get_process_pool

        settings = get_settings()
        super().__init__(
            queue_name=QUEUE_NAME,
            prefetch_count=settings.max_concurrency,
        )
        self._redis_client = get_redis_client()

        # Get the global ProcessPoolManager instance
        # This ensures package_install consumer can also update it
        self._pool = get_process_pool()
        # Set the result callback on the global pool
        self._pool.on_result = self._handle_result
        self._pool_started = False

    async def start(self) -> None:
        """Start the process pool, then begin consuming messages.

        The pool must be fully ready (including the template process) before
        RabbitMQ can deliver work — otherwise route_execution would run while
        the template is still starting, and there would be no valid way to
        create worker processes for incoming messages.
        """
        await self._pool.start()
        self._pool_started = True
        logger.info("Process pool started")

        # Only now begin accepting messages from RabbitMQ.
        await super().start()

    async def stop(self) -> None:
        """Stop the consumer and process pool."""
        # Stop process pool
        if self._pool_started:
            await self._pool.stop()
            self._pool_started = False
            logger.info("Process pool stopped")

        # Call parent stop
        await super().stop()

    async def _handle_result(self, result: dict[str, Any]) -> None:
        """
        Handle result from process pool.

        This callback is invoked by the pool when a worker reports
        a result (success or failure, including timeouts and crashes).

        DB sessions are short-lived — Redis reads and pub/sub happen outside.
        """
        execution_id = result.get("execution_id", "")

        try:
            if result.get("success"):
                await self._process_success(execution_id, result)
            else:
                await self._process_failure(execution_id, result)
        except Exception as e:
            logger.error(f"Failed to process result for {execution_id}: {e}")
            raise

    async def _process_success(
        self,
        execution_id: str,
        result: dict[str, Any],
    ) -> None:
        """
        Process a successful execution result.

        Updates the database, flushes logs, and publishes status updates.
        DB sessions are short-lived — Redis and pub/sub happen outside sessions.
        """
        from src.core.database import get_session_factory
        from src.core.metrics import update_daily_metrics, update_workflow_roi_daily
        from src.models.enums import ExecutionStatus
        from src.repositories.executions import update_execution

        workflow_result = result.get("result")
        duration_ms = result.get("duration_ms", 0)

        # Redis read — no DB connection held
        pending = await self._redis_client.get_pending_execution(execution_id)
        if not pending:
            logger.warning(f"No pending record found for result: {execution_id}")
            return

        workflow_id = pending.get("workflow_id")
        workflow_name = pending.get("workflow_name", "unknown")
        org_id = pending.get("org_id")
        user_id = pending.get("user_id")
        user_name = pending.get("user_name")
        is_sync = pending.get("sync", False)

        status_str = result.get("status", "Success")
        status = (
            ExecutionStatus(status_str)
            if status_str in [s.value for s in ExecutionStatus]
            else ExecutionStatus.SUCCESS
        )

        roi_data = result.get("roi") or {}
        roi_time_saved = roi_data.get("time_saved", 0)
        roi_value = roi_data.get("value", 0.0)

        # DB operations + flush — single short-lived session
        # (flush functions do Redis reads internally but DB writes share the session)
        session_factory = get_session_factory()
        async with session_factory() as session:
            await update_execution(
                execution_id=execution_id,
                status=status,
                result=workflow_result,
                error_message=result.get("error"),
                error_type=result.get("error_type"),
                duration_ms=duration_ms,
                variables=result.get("variables"),
                execution_context=result.get("execution_context"),
                metrics=result.get("metrics"),
                time_saved=roi_time_saved,
                value=roi_value,
                session=session,
            )

            try:
                from src.services.events.processor import update_delivery_from_execution
                await update_delivery_from_execution(execution_id, status.value, session=session)
            except Exception as e:
                logger.warning(f"Failed to update event delivery for {execution_id[:8]}...: {e}")

            # Flush pending changes and logs (Redis read + DB write in same session)
            try:
                from bifrost._sync import flush_pending_changes
                changes_count = await flush_pending_changes(execution_id, session=session)
                if changes_count > 0:
                    logger.info(f"Flushed {changes_count} pending changes for {execution_id[:8]}...")
            except Exception as e:
                logger.warning(f"Failed to flush pending changes for {execution_id[:8]}...: {e}")

            try:
                from bifrost._logging import flush_logs_to_postgres
                logs_count = await flush_logs_to_postgres(execution_id, session=session)
                if logs_count > 0:
                    logger.debug(f"Flushed {logs_count} logs for {execution_id[:8]}...")
            except Exception as e:
                logger.warning(f"Failed to flush logs for {execution_id[:8]}...: {e}")

            metrics_data = result.get("metrics") or {}
            await update_daily_metrics(
                org_id=org_id,
                status=status.value,
                duration_ms=duration_ms,
                peak_memory_bytes=metrics_data.get("peak_memory_bytes"),
                cpu_total_seconds=metrics_data.get("cpu_total_seconds"),
                time_saved=roi_time_saved,
                value=roi_value,
                workflow_id=workflow_id,
                db=session,
            )

            if workflow_id:
                await update_workflow_roi_daily(
                    workflow_id=workflow_id,
                    org_id=org_id,
                    status=status.value,
                    time_saved=roi_time_saved,
                    value=roi_value,
                    db=session,
                )

            await session.commit()

        # Pub/sub — no DB connection held
        await publish_execution_update(
            execution_id,
            status.value,
            {"result": workflow_result, "durationMs": duration_ms},
        )

        completed_at = datetime.now(timezone.utc)
        await publish_history_update(
            execution_id=execution_id,
            status=status.value,
            executed_by=user_id,
            executed_by_name=user_name,
            workflow_name=workflow_name,
            org_id=org_id,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )

        # Redis cleanup — no DB connection held
        try:
            from src.core.cache import cleanup_execution_cache
            await cleanup_execution_cache(execution_id)
        except Exception as e:
            logger.warning(f"Failed to cleanup cache for {execution_id[:8]}...: {e}")

        await self._redis_client.delete_pending_execution(execution_id)

        if is_sync:
            await self._redis_client.push_result(
                execution_id=execution_id,
                status=status.value,
                result=workflow_result,
                error=result.get("error"),
                error_type=result.get("error_type"),
                duration_ms=duration_ms,
            )

        logger.info(
            f"Execution result processed: {execution_id[:8]}... status={status.value}",
            extra={
                "execution_id": execution_id,
                "workflow_id": workflow_id,
                "status": status.value,
                "duration_ms": duration_ms,
                "execution_model": "process",
            },
        )

    async def _process_failure(
        self,
        execution_id: str,
        result: dict[str, Any],
    ) -> None:
        """
        Process a failed execution result.

        Handles various failure types (timeout, crash, execution error).
        DB sessions are short-lived — Redis and pub/sub happen outside sessions.
        """
        from src.core.database import get_session_factory
        from src.core.metrics import update_daily_metrics
        from src.models.enums import ExecutionStatus
        from src.repositories.executions import update_execution

        error = result.get("error", "Unknown error")
        error_type = result.get("error_type", "ExecutionError")
        duration_ms = result.get("duration_ms", 0)

        # Redis read — no DB connection held
        pending = await self._redis_client.get_pending_execution(execution_id)
        if not pending:
            logger.warning(f"No pending record found for failed result: {execution_id}")
            return

        workflow_id = pending.get("workflow_id")
        workflow_name = pending.get("workflow_name", "unknown")
        org_id = pending.get("org_id")
        user_id = pending.get("user_id")
        user_name = pending.get("user_name")
        is_sync = pending.get("sync", False)

        if error_type == "TimeoutError":
            status = ExecutionStatus.TIMEOUT
        elif error_type == "CancelledError":
            status = ExecutionStatus.CANCELLED
        else:
            status = ExecutionStatus.FAILED

        # DB operations + flush — single short-lived session
        session_factory = get_session_factory()
        async with session_factory() as session:
            await update_execution(
                execution_id=execution_id,
                status=status,
                error_message=error,
                error_type=error_type,
                duration_ms=duration_ms,
                session=session,
            )

            try:
                from src.services.events.processor import update_delivery_from_execution
                await update_delivery_from_execution(
                    execution_id, status.value, error_message=error, session=session
                )
            except Exception as e:
                logger.warning(f"Failed to update event delivery for {execution_id[:8]}...: {e}")

            try:
                from bifrost._sync import flush_pending_changes
                changes_count = await flush_pending_changes(execution_id, session=session)
                if changes_count > 0:
                    logger.info(f"Flushed {changes_count} pending changes for failed {execution_id[:8]}...")
            except Exception as e:
                logger.warning(f"Failed to flush pending changes for {execution_id[:8]}...: {e}")

            try:
                from bifrost._logging import flush_logs_to_postgres
                logs_count = await flush_logs_to_postgres(execution_id, session=session)
                if logs_count > 0:
                    logger.debug(f"Flushed {logs_count} logs for failed {execution_id[:8]}...")
            except Exception as e:
                logger.warning(f"Failed to flush logs for {execution_id[:8]}...: {e}")

            await update_daily_metrics(
                org_id=org_id,
                status=status.value,
                duration_ms=duration_ms,
                workflow_id=workflow_id,
                db=session,
            )

            await session.commit()

        # Pub/sub — no DB connection held
        await publish_execution_update(
            execution_id,
            status.value,
            {"error": error, "errorType": error_type},
        )

        completed_at = datetime.now(timezone.utc)
        await publish_history_update(
            execution_id=execution_id,
            status=status.value,
            executed_by=user_id,
            executed_by_name=user_name,
            workflow_name=workflow_name,
            org_id=org_id,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )

        # Redis cleanup — no DB connection held
        try:
            from src.core.cache import cleanup_execution_cache
            await cleanup_execution_cache(execution_id)
        except Exception as e:
            logger.warning(f"Failed to cleanup cache for {execution_id[:8]}...: {e}")

        await self._redis_client.delete_pending_execution(execution_id)

        if is_sync:
            await self._redis_client.push_result(
                execution_id=execution_id,
                status=status.value,
                error=error,
                error_type=error_type,
                duration_ms=duration_ms,
            )

        logger.warning(
            f"Execution failed: {execution_id[:8]}... status={status.value} error={error_type}",
            extra={
                "execution_id": execution_id,
                "workflow_id": workflow_id,
                "status": status.value,
                "error_type": error_type,
                "duration_ms": duration_ms,
                "execution_model": "process",
            },
        )

    async def process_message(self, message_data: dict[str, Any]) -> None:
        """Process a workflow execution message."""
        from src.core.database import get_db_context
        from src.services.execution.queue_tracker import remove_from_queue

        execution_id = message_data.get("execution_id", "")
        workflow_id = message_data.get("workflow_id")
        code_base64 = message_data.get("code")
        script_name = message_data.get("script_name")
        is_sync = message_data.get("sync", False)
        file_path: str | None = None  # Will be set from workflow metadata lookup
        start_time = datetime.now(timezone.utc)

        # Remove from queue tracking (execution is now being processed)
        await remove_from_queue(execution_id)

        # Read execution context from Redis
        pending = await self._redis_client.get_pending_execution(execution_id)

        if pending is None:
            logger.error(f"No pending execution found in Redis: {execution_id}")
            if is_sync:
                await self._redis_client.push_result(
                    execution_id=execution_id,
                    status="Failed",
                    error="Pending execution not found in Redis",
                    error_type="PendingNotFound",
                    duration_ms=0,
                )
            return

        # Extract context from Redis pending record
        parameters = pending["parameters"]
        org_id = pending["org_id"]
        user_id = pending["user_id"]
        user_name = pending["user_name"]
        user_email = pending["user_email"]
        form_id = pending.get("form_id")
        api_key_id = pending.get("api_key_id")  # Workflow ID whose API key triggered this
        startup = pending.get("startup")  # Launch workflow results
        event_data = pending.get("event")  # EventContext dict if event-triggered

        # Determine if this is a code or workflow execution
        is_script = bool(code_base64)

        try:
            logger.info(
                f"Processing {'code' if is_script else 'workflow'} execution",
                extra={
                    "execution_id": execution_id,
                    "workflow_id": workflow_id,
                    "script_name": script_name,
                    "org_id": org_id,
                    "execution_model": "process",
                },
            )

            from src.models.enums import ExecutionStatus
            from src.repositories.executions import (
                create_execution,
                update_execution,
            )

            # Check if execution was cancelled in Redis before we started
            if pending.get("cancelled", False):
                logger.info(f"Execution {execution_id} was cancelled before starting")
                await create_execution(
                    execution_id=execution_id,
                    workflow_name=script_name or "workflow",
                    parameters=parameters,
                    org_id=org_id,
                    user_id=user_id,
                    user_name=user_name,
                    form_id=form_id,
                    api_key_id=api_key_id,
                    status=ExecutionStatus.CANCELLED,
                    execution_model="process",
                    workflow_id=workflow_id,
                )
                await update_execution(
                    execution_id=execution_id,
                    status=ExecutionStatus.CANCELLED,
                    error_message="Execution was cancelled before it could start",
                    duration_ms=0,
                )
                await publish_execution_update(execution_id, "Cancelled")
                await publish_history_update(
                    execution_id=execution_id,
                    status="Cancelled",
                    executed_by=user_id,
                    executed_by_name=user_name,
                    workflow_name=script_name or "workflow",
                    org_id=org_id,
                )
                await self._redis_client.delete_pending_execution(execution_id)
                if is_sync:
                    await self._redis_client.push_result(
                        execution_id=execution_id,
                        status="Cancelled",
                        error="Execution was cancelled before it could start",
                        duration_ms=0,
                    )
                return

            # Get workflow metadata from database if this is a workflow execution
            workflow_name = script_name or "inline_script"
            timeout_seconds = 1800  # Default 30 minutes
            roi_time_saved = 0
            roi_value = 0.0
            workflow_function_name: str | None = None  # Function name for exec_from_db()
            content_hash: str | None = None  # Content hash pinned at dispatch time
            workflow_type = "workflow"
            cache_ttl_seconds = 300

            if not is_script and workflow_id:
                from src.services.execution.service import get_workflow_for_execution, WorkflowNotFoundError

                try:
                    # Get workflow metadata (no code — worker loads via Redis→S3)
                    # Brief DB session for metadata read
                    async with get_db_context() as db:
                        workflow_data = await get_workflow_for_execution(workflow_id, db=db)
                    workflow_name = workflow_data["name"]
                    workflow_function_name = workflow_data["function_name"]
                    file_path = workflow_data["path"]  # Used for __file__ injection and Redis/S3 loading
                    workflow_type = workflow_data["type"]
                    cache_ttl_seconds = workflow_data["cache_ttl_seconds"]

                    timeout_seconds = workflow_data["timeout_seconds"]
                    # Initialize ROI from workflow defaults
                    roi_time_saved = workflow_data["time_saved"]
                    roi_value = workflow_data["value"]

                    # Scope resolution: org-scoped workflows use workflow's org,
                    # global workflows use caller's org
                    workflow_org_id = workflow_data.get("organization_id")
                    if workflow_org_id:
                        # Org-scoped workflow: always use workflow's org
                        org_id = workflow_org_id
                        logger.info(f"Scope: workflow org {org_id} (org-scoped workflow)")
                    else:
                        # Global workflow: use caller's org (already set from pending["org_id"])
                        logger.info(f"Scope: caller org {org_id or 'GLOBAL'} (global workflow)")
                except WorkflowNotFoundError:
                    logger.error(f"Workflow not found: {workflow_id}")
                    duration_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
                    error_msg = f"Workflow with ID '{workflow_id}' not found"
                    await create_execution(
                        execution_id=execution_id,
                        workflow_name="unknown",
                        parameters=parameters,
                        org_id=org_id,
                        user_id=user_id,
                        user_name=user_name,
                        form_id=form_id,
                        api_key_id=api_key_id,
                        status=ExecutionStatus.FAILED,
                        execution_model="process",
                        workflow_id=workflow_id,
                    )
                    await update_execution(
                        execution_id=execution_id,
                        status=ExecutionStatus.FAILED,
                        result={"error": "WorkflowNotFound", "message": error_msg},
                        duration_ms=duration_ms,
                    )
                    await publish_execution_update(execution_id, "Failed", {"error": error_msg})
                    await publish_history_update(
                        execution_id=execution_id,
                        status="Failed",
                        executed_by=user_id,
                        executed_by_name=user_name,
                        workflow_name="unknown",
                        org_id=org_id,
                        duration_ms=duration_ms,
                    )
                    await self._redis_client.delete_pending_execution(execution_id)
                    if is_sync:
                        await self._redis_client.push_result(
                            execution_id=execution_id,
                            status="Failed",
                            error=error_msg,
                            error_type="WorkflowNotFound",
                            duration_ms=duration_ms,
                        )
                    return

            # Store additional context in pending record for result handler
            # (needed when pool reports results asynchronously)
            await self._redis_client.update_pending_execution(
                execution_id=execution_id,
                updates={
                    "workflow_name": workflow_name,
                    "workflow_id": workflow_id,
                    "org_id": org_id,  # Resolved scope for result handlers
                },
            )

            # Create PostgreSQL record with RUNNING status
            await create_execution(
                execution_id=execution_id,
                workflow_name=workflow_name,
                parameters=parameters,
                org_id=org_id,
                user_id=user_id,
                user_name=user_name,
                form_id=form_id,
                api_key_id=api_key_id,
                status=ExecutionStatus.RUNNING,
                execution_model="process",
                workflow_id=workflow_id,
            )
            await publish_execution_update(execution_id, "Running")
            await publish_history_update(
                execution_id=execution_id,
                status="Running",
                executed_by=user_id,
                executed_by_name=user_name,
                workflow_name=workflow_name,
                org_id=org_id,
                started_at=start_time,
            )

            # Load organization
            org = None
            org_data = None

            if org_id:
                from src.core.config_resolver import ConfigResolver

                resolver = ConfigResolver()
                async with get_db_context() as db:
                    org = await resolver.get_organization(org_id, db=db)
                if org:
                    org_data = {
                        "id": org.id,
                        "name": org.name,
                        "is_active": org.is_active,
                        "is_provider": org.is_provider,
                    }

            # Build context for worker process
            context_data = {
                "execution_id": execution_id,
                "workflow_id": workflow_id,
                "name": workflow_name,
                "function_name": workflow_function_name,  # For exec_from_db()
                "code": code_base64,  # Base64-encoded inline script (different from workflow_code)
                "parameters": parameters,
                "caller": {
                    "user_id": user_id,
                    "email": user_email,
                    "name": user_name,
                },
                "organization": org_data,
                "tags": [workflow_type] if not is_script else [],
                "timeout_seconds": timeout_seconds,
                "cache_ttl_seconds": cache_ttl_seconds,
                "transient": False,
                "is_platform_admin": pending.get("is_platform_admin", False),
                "startup": startup,  # Launch workflow results (available via context.startup)
                "roi": {
                    "time_saved": roi_time_saved,
                    "value": roi_value,
                },
                "file_path": file_path,  # Path for __file__ injection and fallback loading
                "content_hash": content_hash,  # Pinned hash at dispatch time
                "event": event_data,  # EventContext dict (None if not event-triggered)
            }

            # Route to process pool
            # Results are handled asynchronously via _handle_result callback
            await self._pool.route_execution(
                execution_id=execution_id,
                context=context_data,
            )
            # Don't wait for result - pool will call back

        except asyncio.CancelledError:
            logger.info(f"Execution task {execution_id} was cancelled")
            await self._redis_client.delete_pending_execution(execution_id)
            raise

        except MemoryError as e:
            # Admission rejected due to memory pressure — requeue for retry
            logger.warning(
                f"Admission rejected for {execution_id[:8]}: {e}. "
                "Will requeue for retry."
            )
            # Don't mark as failed — the execution hasn't started yet.
            # Clean up pending state so it can be re-routed.
            await self._redis_client.delete_pending_execution(execution_id)
            # Re-raise so the consumer framework NACKs with requeue=True
            raise

        except Exception as e:
            # Unexpected error during setup (before routing to pool)
            duration_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
            completed_at = datetime.now(timezone.utc)
            error_msg = str(e)
            error_type = type(e).__name__

            from src.models.enums import ExecutionStatus
            from src.repositories.executions import update_execution

            await update_execution(
                execution_id=execution_id,
                status=ExecutionStatus.FAILED,
                error_message=error_msg,
                error_type=error_type,
                duration_ms=duration_ms,
            )

            await publish_execution_update(
                execution_id,
                "Failed",
                {"error": error_msg, "errorType": error_type},
            )
            await publish_history_update(
                execution_id=execution_id,
                status="Failed",
                executed_by=user_id,
                executed_by_name=user_name,
                workflow_name=workflow_name,
                org_id=org_id,
                started_at=start_time,
                completed_at=completed_at,
                duration_ms=duration_ms,
            )

            await self._redis_client.delete_pending_execution(execution_id)

            if is_sync:
                await self._redis_client.push_result(
                    execution_id=execution_id,
                    status="Failed",
                    error=error_msg,
                    error_type=error_type,
                    duration_ms=duration_ms,
                )

            logger.error(
                f"Workflow execution error: {execution_id}",
                extra={
                    "execution_id": execution_id,
                    "workflow_id": workflow_id,
                    "error": error_msg,
                    "error_type": error_type,
                    "execution_model": "process",
                },
                exc_info=True,
            )
            raise
