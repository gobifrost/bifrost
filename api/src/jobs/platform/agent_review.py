"""``agent.review`` PlatformJob handler."""

from __future__ import annotations

from typing import Any

from shared.models import AgentReviewJobPayload
from src.jobs.platform.base import PlatformJobContext, PlatformJobDefinition, PlatformJobPolicy

JOB_TYPE = "agent.review"
PAYLOAD_VERSION = 1


async def handle_agent_review(
    context: PlatformJobContext,
    payload: AgentReviewJobPayload,
) -> dict[str, Any] | None:
    from shared.agent_reviews import execute_agent_review_job

    return await execute_agent_review_job(context, payload.review_run_id)


AGENT_REVIEW_DEFINITION = PlatformJobDefinition(
    job_type=JOB_TYPE,
    payload_version=PAYLOAD_VERSION,
    payload_model=AgentReviewJobPayload,
    handler=handle_agent_review,
    policy=PlatformJobPolicy(
        timeout_seconds=10 * 60,
        max_attempts=1,
        retry_on_runner_loss=False,
        min_memory_headroom_mb=64,
        allow_running_cancellation=True,
    ),
)
