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
    ScheduledTaskDefinition("schedule_processor", "Process schedule sources", "Every minute"),
    ScheduledTaskDefinition("deferred_execution_promoter", "Promote due executions", "Every minute"),
    ScheduledTaskDefinition("execution_cleanup", "Cleanup stuck executions", "Every 5 minutes"),
    ScheduledTaskDefinition("oauth_token_refresh", "Refresh expiring OAuth tokens", "Every 15 minutes", "durable_job"),
    ScheduledTaskDefinition("metrics_refresh", "Refresh platform metrics snapshot", "Hourly"),
    ScheduledTaskDefinition("knowledge_storage_refresh", "Refresh knowledge storage metrics", "Daily at 02:00 UTC"),
    ScheduledTaskDefinition("file_index_reconciliation", "Reconcile workspace file index", "Daily at 01:00 UTC", "durable_job"),
    ScheduledTaskDefinition("webhook_renewal", "Renew webhook subscriptions", "Every 6 hours", "durable_job"),
    ScheduledTaskDefinition("solution_update_check", "Check Solution updates", "Every 6 hours", "durable_job"),
    ScheduledTaskDefinition("solution_export_job_cleanup", "Cleanup Solution export artifacts", "Hourly"),
    ScheduledTaskDefinition("event_cleanup", "Cleanup old events", "Daily at 03:00 UTC"),
    ScheduledTaskDefinition("stuck_event_cleanup", "Cleanup stuck event deliveries", "Every 5 minutes"),
    ScheduledTaskDefinition("worker_metrics_sampling", "Sample worker metrics", "Every minute"),
    ScheduledTaskDefinition("worker_metrics_cleanup", "Cleanup worker metrics", "Daily at 04:00 UTC"),
    ScheduledTaskDefinition("scheduler_diagnostics_cleanup", "Cleanup scheduler diagnostics", "Hourly"),
    ScheduledTaskDefinition("summary_backfill_reconciliation", "Reconcile summary backfills", "Every minute"),
)

SCHEDULED_TASKS_BY_ID = {task.task_id: task for task in SCHEDULED_TASKS}
