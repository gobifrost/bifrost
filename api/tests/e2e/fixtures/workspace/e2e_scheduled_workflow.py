"""
E2E Scheduled Workflow

A workflow for testing the schedules endpoint.
"""

import logging

from bifrost import workflow

logger = logging.getLogger(__name__)


@workflow(
    name="e2e_scheduled_task",
    description="E2E scheduled workflow for testing cron scheduling",
    category="e2e_testing",
    tags=["e2e", "test", "scheduled"],
)
async def e2e_scheduled_task() -> dict:
    """
    E2E scheduled workflow that runs on a cron schedule.

    Returns:
        Dictionary with execution result
    """
    logger.info("E2E scheduled task executed")
    return {
        "status": "success",
        "message": "Scheduled task completed",
    }
