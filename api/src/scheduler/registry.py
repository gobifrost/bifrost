"""Code-owned catalog of system schedules.

This catalog is the single source for both the trigger leader (which installs
one APScheduler job per entry) and Diagnostics (which lists every entry). It
stays import-light: callbacks are ``module:function`` references resolved only
by the elected leader.
"""

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class ScheduledTaskDefinition:
    task_id: str
    name: str
    schedule: str
    execution_mode: str = "leader"
    callback: str = ""
    trigger: Literal["cron", "interval"] = "interval"
    trigger_args: dict[str, int | str] = field(default_factory=dict, hash=False)
    run_at_startup: bool = False


@dataclass(frozen=True)
class ScheduledTaskOutcome:
    summary: str
    platform_job_id: UUID | None = None


_MAINTENANCE = "src.jobs.platform.system_maintenance"

SCHEDULED_TASKS: tuple[ScheduledTaskDefinition, ...] = (
    ScheduledTaskDefinition(
        "schedule_processor", "Process Schedule Sources", "Every minute",
        callback="src.jobs.schedulers.cron_scheduler:process_schedule_sources",
        trigger="cron", trigger_args={"minute": "*/1"},
    ),
    ScheduledTaskDefinition(
        "deferred_execution_promoter", "Promote Due Executions", "Every minute",
        callback="src.jobs.schedulers.deferred_execution_promoter:promote_due_executions",
        trigger_args={"seconds": 60}, run_at_startup=True,
    ),
    ScheduledTaskDefinition(
        "execution_cleanup", "Clean Up Stuck Executions", "Every 5 minutes",
        callback="src.jobs.schedulers.execution_cleanup:cleanup_stuck_executions",
        trigger="cron", trigger_args={"minute": "*/5"}, run_at_startup=True,
    ),
    ScheduledTaskDefinition(
        "oauth_token_refresh", "Refresh Expiring OAuth Tokens", "Every 15 minutes", "durable_job",
        callback=f"{_MAINTENANCE}:enqueue_automatic_oauth_refresh",
        trigger_args={"minutes": 15}, run_at_startup=True,
    ),
    ScheduledTaskDefinition(
        "metrics_refresh", "Refresh Platform Metrics Snapshot", "Hourly",
        callback="src.jobs.schedulers.metrics_refresh:refresh_metrics_snapshot",
        trigger_args={"minutes": 60}, run_at_startup=True,
    ),
    ScheduledTaskDefinition(
        "knowledge_storage_refresh", "Refresh Knowledge Storage Metrics", "Daily at 02:00 UTC",
        callback="src.jobs.schedulers.knowledge_storage_refresh:refresh_knowledge_storage_daily",
        trigger="cron", trigger_args={"hour": 2, "minute": 0}, run_at_startup=True,
    ),
    ScheduledTaskDefinition(
        "file_index_reconciliation", "Reconcile Workspace File Index", "Daily at 01:00 UTC", "durable_job",
        callback=f"{_MAINTENANCE}:enqueue_automatic_file_index_reconciliation",
        trigger="cron", trigger_args={"hour": 1, "minute": 0}, run_at_startup=True,
    ),
    ScheduledTaskDefinition(
        "webhook_renewal", "Renew Webhook Subscriptions", "Every 6 hours", "durable_job",
        callback=f"{_MAINTENANCE}:enqueue_automatic_webhook_renewal",
        trigger_args={"hours": 6}, run_at_startup=True,
    ),
    ScheduledTaskDefinition(
        "solution_update_check", "Check Solution Updates", "Every 6 hours", "durable_job",
        callback=f"{_MAINTENANCE}:enqueue_automatic_solution_update_check",
        trigger_args={"hours": 6}, run_at_startup=True,
    ),
    ScheduledTaskDefinition(
        "model_catalog_refresh", "Refresh Model Catalog", "Every 4 hours", "durable_job",
        callback=f"{_MAINTENANCE}:enqueue_automatic_model_catalog_refresh",
        trigger_args={"hours": 4}, run_at_startup=True,
    ),
    ScheduledTaskDefinition(
        "solution_export_job_cleanup", "Clean Up Solution Export Artifacts", "Hourly",
        callback="src.jobs.schedulers.solution_export_jobs:cleanup_expired_solution_export_jobs",
        trigger_args={"hours": 1},
    ),
    ScheduledTaskDefinition(
        "audit_archive", "Archive Audit Events", "Daily at 02:30 UTC", "durable_job",
        callback="src.jobs.schedulers.audit_archive:archive_audit_events_schedule",
        trigger="cron", trigger_args={"hour": 2, "minute": 30},
    ),
    ScheduledTaskDefinition(
        "artifact_retention_cleanup", "Clean Up Artifacts", "Daily at 03:30 UTC", "durable_job",
        callback="src.jobs.schedulers.artifact_retention:cleanup_expired_chat_artifacts_schedule",
        trigger="cron", trigger_args={"hour": 3, "minute": 30},
    ),
    ScheduledTaskDefinition(
        "run_retention", "Run Retention", "Daily at 03:00 UTC", "durable_job",
        callback="src.jobs.schedulers.run_retention:delete_expired_runs_schedule",
        trigger="cron", trigger_args={"hour": 3, "minute": 0},
    ),
    ScheduledTaskDefinition(
        "stuck_event_cleanup", "Clean Up Stuck Event Deliveries", "Every 5 minutes",
        callback="src.jobs.schedulers.event_cleanup:cleanup_stuck_events",
        trigger="cron", trigger_args={"minute": "*/5"}, run_at_startup=True,
    ),
    ScheduledTaskDefinition(
        "worker_metrics_sampling", "Sample Worker Metrics", "Every minute",
        callback="src.jobs.schedulers.worker_metrics_sampling:sample_worker_metrics",
        trigger_args={"seconds": 60}, run_at_startup=True,
    ),
    ScheduledTaskDefinition(
        "worker_metrics_cleanup", "Clean Up Worker Metrics", "Daily at 04:00 UTC",
        callback="src.jobs.schedulers.worker_metrics_cleanup:cleanup_old_worker_metrics",
        trigger="cron", trigger_args={"hour": 4, "minute": 0},
    ),
    ScheduledTaskDefinition(
        "scheduler_diagnostics_cleanup", "Clean Up Scheduler Diagnostics", "Hourly",
        callback="src.services.scheduler_diagnostics:cleanup_scheduler_diagnostics",
        trigger_args={"hours": 1},
    ),
    ScheduledTaskDefinition(
        "summary_backfill_reconciliation", "Reconcile Summary Backfills", "Every minute",
        callback="src.jobs.platform.summary_backfill:reconcile_summary_backfill_jobs",
        trigger_args={"seconds": 60}, run_at_startup=True,
    ),
    ScheduledTaskDefinition(
        "workspace_bundle_preview_cleanup", "Clean Up Workspace Bundle Previews", "Hourly",
        callback="src.jobs.schedulers.workspace_bundle_cleanup:cleanup_workspace_bundle_previews",
        trigger_args={"hours": 1}, run_at_startup=True,
    ),
    ScheduledTaskDefinition(
        "workflow_operation_usage_flush", "Flush Workflow Operation Usage Counters", "Every 15 minutes",
        callback="src.jobs.schedulers.workflow_operation_usage_flush:flush_workflow_operation_usage",
        trigger_args={"minutes": 15}, run_at_startup=True,
    ),
    ScheduledTaskDefinition(
        "execution_worker_cleanup", "Clean Up Executors", "Hourly",
        callback="src.services.worker_pool.maintenance:prune_execution_workers",
        trigger_args={"hours": 1},
    ),
    ScheduledTaskDefinition(
        "logo_thumbnail_backfill", "Backfill Entity Logo Thumbnails", "Every minute",
        callback="src.jobs.schedulers.logo_thumbnail_backfill:backfill_logo_thumbnails",
        trigger_args={"minutes": 1}, run_at_startup=True,
    ),
)

SCHEDULED_TASKS_BY_ID = {task.task_id: task for task in SCHEDULED_TASKS}
