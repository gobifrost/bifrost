"""Scheduled enqueue hook for expired artifact cleanup."""

from src.scheduler.registry import ScheduledTaskOutcome


async def cleanup_expired_chat_artifacts_schedule() -> ScheduledTaskOutcome:
    from src.jobs.platform.system_maintenance import (
        enqueue_automatic_artifact_retention_cleanup,
    )

    return await enqueue_automatic_artifact_retention_cleanup()
