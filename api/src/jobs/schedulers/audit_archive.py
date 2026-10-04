"""Scheduled enqueue hook for the daily audit archive."""

from src.scheduler.registry import ScheduledTaskOutcome


async def archive_audit_events_schedule() -> ScheduledTaskOutcome:
    from src.jobs.platform.audit_archive import enqueue_automatic_audit_archive

    return await enqueue_automatic_audit_archive()
