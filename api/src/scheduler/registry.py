"""Code-owned catalog of system schedules shown in Diagnostics."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ScheduledTaskDefinition:
    task_id: str
    name: str
    schedule: str
    execution_mode: str = "leader"


@dataclass(frozen=True)
class ScheduledTaskOutcome:
    summary: str
    platform_job_id: UUID | None = None


SCHEDULED_TASKS: tuple[ScheduledTaskDefinition, ...] = (
    ScheduledTaskDefinition("schedule_processor", "Process Schedule Sources", "Every minute"),
    ScheduledTaskDefinition("deferred_execution_promoter", "Promote Due Executions", "Every minute"),
    ScheduledTaskDefinition("execution_cleanup", "Clean Up Stuck Executions", "Every 5 minutes"),
    ScheduledTaskDefinition("oauth_token_refresh", "Refresh Expiring OAuth Tokens", "Every 15 minutes", "durable_job"),
    ScheduledTaskDefinition("metrics_refresh", "Refresh Platform Metrics Snapshot", "Hourly"),
    ScheduledTaskDefinition("knowledge_storage_refresh", "Refresh Knowledge Storage Metrics", "Daily at 02:00 UTC"),
    ScheduledTaskDefinition("file_index_reconciliation", "Reconcile Workspace File Index", "Daily at 01:00 UTC", "durable_job"),
    ScheduledTaskDefinition("webhook_renewal", "Renew Webhook Subscriptions", "Every 6 hours", "durable_job"),
    ScheduledTaskDefinition("solution_update_check", "Check Solution Updates", "Every 6 hours", "durable_job"),
    ScheduledTaskDefinition("solution_export_job_cleanup", "Clean Up Solution Export Artifacts", "Hourly"),
    ScheduledTaskDefinition("event_cleanup", "Clean Up Old Events", "Daily at 03:00 UTC"),
    ScheduledTaskDefinition("stuck_event_cleanup", "Clean Up Stuck Event Deliveries", "Every 5 minutes"),
    ScheduledTaskDefinition("worker_metrics_sampling", "Sample Worker Metrics", "Every minute"),
    ScheduledTaskDefinition("worker_metrics_cleanup", "Clean Up Worker Metrics", "Daily at 04:00 UTC"),
    ScheduledTaskDefinition("scheduler_diagnostics_cleanup", "Clean Up Scheduler Diagnostics", "Hourly"),
    ScheduledTaskDefinition("summary_backfill_reconciliation", "Reconcile Summary Backfills", "Every minute"),
    ScheduledTaskDefinition("solution_build_reconciliation", "Reconcile Solution Builds", "Every minute"),
)

SCHEDULED_TASKS_BY_ID = {task.task_id: task for task in SCHEDULED_TASKS}
