"""
Bifrost Scheduler - Background Scheduler Service

Main entry point for the scheduler service.
Handles APScheduler for cron jobs, cleanup tasks, and OAuth token refresh.

This container is responsible for:
- Competing for the singleton trigger lease used by APScheduler
- Running a small durable platform-job pool on every replica

Scheduler replicas are interchangeable. PostgreSQL lease fencing ensures only
one replica runs scheduled triggers while durable job rows are claimed across
all replicas with ``FOR UPDATE SKIP LOCKED``.

NOTE: File watching and DB sync has been moved to the Discovery container.
"""

import asyncio
import logging
import os
import signal
import socket
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.config import get_settings
from src.core.database import init_db, close_db, get_db_context
from src.core.pubsub import publish_git_op_completed
from src.jobs.schedulers.cron_scheduler import process_schedule_sources
from src.jobs.schedulers.execution_cleanup import cleanup_stuck_executions
from src.jobs.schedulers.platform_jobs import platform_job_worker_loop
from src.scheduler.health import heartbeat_loop, write_heartbeat
from src.scheduler.leadership import SchedulerLeadershipLease
from src.scheduler.registry import (
    SCHEDULED_TASKS_BY_ID,
    ScheduledTaskOutcome,
)
from src.services.scheduler_diagnostics import (
    finish_scheduler_run,
    heartbeat_scheduler_replica,
    publish_task_states,
    remove_scheduler_replica,
    start_scheduler_run,
)


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Suppress noisy third-party loggers
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("apscheduler.executors").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("aiobotocore").setLevel(logging.WARNING)
logging.getLogger("s3transfer").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

LEADERSHIP_RETRY_SECONDS = 5.0
LEADERSHIP_RENEW_SECONDS = 10.0
PLATFORM_JOB_CONCURRENCY = 2


class Scheduler:
    """
    Background scheduler service.

    Manages APScheduler for scheduled tasks:
    - CRON workflow execution
    - Stuck execution cleanup
    - OAuth token refresh

    Every replica also claims durable on-demand platform jobs from PostgreSQL.
    """

    def __init__(
        self,
        *,
        leadership_lease: SchedulerLeadershipLease | None = None,
    ):
        self.settings = get_settings()
        self.running = False
        self._shutdown_event = asyncio.Event()
        self._leadership_lease = leadership_lease or SchedulerLeadershipLease()
        self._scheduler: AsyncIOScheduler | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._diagnostics_task: asyncio.Task[None] | None = None
        self._leadership_task: asyncio.Task[None] | None = None
        self._job_slots = PLATFORM_JOB_CONCURRENCY
        self._platform_job_tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        """Start the scheduler."""
        self.running = True
        logger.info("Starting Bifrost Scheduler...")
        logger.info(f"Environment: {self.settings.environment}")

        # Initialize database connection
        logger.info("Initializing database connection...")
        await init_db()
        logger.info("Database connection established")

        write_heartbeat()
        self._heartbeat_task = asyncio.create_task(heartbeat_loop())
        self._diagnostics_task = asyncio.create_task(
            self._diagnostics_heartbeat_loop(),
            name="scheduler-diagnostics-heartbeat",
        )
        self._platform_job_tasks = [
            asyncio.create_task(
                platform_job_worker_loop(self._shutdown_event),
                name=f"platform-job-worker-{slot + 1}",
            )
            for slot in range(self._job_slots)
        ]
        self._leadership_task = asyncio.create_task(
            self._leadership_loop(),
            name="scheduler-leadership",
        )
        for task in self._platform_job_tasks:
            task.add_done_callback(self._background_task_done)
        self._leadership_task.add_done_callback(self._background_task_done)

        logger.info("Bifrost Scheduler replica started")
        logger.info("Running... (Ctrl+C to stop)")

        # Keep running until shutdown
        await self._shutdown_event.wait()

        for task in (*self._platform_job_tasks, self._leadership_task):
            if (
                self.running
                and task is not None
                and task.done()
                and not task.cancelled()
            ):
                error = task.exception()
                await self.stop()
                if error is not None:
                    raise RuntimeError(
                        f"Scheduler background task {task.get_name()} failed"
                    ) from error
                raise RuntimeError(
                    f"Scheduler background task {task.get_name()} stopped unexpectedly"
                )

    def _background_task_done(self, _task: asyncio.Task[None]) -> None:
        """Terminate the replica if an always-on control loop exits."""
        if self.running and not self._shutdown_event.is_set():
            self._shutdown_event.set()

    async def _diagnostics_heartbeat_loop(self) -> None:
        started_at = datetime.now(timezone.utc)
        while not self._shutdown_event.is_set():
            try:
                await heartbeat_scheduler_replica(
                    replica_id=self._leadership_lease.owner_id,
                    hostname=socket.gethostname(),
                    pid=os.getpid(),
                    job_slots=self._job_slots,
                    started_at=started_at,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to publish scheduler diagnostics heartbeat")
            await self._wait_or_shutdown(10)

    async def _run_scheduled_task(self, task_id: str, callback) -> None:  # type: ignore[no-untyped-def]
        run_id = await start_scheduler_run(task_id, self._leadership_lease.owner_id)
        try:
            result = await callback()
        except asyncio.CancelledError:
            await finish_scheduler_run(
                run_id,
                status="failed",
                error_message="Scheduler leadership ended while the task was running.",
            )
            raise
        except Exception as exc:
            await finish_scheduler_run(
                run_id,
                status="failed",
                error_message=f"{type(exc).__name__}: {exc}",
            )
            raise
        outcome = (
            result
            if isinstance(result, ScheduledTaskOutcome)
            else ScheduledTaskOutcome(summary="Completed")
        )
        await finish_scheduler_run(
            run_id,
            status="enqueued" if outcome.platform_job_id else "succeeded",
            summary=outcome.summary,
            platform_job_id=outcome.platform_job_id,
        )
        await self._publish_task_states()

    async def _publish_task_states(self) -> None:
        scheduler = self._scheduler
        if scheduler is None:
            return
        states = []
        for task_id, definition in SCHEDULED_TASKS_BY_ID.items():
            job = scheduler.get_job(task_id)
            states.append((definition, job.next_run_time if job else None))
        await publish_task_states(states)

    async def _start_scheduler(self) -> None:
        """Start APScheduler with all scheduled jobs."""
        scheduler = AsyncIOScheduler()

        # Common job options for misfire handling
        misfire_options = {
            "misfire_grace_time": 60 * 10,  # 10 minute grace period
            "coalesce": True,  # Combine missed runs into one
        }

        # Schedule processor - every 1 minute
        scheduler.add_job(
            self._run_scheduled_task,
            CronTrigger(minute="*/1"),  # Every 1 minute
            id="schedule_processor",
            name="Process schedule sources",
            replace_existing=True,
            args=["schedule_processor", process_schedule_sources],
            **misfire_options,
        )

        from src.jobs.platform.summary_backfill import reconcile_summary_backfill_jobs

        scheduler.add_job(
            self._run_scheduled_task,
            IntervalTrigger(seconds=60),
            id="summary_backfill_reconciliation",
            name="Reconcile summary backfill parents",
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc),
            args=["summary_backfill_reconciliation", reconcile_summary_backfill_jobs],
            **misfire_options,
        )

        # Deferred execution promoter — every 60s.
        from src.jobs.schedulers.deferred_execution_promoter import (
            promote_due_executions,
        )

        scheduler.add_job(
            self._run_scheduled_task,
            IntervalTrigger(seconds=60),
            id="deferred_execution_promoter",
            name="Promote due scheduled executions",
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc),
            args=["deferred_execution_promoter", promote_due_executions],
            **misfire_options,
        )
        logger.info("Deferred execution promoter scheduled (every 60s)")

        # Legacy entity-logo normalization — bounded batches, immediate at startup.
        from src.jobs.schedulers.logo_thumbnail_backfill import (
            backfill_logo_thumbnails,
        )

        scheduler.add_job(
            backfill_logo_thumbnails,
            IntervalTrigger(minutes=1),
            id="logo_thumbnail_backfill",
            name="Backfill bounded entity-logo thumbnails",
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc),
            **misfire_options,
        )
        logger.info("Logo thumbnail backfill scheduled (every 60s)")

        # Execution cleanup - every 5 minutes (run immediately at startup)
        scheduler.add_job(
            self._run_scheduled_task,
            CronTrigger(minute="*/5"),  # Every 5 minutes
            id="execution_cleanup",
            name="Cleanup stuck executions",
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc),  # Run immediately at startup
            args=["execution_cleanup", cleanup_stuck_executions],
            **misfire_options,
        )

        # OAuth token refresh - every 15 minutes (run immediately at startup)
        try:
            from src.jobs.platform.system_maintenance import (
                enqueue_automatic_oauth_refresh,
            )
            scheduler.add_job(
                self._run_scheduled_task,
                IntervalTrigger(minutes=15),
                id="oauth_token_refresh",
                name="Refresh expiring OAuth tokens",
                replace_existing=True,
                next_run_time=datetime.now(timezone.utc),  # Run immediately at startup
                args=["oauth_token_refresh", enqueue_automatic_oauth_refresh],
                **misfire_options,
            )
            logger.info("OAuth token refresh job scheduled (every 15 min)")
        except ImportError:
            logger.warning("OAuth token refresh job not available")

        # Metrics snapshot refresh - every 60 minutes (run immediately at startup)
        try:
            from src.jobs.schedulers.metrics_refresh import refresh_metrics_snapshot
            scheduler.add_job(
                self._run_scheduled_task,
                IntervalTrigger(minutes=60),
                id="metrics_refresh",
                name="Refresh platform metrics snapshot",
                replace_existing=True,
                next_run_time=datetime.now(timezone.utc),  # Run immediately at startup
                args=["metrics_refresh", refresh_metrics_snapshot],
                **misfire_options,
            )
            logger.info("Metrics snapshot refresh job scheduled (every 60 min)")
        except ImportError:
            logger.warning("Metrics snapshot refresh job not available")

        # Knowledge storage refresh - daily at 2:00 AM UTC (run immediately at startup)
        try:
            from src.jobs.schedulers.knowledge_storage_refresh import (
                refresh_knowledge_storage_daily,
            )
            scheduler.add_job(
                self._run_scheduled_task,
                CronTrigger(hour=2, minute=0),  # Daily at 2:00 AM UTC
                id="knowledge_storage_refresh",
                name="Refresh knowledge storage daily metrics",
                replace_existing=True,
                next_run_time=datetime.now(timezone.utc),  # Run immediately at startup
                args=["knowledge_storage_refresh", refresh_knowledge_storage_daily],
                **misfire_options,
            )
            logger.info("Knowledge storage refresh job scheduled (daily at 2:00 AM)")
        except ImportError:
            logger.warning("Knowledge storage refresh job not available")

        # Workspace file-index reconciliation - daily at 01:00 UTC and startup.
        from src.jobs.platform.system_maintenance import (
            enqueue_automatic_file_index_reconciliation,
        )

        scheduler.add_job(
            self._run_scheduled_task,
            CronTrigger(hour=1, minute=0),
            id="file_index_reconciliation",
            name="Reconcile workspace file index",
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc),
            args=[
                "file_index_reconciliation",
                enqueue_automatic_file_index_reconciliation,
            ],
            **misfire_options,
        )
        logger.info("Workspace file-index reconciliation scheduled (daily at 01:00)")

        # Webhook subscription renewal - every 6 hours
        try:
            from src.jobs.platform.system_maintenance import (
                enqueue_automatic_webhook_renewal,
            )
            scheduler.add_job(
                self._run_scheduled_task,
                IntervalTrigger(hours=6),
                id="webhook_renewal",
                name="Renew expiring webhook subscriptions",
                replace_existing=True,
                next_run_time=datetime.now(timezone.utc),  # Run immediately at startup
                args=["webhook_renewal", enqueue_automatic_webhook_renewal],
                **misfire_options,
            )
            logger.info("Webhook renewal job scheduled (every 6 hours)")
        except ImportError:
            logger.warning("Webhook renewal job not available")

        # Solution update check - every 6 hours (run immediately at startup)
        try:
            from src.jobs.platform.system_maintenance import (
                enqueue_automatic_solution_update_check,
            )
            scheduler.add_job(
                self._run_scheduled_task,
                IntervalTrigger(hours=6),
                id="solution_update_check",
                name="Check git-connected Solution installs for updates",
                replace_existing=True,
                next_run_time=datetime.now(timezone.utc),  # Run immediately at startup
                args=["solution_update_check", enqueue_automatic_solution_update_check],
                **misfire_options,
            )
            logger.info("Solution update check job scheduled (every 6 hours)")
        except ImportError:
            logger.warning("Solution update check job not available")

        # Completed Solution backup artifacts expire independently of execution.
        try:
            from src.jobs.schedulers.solution_export_jobs import (
                cleanup_expired_solution_export_jobs,
            )
            scheduler.add_job(
                self._run_scheduled_task,
                IntervalTrigger(hours=1),
                id="solution_export_job_cleanup",
                name="Cleanup expired Solution backup export artifacts",
                replace_existing=True,
                args=["solution_export_job_cleanup", cleanup_expired_solution_export_jobs],
                **misfire_options,
            )
            logger.info("Solution export artifact cleanup scheduled (hourly)")
        except ImportError:
            logger.warning("Solution export artifact cleanup not available")

        # Event cleanup - daily at 3:00 AM UTC (30-day retention)
        try:
            from src.jobs.schedulers.event_cleanup import cleanup_old_events
            scheduler.add_job(
                self._run_scheduled_task,
                CronTrigger(hour=3, minute=0),  # Daily at 3:00 AM UTC
                id="event_cleanup",
                name="Cleanup old events (30-day retention)",
                replace_existing=True,
                args=["event_cleanup", cleanup_old_events],
                **misfire_options,
            )
            logger.info("Event cleanup job scheduled (daily at 3:00 AM)")
        except ImportError:
            logger.warning("Event cleanup job not available")

        # Stuck event delivery cleanup - every 5 minutes (run immediately at startup)
        try:
            from src.jobs.schedulers.event_cleanup import cleanup_stuck_events
            scheduler.add_job(
                self._run_scheduled_task,
                CronTrigger(minute="*/5"),  # Every 5 minutes
                id="stuck_event_cleanup",
                name="Cleanup stuck event deliveries",
                replace_existing=True,
                next_run_time=datetime.now(timezone.utc),  # Run immediately at startup
                args=["stuck_event_cleanup", cleanup_stuck_events],
                **misfire_options,
            )
            logger.info("Stuck event cleanup job scheduled (every 5 min)")
        except ImportError:
            logger.warning("Stuck event cleanup job not available")

        # Worker metrics sampling - every 60 seconds
        # Reads heartbeats from Redis and persists to DB for the diagnostics chart
        try:
            from src.jobs.schedulers.worker_metrics_sampling import sample_worker_metrics
            scheduler.add_job(
                self._run_scheduled_task,
                IntervalTrigger(seconds=60),
                id="worker_metrics_sampling",
                name="Sample worker metrics from Redis heartbeats",
                replace_existing=True,
                next_run_time=datetime.now(timezone.utc),
                args=["worker_metrics_sampling", sample_worker_metrics],
                **misfire_options,
            )
            logger.info("Worker metrics sampling job scheduled (every 60s)")
        except ImportError:
            logger.warning("Worker metrics sampling job not available")

        # Worker metrics cleanup - daily at 4:00 AM UTC (7-day retention)
        try:
            from src.jobs.schedulers.worker_metrics_cleanup import cleanup_old_worker_metrics
            scheduler.add_job(
                self._run_scheduled_task,
                CronTrigger(hour=4, minute=0),  # Daily at 4:00 AM UTC
                id="worker_metrics_cleanup",
                name="Cleanup old worker metrics (7-day retention)",
                replace_existing=True,
                args=["worker_metrics_cleanup", cleanup_old_worker_metrics],
                **misfire_options,
            )
            logger.info("Worker metrics cleanup job scheduled (daily at 4:00 AM)")
        except ImportError:
            logger.warning("Worker metrics cleanup job not available")

        from src.services.scheduler_diagnostics import cleanup_scheduler_diagnostics

        scheduler.add_job(
            self._run_scheduled_task,
            IntervalTrigger(hours=1),
            id="scheduler_diagnostics_cleanup",
            name="Cleanup scheduler diagnostics",
            replace_existing=True,
            args=["scheduler_diagnostics_cleanup", cleanup_scheduler_diagnostics],
            **misfire_options,
        )

        scheduler.start()
        self._scheduler = scheduler
        await self._publish_task_states()
        logger.info("APScheduler started with scheduled jobs")

    async def _start_leader_services(self) -> None:
        """Start services that must have exactly one active replica."""
        if self._scheduler is not None:
            return
        await self._start_scheduler()

    async def _stop_leader_services(self) -> None:
        """Stop singleton trigger services before surrendering leadership."""
        stop_error: Exception | None = None
        if self._scheduler is not None:
            scheduler = self._scheduler
            self._scheduler = None
            try:
                scheduler.shutdown(wait=False)
                logger.info("APScheduler stopped")
            except Exception as exc:
                stop_error = stop_error or exc
                logger.exception("Failed to stop APScheduler")

        if stop_error is not None:
            raise stop_error

    async def _wait_or_shutdown(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._shutdown_event.wait(), timeout=seconds)
        except TimeoutError:
            # The retry interval elapsed without a shutdown request.
            return

    async def _leadership_loop(self) -> None:
        """Elect one trigger leader while every replica remains a job runner."""
        try:
            while self.running and not self._shutdown_event.is_set():
                if not self._leadership_lease.is_leader:
                    try:
                        acquired = await self._leadership_lease.try_acquire()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Failed to acquire scheduler trigger lease")
                        await self._wait_or_shutdown(LEADERSHIP_RETRY_SECONDS)
                        continue

                    if not acquired:
                        await self._wait_or_shutdown(LEADERSHIP_RETRY_SECONDS)
                        continue

                    logger.info(
                        "Scheduler replica became trigger leader",
                        extra={"owner_id": self._leadership_lease.owner_id},
                    )
                    try:
                        await self._start_leader_services()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Failed to start scheduler leader services")
                        await self._stop_leader_services()
                        try:
                            await self._leadership_lease.release()
                        except Exception:
                            logger.exception("Failed to release scheduler trigger lease")
                        await self._wait_or_shutdown(LEADERSHIP_RETRY_SECONDS)
                        continue

                await self._wait_or_shutdown(LEADERSHIP_RENEW_SECONDS)
                if self._shutdown_event.is_set():
                    break

                try:
                    renewed = await self._leadership_lease.renew()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Failed to renew scheduler trigger lease")
                    renewed = False

                if renewed:
                    continue

                logger.warning("Scheduler replica lost trigger leadership")
                await self._stop_leader_services()
                try:
                    await self._leadership_lease.release()
                except Exception:
                    logger.exception("Failed to clear lost scheduler trigger lease")
        finally:
            await self._stop_leader_services()
            try:
                await self._leadership_lease.release()
            except Exception:
                logger.exception("Failed to release scheduler trigger lease on shutdown")

    @staticmethod
    def _build_clone_url_from_config(config) -> str:
        """Build an authenticated git clone URL from a GitHubConfig object."""
        repo_url = config.repo_url

        # Extract owner/repo from URL
        if repo_url.startswith("https://github.com/"):
            repo = repo_url.replace("https://github.com/", "").rstrip(".git")
        else:
            repo = repo_url

        return f"https://x-access-token:{config.token}@github.com/{repo}.git"

    async def _handle_git_operation(self, data: dict) -> bool:
        """
        Handle a desktop-style git operation request.

        Dispatches to the appropriate GitHubSyncService method based on op_type.
        """
        from src.services.github_config import get_github_config
        from src.services.github_sync import GitHubSyncService

        op_type = data.get("type", "unknown")
        job_id = data.get("jobId", "unknown")
        org_id = data.get("orgId")

        logger.info(f"Starting git operation {op_type} job {job_id} for org {org_id}")

        try:
            async with get_db_context() as db:
                github_config = await get_github_config(db, org_id)

                if not github_config:
                    await publish_git_op_completed(
                        job_id, status="failed", result_type=op_type.replace("git_", ""),
                        error="GitHub not configured",
                    )
                    return False

                if not github_config.token or not github_config.repo_url:
                    await publish_git_op_completed(
                        job_id, status="failed", result_type=op_type.replace("git_", ""),
                        error="GitHub token or repository not configured",
                    )
                    return False

                clone_url = self._build_clone_url_from_config(github_config)
                branch = github_config.branch

                sync_service = GitHubSyncService(
                    db=db,
                    repo_url=clone_url,
                    branch=branch,
                    settings=get_settings(),
                )

                result_type = op_type.replace("git_", "")

                if op_type == "git_fetch":
                    # Fetch does S3 sync down + git fetch + status
                    fetch_result = await sync_service.desktop_fetch(job_id=job_id)
                    if fetch_result.success:
                        status_result = await sync_service.desktop_status()
                        await publish_git_op_completed(
                            job_id, status="success", result_type="fetch",
                            data={
                                **fetch_result.model_dump(),
                                "changed_files": [cf.model_dump() for cf in status_result.changed_files],
                                "conflicts": [c.model_dump() for c in status_result.conflicts],
                            },
                        )
                    else:
                        await publish_git_op_completed(
                            job_id, status="failed", result_type="fetch",
                            error=fetch_result.error,
                        )
                    return bool(fetch_result.success)

                elif op_type == "git_status":
                    op_result = await sync_service.desktop_status()
                    await publish_git_op_completed(
                        job_id, status="success", result_type="status",
                        data=op_result.model_dump(),
                    )
                    return True

                elif op_type == "git_commit":
                    message = data.get("message", "Commit from Bifrost")
                    op_result = await sync_service.desktop_commit(message)
                    await publish_git_op_completed(
                        job_id, status="success" if op_result.success else "failed",
                        result_type="commit",
                        data=op_result.model_dump(),
                        error=op_result.error,
                    )
                    return bool(op_result.success)

                elif op_type == "git_sync":
                    # Combined pull + push + entity import
                    confirm_deletes = data.get("confirm_deletes", False)
                    op_result = await sync_service.desktop_sync(job_id=job_id, confirm_deletes=confirm_deletes)
                    if op_result.needs_delete_confirmation:
                        status_str = "needs_confirmation"
                    else:
                        status_str = "success" if op_result.success else ("conflict" if op_result.conflicts else "failed")
                    await publish_git_op_completed(
                        job_id, status=status_str, result_type="sync",
                        data=op_result.model_dump(),
                        error=op_result.error if not op_result.success else None,
                        pulled=op_result.pulled,
                        pushed=op_result.pushed_commits,
                        commit_sha=op_result.commit_sha,
                        conflicts=[c.model_dump() for c in op_result.conflicts] if op_result.conflicts else None,
                    )

                    # Clear repo dirty flag after successful sync
                    if op_result.success:
                        from src.core.repo_dirty import clear_repo_dirty
                        try:
                            await clear_repo_dirty()
                        except Exception as e:
                            logger.warning(f"Failed to clear repo dirty flag: {e}")
                    return bool(op_result.success or op_result.needs_delete_confirmation)

                elif op_type == "git_resolve":
                    resolutions = data.get("resolutions", {})
                    op_result = await sync_service.desktop_resolve(resolutions)
                    await publish_git_op_completed(
                        job_id, status="success" if op_result.success else "failed",
                        result_type="resolve",
                        data=op_result.model_dump() if op_result.success else None,
                        error=op_result.error,
                    )
                    return bool(op_result.success)

                elif op_type == "git_abort_merge":
                    op_result = await sync_service.desktop_abort_merge()
                    await publish_git_op_completed(
                        job_id, status="success" if op_result.success else "failed",
                        result_type="abort_merge",
                        data=op_result.model_dump() if op_result.success else None,
                        error=op_result.error,
                    )
                    return bool(op_result.success)

                elif op_type == "git_diff":
                    path = data.get("path", "")
                    op_result = await sync_service.desktop_diff(path)
                    await publish_git_op_completed(
                        job_id, status="success", result_type="diff",
                        data=op_result.model_dump(),
                    )
                    return True

                elif op_type == "git_discard":
                    paths = data.get("paths", [])
                    op_result = await sync_service.desktop_discard(paths)
                    await publish_git_op_completed(
                        job_id,
                        status="success" if op_result.success else "failed",
                        result_type="discard",
                        data=op_result.model_dump(),
                        error=op_result.error if not op_result.success else None,
                    )
                    return bool(op_result.success)

                else:
                    await publish_git_op_completed(
                        job_id, status="failed", result_type=result_type,
                        error=f"Unknown operation type: {op_type}",
                    )
                    return False

        except Exception as e:
            logger.error(f"Git operation {op_type} job {job_id} failed: {e}", exc_info=True)
            await publish_git_op_completed(
                job_id, status="failed", result_type=op_type.replace("git_", ""),
                error=str(e),
            )
            return False

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        if not self.running:
            return
        logger.info("Stopping Bifrost Scheduler...")
        self.running = False
        self._shutdown_event.set()

        tasks = [
            task
            for task in (*self._platform_job_tasks, self._leadership_task)
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._platform_job_tasks = []
        self._leadership_task = None

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
            self._heartbeat_task = None

        if self._diagnostics_task:
            self._diagnostics_task.cancel()
            await asyncio.gather(self._diagnostics_task, return_exceptions=True)
            self._diagnostics_task = None
        try:
            await remove_scheduler_replica(self._leadership_lease.owner_id)
        except Exception:
            logger.exception("Failed to remove scheduler diagnostics heartbeat")

        # Close database connections
        await close_db()
        logger.info("Database connections closed")

        logger.info("Bifrost Scheduler stopped")

    def handle_signal(self, signum: int, frame) -> None:
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, initiating shutdown...")
        asyncio.create_task(self.stop())


async def main() -> None:
    """Main entry point."""
    scheduler = Scheduler()

    # Register signal handlers
    def make_handler(s: signal.Signals) -> None:
        scheduler.handle_signal(int(s), None)

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, make_handler, signal.SIGINT)
    loop.add_signal_handler(signal.SIGTERM, make_handler, signal.SIGTERM)

    try:
        await scheduler.start()
    except Exception as e:
        logger.error(f"Scheduler error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
