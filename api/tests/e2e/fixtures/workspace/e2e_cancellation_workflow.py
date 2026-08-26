"""
E2E Cancellation Test Workflow

A workflow with a configurable sleep duration for testing cancellation.
This workflow is used by E2E tests to verify that running workflows can be cancelled.
"""

import logging
import time

from bifrost import workflow

logger = logging.getLogger(__name__)


@workflow(
    name="e2e_cancellation_test",
    description="Workflow with configurable sleep for cancellation testing",
    category="e2e_testing",
    tags=["e2e", "test", "cancellation"],
)
async def e2e_cancellation_test(sleep_seconds: int = 30):
    """
    A workflow that sleeps for a configurable duration.
    Used to test cancellation of running workflows.
    """
    logger.info(f"Starting sleep for {sleep_seconds} seconds...")
    time.sleep(sleep_seconds)
    logger.info("Sleep completed - this should not appear if cancelled")
    return {"status": "completed", "slept_for": sleep_seconds}
