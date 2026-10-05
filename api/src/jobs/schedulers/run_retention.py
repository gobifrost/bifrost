"""Scheduled enqueue hook for the daily run and event retention."""

from src.scheduler.registry import ScheduledTaskOutcome


async def delete_expired_runs_schedule() -> ScheduledTaskOutcome:
    from src.jobs.platform.run_retention import enqueue_automatic_run_retention

    return await enqueue_automatic_run_retention()
