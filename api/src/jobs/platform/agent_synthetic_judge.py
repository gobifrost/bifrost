"""Synthetic semantic judge postprocessing PlatformJob definition."""

from __future__ import annotations

from typing import Any

from shared.models import SyntheticSemanticJudgePayload
from src.jobs.platform.base import PlatformJobContext, PlatformJobDefinition, PlatformJobPolicy

JOB_TYPE = "agent.evaluation_synthetic_semantic"
PAYLOAD_VERSION = 1


async def run_synthetic_semantic_judge(
    context: PlatformJobContext,
    payload: SyntheticSemanticJudgePayload,
) -> dict[str, Any]:
    from shared.agent_synthetic_judge import run_synthetic_semantic_judge_job

    return await run_synthetic_semantic_judge_job(context, payload)


SYNTHETIC_SEMANTIC_JUDGE_DEFINITION = PlatformJobDefinition(
    job_type=JOB_TYPE,
    payload_version=PAYLOAD_VERSION,
    payload_model=SyntheticSemanticJudgePayload,
    handler=run_synthetic_semantic_judge,
    policy=PlatformJobPolicy(
        timeout_seconds=10 * 60,
        max_attempts=1,
        retry_on_runner_loss=False,
        min_memory_headroom_mb=64,
        allow_running_cancellation=True,
    ),
)
