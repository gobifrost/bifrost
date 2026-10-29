"""
Workflow Execution Consumer

Runs the workflow dispatches an executor claimed from PostgreSQL.

Architecture (PostgreSQL-durable with Redis acceleration):
1. API accepts the run: a PENDING execution row and its dispatch
2. An executor claims the dispatch and the run (PENDING -> RUNNING) in one
   transaction, taking the run's context from the dispatch
3. Consumer routes execution with a compact, parent-owned Redis lease
4. ProcessPoolManager refreshes that lease while its child remains active
5. Consumer records the result from the lease or reconstructs it from PostgreSQL

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
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_context
from src.core.pubsub import publish_execution_update, publish_history_update
from src.core.redis_client import ActiveExecution, PendingExecution, get_redis_client
from src.models.enums import ExecutionStatus
from src.models.orm import Execution, User
from src.repositories.executions import create_execution, update_execution

if TYPE_CHECKING:
    from src.services.execution.process_pool import ProcessPoolManager

logger = logging.getLogger(__name__)

# Sync callers are already awake when derived terminal work begins. A short
# grace lets sibling authoritative result commits finish before shared daily
# and ROI aggregate rows are updated.
_SYNC_DERIVED_WORK_GRACE_SECONDS = 0.025


class WorkflowExecutionConsumer:
    """
    Runs claimed workflow dispatches in the executor's process pool.

    Message format (the dispatch's message; the context is the claimed run):
    {
        "execution_id": "uuid",
        "workflow_id": "uuid" (optional, for workflow execution),
        "code": "base64-encoded-script" (optional, for inline scripts),
        "script_name": "name" (optional, for inline scripts),
        "sync": false (optional, if true pushes result to Redis for API)
    }
    """

    # The executor incarnation this consumer writes results for: a result is
    # written only while that executor still holds the run.
    owner_worker_id: UUID | None = None

    def __init__(self, pool: ProcessPoolManager | None = None):
        self._redis_client = get_redis_client()

        # Default to the process-wide manager so package installation keeps
        # updating the same pool; API hosts may supply their canonical pool.
        if pool is None:
            from src.services.execution.process_pool import get_process_pool

            pool = get_process_pool()
        self._pool = pool
        # Set the result callback on the selected pool.
        self._pool.on_result = self._handle_result
        self._pool_started = False

    async def start_pool(self) -> None:
        """Start the process pool (and its template process)."""
        await self._pool.start()
        self._pool_started = True
        logger.info("Process pool started")

    async def stop_pool(self) -> None:
        if self._pool_started:
            await self._pool.stop()
            self._pool_started = False
            logger.info("Process pool stopped")

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

    async def _load_completion_metadata(
        self,
        execution_id: str,
        session: AsyncSession | None = None,
    ) -> tuple[ActiveExecution | None, bool]:
        """Load compact metadata, rebuilding it from PostgreSQL if Redis lost it.

        Returns the metadata and whether PostgreSQL recovery was required. The
        recovery bit lets callers reconcile event delivery defensively because
        the durable execution row does not retain the triggering event payload.
        """
        try:
            active = await self._redis_client.get_active_execution(execution_id)
        except Exception as exc:  # noqa: BLE001 - PostgreSQL is the fallback
            logger.warning(
                "Could not read active execution lease for %s: %s",
                execution_id,
                exc,
            )
            active = None

        if active is not None:
            return active, False

        if session is None:
            from src.core.database import get_session_factory

            session_factory = get_session_factory()
            async with session_factory() as recovery_session:
                return await self._load_completion_metadata_from_database(
                    execution_id, recovery_session
                )

        return await self._load_completion_metadata_from_database(
            execution_id, session
        )

    async def _load_completion_metadata_from_database(
        self,
        execution_id: str,
        session: AsyncSession,
    ) -> tuple[ActiveExecution | None, bool]:
        """Reconstruct terminal bookkeeping metadata from its durable row."""

        row = (
            await session.execute(
                select(Execution, User.email)
                .outerjoin(User, Execution.executed_by == User.id)
                .where(
                    Execution.id == UUID(execution_id),
                    Execution.status.in_(
                        [ExecutionStatus.RUNNING, ExecutionStatus.CANCELLING]
                    ),
                )
            )
        ).one_or_none()
        if row is None:
            return None, True

        execution, user_email = row
        return (
            ActiveExecution(
                execution_id=str(execution.id),
                workflow_id=(
                    str(execution.workflow_id) if execution.workflow_id else None
                ),
                workflow_name=execution.workflow_name,
                org_id=(
                    str(execution.organization_id)
                    if execution.organization_id
                    else None
                ),
                user_id=(str(execution.executed_by) if execution.executed_by else None),
                user_name=execution.executed_by_name,
                user_email=user_email,
                sync=False,
                event=None,
            ),
            True,
        )

    async def _record_completion_metrics(
        self,
        *,
        workflow_id: str | None,
        org_id: str | None,
        status: str,
        duration_ms: int,
        peak_memory_bytes: int | None = None,
        cpu_total_seconds: float | None = None,
        time_saved: int = 0,
        value: float = 0.0,
        include_workflow_roi: bool = False,
    ) -> None:
        """Persist derived aggregates outside the authoritative result commit.

        Daily and workflow aggregates intentionally share rows across many
        executions. PostgreSQL serializes their atomic increments, so keeping
        them in the authoritative completion transaction makes concurrent sync
        callers wait behind unrelated aggregate bookkeeping. The execution
        record, buffered SDK writes, and captured logs are committed before
        this method is called.

        Aggregate failures have never been allowed to fail an execution. Keep
        that contract here while ensuring the short-lived metrics transaction
        is independently committed or rolled back.
        """
        from src.core.database import get_session_factory
        from src.core.metrics import update_daily_metrics, update_workflow_roi_daily

        try:
            session_factory = get_session_factory()
            async with session_factory() as session:
                await update_daily_metrics(
                    org_id=org_id,
                    status=status,
                    duration_ms=duration_ms,
                    peak_memory_bytes=peak_memory_bytes,
                    cpu_total_seconds=cpu_total_seconds,
                    time_saved=time_saved,
                    value=value,
                    workflow_id=workflow_id,
                    db=session,
                )

                if include_workflow_roi and workflow_id:
                    await update_workflow_roi_daily(
                        workflow_id=workflow_id,
                        org_id=org_id,
                        status=status,
                        time_saved=time_saved,
                        value=value,
                        db=session,
                    )

                await session.commit()
        except Exception as e:
            logger.warning(
                "Failed to update derived metrics for %s: %s",
                workflow_id or "inline execution",
                e,
                exc_info=True,
            )

    @staticmethod
    def _drop_fenced_result(execution_id: str) -> None:
        logger.warning(
            "Dropped the result of a run this executor no longer holds "
            "(it was declared lost and the run already ended)",
            extra={"execution_id": execution_id},
        )

    @staticmethod
    async def _read_persisted_completion_timing(
        session: AsyncSession,
        execution_id: str,
    ) -> tuple[datetime | None, datetime | None]:
        """Read timing written by the authoritative terminal-status update."""
        started_at, completed_at = (
            await session.execute(
                select(Execution.started_at, Execution.completed_at).where(
                    Execution.id == UUID(execution_id)
                )
            )
        ).one()
        return started_at, completed_at

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

        completion_started = time.perf_counter()
        workflow_result = result.get("result")
        duration_ms = result.get("duration_ms", 0)

        metadata, recovered_from_database = await self._load_completion_metadata(
            execution_id
        )
        if metadata is None:
            logger.error(
                "No active lease or durable execution row found for result: %s",
                execution_id,
            )
            return
        metadata_read_ms = (time.perf_counter() - completion_started) * 1000

        workflow_id = metadata.get("workflow_id")
        workflow_name = metadata.get("workflow_name", "unknown")
        org_id = metadata.get("org_id")
        user_id = metadata.get("user_id")
        user_name = metadata.get("user_name")
        is_sync = result["sync"]

        status_str = result.get("status", "Success")
        status = (
            ExecutionStatus(status_str)
            if status_str in [s.value for s in ExecutionStatus]
            else ExecutionStatus.SUCCESS
        )

        roi_data = result.get("roi") or {}
        roi_time_saved = roi_data.get("time_saved", 0)
        roi_value = roi_data.get("value", 0.0)
        started_at: datetime | None = None
        completed_at: datetime | None = None
        log_receipt = None

        # DB operations and log flush — single short-lived session
        # (log flush reads Redis internally but DB writes share the session)
        session_factory = get_session_factory()
        async with session_factory() as session:
            written = await update_execution(
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
                owner_worker_id=self.owner_worker_id,
            )
            if not written:
                await session.rollback()
                self._drop_fenced_result(execution_id)
                return
            execution_update_ms = (time.perf_counter() - completion_started) * 1000

            if metadata.get("event") is not None or recovered_from_database:
                try:
                    from src.services.events.processor import update_delivery_from_execution
                    await update_delivery_from_execution(
                        execution_id, status.value, session=session
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to update event delivery for {execution_id[:8]}...: {e}"
                    )

            try:
                from bifrost._logging import stage_logs_to_postgres

                # Redis is authoritative for worker-side logs. Inspect it even
                # when the result payload has no in-memory log list, then only
                # acknowledge its receipt after this transaction commits.
                log_receipt = await stage_logs_to_postgres(execution_id, session)
                if log_receipt.count:
                    logger.debug("Staged %d logs for %s", log_receipt.count, execution_id[:8])
            except Exception as e:
                logger.warning(f"Failed to stage logs for {execution_id[:8]}...: {e}")
            logs_flushed_ms = (time.perf_counter() - completion_started) * 1000

            if status == ExecutionStatus.SUCCESS:
                started_at, completed_at = await self._read_persisted_completion_timing(
                    session, execution_id
                )

            await session.commit()
        durable_ms = (time.perf_counter() - completion_started) * 1000

        # Wake sync callers as soon as their result is durably committed.
        # Everything below is terminal-event fan-out or
        # cleanup and must not add latency to the request/response path.
        if is_sync:
            await self._redis_client.push_result(
                execution_id=execution_id,
                status=status.value,
                result=workflow_result,
                error=result.get("error"),
                error_type=result.get("error_type"),
                duration_ms=duration_ms,
            )
        result_ready_ms = (time.perf_counter() - completion_started) * 1000

        if is_sync:
            await asyncio.sleep(_SYNC_DERIVED_WORK_GRACE_SECONDS)

        if log_receipt is not None:
            from bifrost._logging import acknowledge_staged_logs, recover_terminal_execution_logs

            await acknowledge_staged_logs(log_receipt)
            try:
                await recover_terminal_execution_logs([execution_id], limit=1)
            except Exception as e:
                logger.warning(f"Failed terminal log recovery for {execution_id[:8]}...: {e}")

        # This is deliberately after the authoritative execution commit and
        # sync wakeup, before unrelated fan-out. Built-in event emission is
        # best effort, matching workflow.failed: a crash in this gap can omit
        # the event while preserving the durable execution result.
        if status == ExecutionStatus.SUCCESS and completed_at is not None:
            from src.services.events.builtins import emit_workflow_completed_event

            await emit_workflow_completed_event(
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                execution_id=execution_id,
                organization_id=org_id,
                user_id=user_id,
                user_email=metadata.get("user_email"),
                user_name=user_name,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                trigger_event=metadata.get("event"),
            )

        metrics_data = result.get("metrics") or {}
        await self._record_completion_metrics(
            workflow_id=workflow_id,
            org_id=org_id,
            status=status.value,
            duration_ms=duration_ms,
            peak_memory_bytes=metrics_data.get("peak_memory_bytes"),
            cpu_total_seconds=metrics_data.get("cpu_total_seconds"),
            time_saved=roi_time_saved,
            value=roi_value,
            include_workflow_roi=True,
        )
        metrics_done_ms = (time.perf_counter() - completion_started) * 1000

        # Pub/sub — no DB connection held.  The result is already persisted and
        # execution clients fetch it from the result endpoint, so publishing it
        # again needlessly serializes and transports large payloads through
        # Redis and WebSocket connections.
        await publish_execution_update(
            execution_id,
            status.value,
            {"duration_ms": duration_ms},
        )

        await publish_history_update(
            execution_id=execution_id,
            status=status.value,
            executed_by=user_id,
            executed_by_name=user_name,
            workflow_name=workflow_name,
            org_id=org_id,
            completed_at=completed_at or datetime.now(timezone.utc),
            duration_ms=duration_ms,
        )

        # Redis cleanup — no DB connection held
        try:
            from src.core.cache import cleanup_execution_cache
            await cleanup_execution_cache(execution_id)
        except Exception as e:
            logger.warning(f"Failed to cleanup cache for {execution_id[:8]}...: {e}")

        logger.debug(
            "Completion timing %s: metadata=%.1fms execution_update=%.1fms "
            "logs=%.1fms durable=%.1fms result_ready=%.1fms "
            "metrics=%.1fms total=%.1fms",
            execution_id[:8],
            metadata_read_ms,
            execution_update_ms,
            logs_flushed_ms,
            durable_ms,
            result_ready_ms,
            metrics_done_ms,
            (time.perf_counter() - completion_started) * 1000,
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

        error = result.get("error", "Unknown error")
        error_type = result.get("error_type", "ExecutionError")
        duration_ms = result.get("duration_ms", 0)

        metadata, recovered_from_database = await self._load_completion_metadata(
            execution_id
        )
        if metadata is None:
            logger.error(
                "No active lease or durable execution row found for failed result: %s",
                execution_id,
            )
            return

        workflow_id = metadata.get("workflow_id")
        workflow_name = metadata.get("workflow_name", "unknown")
        org_id = metadata.get("org_id")
        user_id = metadata.get("user_id")
        user_email = metadata.get("user_email")
        user_name = metadata.get("user_name")
        is_sync = result["sync"]

        if error_type == "TimeoutError":
            status = ExecutionStatus.TIMEOUT
        elif error_type == "CancelledError":
            status = ExecutionStatus.CANCELLED
        else:
            status = ExecutionStatus.FAILED
        log_receipt = None

        # DB operations and log flush — single short-lived session
        session_factory = get_session_factory()
        async with session_factory() as session:
            # Forward resource metrics when the pool reports them (timeout,
            # crash, and engine-error results can still carry memory/CPU
            # samples). Failed executions are the highest-memory samples, so
            # dropping them here would bias any future lane-sizing data low.
            failure_metrics = result.get("metrics")
            written = await update_execution(
                execution_id=execution_id,
                status=status,
                error_message=error,
                error_type=error_type,
                duration_ms=duration_ms,
                metrics=failure_metrics,
                session=session,
                owner_worker_id=self.owner_worker_id,
            )
            if not written:
                await session.rollback()
                self._drop_fenced_result(execution_id)
                return

            if metadata.get("event") is not None or recovered_from_database:
                try:
                    from src.services.events.processor import update_delivery_from_execution
                    await update_delivery_from_execution(
                        execution_id,
                        status.value,
                        error_message=error,
                        session=session,
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to update event delivery for {execution_id[:8]}...: {e}"
                    )

            try:
                from bifrost._logging import stage_logs_to_postgres

                log_receipt = await stage_logs_to_postgres(execution_id, session)
                if log_receipt.count:
                    logger.debug(
                        "Staged %d logs for failed %s", log_receipt.count, execution_id[:8]
                    )
            except Exception as e:
                logger.warning(f"Failed to stage logs for {execution_id[:8]}...: {e}")

            await session.commit()

        # A sync failure is just as latency-sensitive as a success. Wake the
        # caller once the authoritative failure is durable; aggregates and
        # terminal fan-out are derived follow-up work.
        if is_sync:
            await self._redis_client.push_result(
                execution_id=execution_id,
                status=status.value,
                error=error,
                error_type=error_type,
                duration_ms=duration_ms,
            )

            await asyncio.sleep(_SYNC_DERIVED_WORK_GRACE_SECONDS)

        if log_receipt is not None:
            from bifrost._logging import acknowledge_staged_logs, recover_terminal_execution_logs

            await acknowledge_staged_logs(log_receipt)
            try:
                await recover_terminal_execution_logs([execution_id], limit=1)
            except Exception as e:
                logger.warning(f"Failed terminal log recovery for {execution_id[:8]}...: {e}")

        failure_metrics = result.get("metrics") or {}
        await self._record_completion_metrics(
            workflow_id=workflow_id,
            org_id=org_id,
            status=status.value,
            duration_ms=duration_ms,
            peak_memory_bytes=failure_metrics.get("peak_memory_bytes"),
            cpu_total_seconds=failure_metrics.get("cpu_total_seconds"),
        )

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

        from src.services.events.builtins import emit_workflow_failure_events

        await emit_workflow_failure_events(
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            execution_id=execution_id,
            organization_id=org_id,
            user_id=user_id,
            user_email=user_email,
            user_name=user_name,
            error_type=error_type,
            error_message=error,
            status=status.value,
            trigger_event=metadata.get("event"),
        )

    async def process_message(
        self,
        message_data: dict[str, Any],
        claimed: PendingExecution | None,
    ) -> None:
        """Run a claimed workflow dispatch.

        ``claimed`` is the context of the run the executor claimed (PENDING ->
        RUNNING) in its claim transaction, or None when the run had already
        moved on (cancelled): the dispatch is then discarded.
        """
        from src.services.execution.queue_tracker import publish_queue_positions

        dispatch_started = time.perf_counter()
        execution_id = message_data.get("execution_id", "")
        workflow_id = message_data.get("workflow_id")
        code_base64 = message_data.get("code")
        script_name = message_data.get("script_name")
        is_sync = message_data.get("sync", False)
        dispatch_metadata = message_data.get("dispatch_metadata")
        file_path: str | None = None  # Will be set from workflow metadata lookup
        start_time = datetime.now(timezone.utc)

        # Sync executions never enter the UI queue tracker: their HTTP caller
        # waits on a private Redis result list while pool admission remains
        # fully enforced.
        if not is_sync:
            # PostgreSQL already owns this claim. Queue notifications must not
            # strand it before a child starts when publication is unavailable.
            try:
                await publish_queue_positions()
            except Exception:
                logger.warning(
                    "Could not update execution queue tracking",
                    extra={"execution_id": execution_id},
                    exc_info=True,
                )

        if claimed is None:
            logger.info(
                "Discarding dispatch whose run was not pending",
                extra={"execution_id": execution_id},
            )
            if is_sync:
                from src.services.execution.workflow_dispatch import (
                    notify_discarded_sync_run,
                )

                async with get_db_context() as db:
                    await notify_discarded_sync_run(db, UUID(execution_id))
            return
        pending = claimed
        pending_ready_ms = (time.perf_counter() - dispatch_started) * 1000

        # Extract context from the claimed run.
        parameters = pending["parameters"]
        org_id = pending["org_id"]
        user_id = pending["user_id"]
        user_name = pending["user_name"]
        user_email = pending["user_email"]
        form_id = pending.get("form_id")
        api_key_id = pending.get("api_key_id")  # Workflow ID whose API key triggered this
        startup = pending.get("startup")  # Launch workflow results
        form_inputs = pending.get("form_inputs", {})
        embed = pending.get("embed", {})
        event_data = pending.get("event")  # EventContext dict if event-triggered
        artifact_workspace_id = pending.get("artifact_workspace_id")
        lineage = pending.get("lineage")
        impersonation_provenance = pending.get("impersonation_provenance")

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

            # Get workflow metadata from database if this is a workflow execution
            workflow_name = script_name or "inline_script"
            timeout_seconds = 1800  # Default 30 minutes
            roi_time_saved = 0
            roi_value = 0.0
            workflow_function_name: str | None = None  # Function name for exec_from_db()
            content_hash: str | None = None  # Content hash pinned at dispatch time
            workflow_type = "workflow"
            cache_ttl_seconds = 300
            solution_id: str | None = None  # Install id if solution-managed
            solution_global_repo_access = False  # Whether solution code may import _repo/

            if not is_script and workflow_id:
                from src.services.execution.service import get_workflow_for_execution, WorkflowNotFoundError

                try:
                    if dispatch_metadata is None:
                        # Non-HTTP producers may only carry an ID. Preserve the
                        # hardened active-Solution lookup for those paths.
                        async with get_db_context() as db:
                            workflow_data = await get_workflow_for_execution(
                                workflow_id,
                                db=db,
                            )
                    else:
                        # The HTTP route already performed this same hardened
                        # lookup after authorization and before enqueueing.
                        workflow_data = dispatch_metadata
                    workflow_name = workflow_data["name"]
                    workflow_function_name = workflow_data["function_name"]
                    file_path = workflow_data["path"]  # Used for __file__ injection and Redis/S3 loading
                    workflow_type = workflow_data["type"]
                    cache_ttl_seconds = workflow_data["cache_ttl_seconds"]

                    timeout_seconds = workflow_data["timeout_seconds"]
                    # Initialize ROI from workflow defaults
                    roi_time_saved = workflow_data["time_saved"]
                    roi_value = workflow_data["value"]

                    # Solution scoping: if the workflow is solution-managed, its
                    # code + imports must resolve under _solutions/{id}/ (with
                    # _repo/ fallback only when the install allows it). Look up
                    # the install's global_repo_access here so the worker can set
                    # the per-execution import root. See module_cache_sync.
                    solution_id = workflow_data.get("solution_id")
                    # global_repo_access now rides on workflow_data from the same
                    # DB grab as the metadata (get_workflow_for_execution). The
                    # engine subprocess has no DB; this is the last enrichment.
                    solution_global_repo_access = workflow_data.get(
                        "can_access_global_repo", False
                    )

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
                        check_existing=True,
                        lineage=lineage,
                        impersonation_provenance=impersonation_provenance,
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
                    if is_sync:
                        await self._redis_client.push_result(
                            execution_id=execution_id,
                            status="Failed",
                            error=error_msg,
                            error_type="WorkflowNotFound",
                            duration_ms=duration_ms,
                        )
                    return
            metadata_ready_ms = (time.perf_counter() - dispatch_started) * 1000

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
                check_existing=True,
                lineage=lineage,
                impersonation_provenance=impersonation_provenance,
            )
            execution_created_ms = (time.perf_counter() - dispatch_started) * 1000
            if not is_sync:
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
            running_published_ms = (time.perf_counter() - dispatch_started) * 1000

            # Rehydrate the org from org_id (the enqueue boundary only carried
            # the scalar org_id, not the Organization object built API-side).
            # is_provider MUST come through here — it is the SDK-side C2
            # scope-bypass flag the worker hands to resolve_scope. See
            # OrganizationRepository.get_with_cache.
            org = None
            org_data = None

            if org_id:
                from src.repositories.organizations import OrganizationRepository

                async with get_db_context() as db:
                    org = await OrganizationRepository(db).get_with_cache(org_id)
                if org:
                    org_data = {
                        "id": org.id,
                        "name": org.name,
                        "is_active": org.is_active,
                        "is_provider": org.is_provider,
                    }

            # Mint engine token parent-side (consumer holds SECRET_KEY legitimately).
            # The child receives it through context_data and installs it only in
            # its one-shot process environment — no SECRET_KEY or persistent
            # credential write is needed in the child.
            from src.core.security import mint_engine_token
            engine_token, _ = mint_engine_token(
                execution_id=execution_id,
                timeout_seconds=timeout_seconds,
            )

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
                "form_inputs": form_inputs,
                "embed": embed,
                "roi": {
                    "time_saved": roi_time_saved,
                    "value": roi_value,
                },
                "file_path": file_path,  # Path for __file__ injection and fallback loading
                "content_hash": content_hash,  # Pinned hash at dispatch time
                "event": event_data,  # EventContext dict (None if not event-triggered)
                "solution_id": solution_id,  # Install id if solution-managed (else None)
                "solution_global_repo_access": solution_global_repo_access,
                "artifact_workspace_id": artifact_workspace_id,
                # Pre-minted engine token: child uses process-scoped SDK
                # credentials, with no SECRET_KEY in its environment.
                "engine_token": engine_token,
            }

            # Route to process pool
            # Results are handled asynchronously via _handle_result callback
            await self._pool.route_execution(
                execution_id=execution_id,
                context=context_data,
                active_execution=ActiveExecution(
                    execution_id=execution_id,
                    workflow_id=str(workflow_id) if workflow_id else None,
                    workflow_name=workflow_name,
                    org_id=str(org_id) if org_id else None,
                    user_id=str(user_id) if user_id else None,
                    user_name=user_name,
                    user_email=user_email,
                    sync=is_sync,
                    event=(
                        {
                            "id": event_data.get("id"),
                            "type": event_data.get("type"),
                        }
                        if event_data
                        else None
                    ),
                ),
            )
            logger.debug(
                "Dispatch timing %s: pending=%.1fms metadata=%.1fms "
                "execution_row=%.1fms running_events=%.1fms routed=%.1fms",
                execution_id[:8],
                pending_ready_ms,
                metadata_ready_ms,
                execution_created_ms,
                running_published_ms,
                (time.perf_counter() - dispatch_started) * 1000,
            )
            # Don't wait for result - pool will call back

        except asyncio.CancelledError:
            logger.info(f"Execution task {execution_id} was cancelled")
            raise

        except MemoryError:
            # The fork was refused before the run started; the executor hands
            # the run back (pool_main) instead of failing it.
            raise

        except Exception as e:
            # Unexpected error during setup (before routing to pool)
            duration_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
            completed_at = datetime.now(timezone.utc)
            error_msg = str(e)
            error_type = type(e).__name__

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
