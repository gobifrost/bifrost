"""Scheduler callback for pseudonymous daily usage reporting."""

from src.scheduler.registry import ScheduledTaskOutcome


async def report_usage_schedule() -> ScheduledTaskOutcome:
    from shared.usage_reporting import report_usage

    return await report_usage()
