"""``agent.evaluation_recorded`` PlatformJob handler."""

from __future__ import annotations

from shared.models import AgentRecordedEvaluationPayload

from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobPolicy,
)

JOB_TYPE = "agent.evaluation_recorded"
SEMANTIC_JOB_TYPE = "agent.evaluation_recorded_semantic"
PAYLOAD_VERSION = 1


async def handle_recorded_evaluation(
    context: PlatformJobContext,
    payload: AgentRecordedEvaluationPayload,
) -> dict | None:
    from shared.agent_recorded_admission import execute_recorded_evaluation_job

    return await execute_recorded_evaluation_job(
        context, evaluation_id=payload.evaluation_id
    )


AGENT_RECORDED_EVALUATION_DEFINITION = PlatformJobDefinition(
    job_type=JOB_TYPE,
    payload_version=PAYLOAD_VERSION,
    payload_model=AgentRecordedEvaluationPayload,
    handler=handle_recorded_evaluation,
    policy=PlatformJobPolicy(
        timeout_seconds=5 * 60,
        max_attempts=2,
        retry_on_runner_loss=True,
        allow_running_cancellation=True,
    ),
)

AGENT_RECORDED_SEMANTIC_EVALUATION_DEFINITION = PlatformJobDefinition(
    job_type=SEMANTIC_JOB_TYPE,
    payload_version=PAYLOAD_VERSION,
    payload_model=AgentRecordedEvaluationPayload,
    handler=handle_recorded_evaluation,
    policy=PlatformJobPolicy(
        timeout_seconds=10 * 60,
        max_attempts=1,
        retry_on_runner_loss=False,
        allow_running_cancellation=True,
    ),
)
