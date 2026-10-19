"""Platform-job definitions for reviewed workflow permission conversion."""

from __future__ import annotations

from src.jobs.platform.base import PlatformJobDefinition, PlatformJobPolicy
from shared.models import (
    WorkflowConversionAnalyzePayload,
    WorkflowConversionApplyPayload,
    WorkflowConversionCutoverPayload,
)
from shared.workflow_conversions import (
    ANALYZE_JOB_TYPE,
    APPLY_JOB_TYPE,
    CUTOVER_JOB_TYPE,
    run_analysis,
    run_apply,
    run_cutover,
)


ANALYZE_JOB_DEFINITION = PlatformJobDefinition(
    job_type=ANALYZE_JOB_TYPE,
    payload_version=1,
    payload_model=WorkflowConversionAnalyzePayload,
    handler=run_analysis,
    policy=PlatformJobPolicy(
        timeout_seconds=60 * 60,
        max_attempts=2,
        max_concurrency=1,
        retry_on_runner_loss=True,
        min_memory_headroom_mb=384,
        admission_memory_ratio=0.85,
        hard_memory_ratio=0.95,
        allow_running_cancellation=True,
    ),
)

APPLY_JOB_DEFINITION = PlatformJobDefinition(
    job_type=APPLY_JOB_TYPE,
    payload_version=1,
    payload_model=WorkflowConversionApplyPayload,
    handler=run_apply,
    policy=PlatformJobPolicy(
        timeout_seconds=60 * 60,
        max_attempts=2,
        max_concurrency=1,
        retry_on_runner_loss=True,
        min_memory_headroom_mb=384,
        admission_memory_ratio=0.85,
        hard_memory_ratio=0.95,
        allow_running_cancellation=True,
    ),
)

CUTOVER_JOB_DEFINITION = PlatformJobDefinition(
    job_type=CUTOVER_JOB_TYPE,
    payload_version=1,
    payload_model=WorkflowConversionCutoverPayload,
    handler=run_cutover,
    policy=PlatformJobPolicy(
        timeout_seconds=15 * 60,
        max_attempts=2,
        max_concurrency=1,
        retry_on_runner_loss=True,
        min_memory_headroom_mb=384,
        admission_memory_ratio=0.85,
        hard_memory_ratio=0.95,
        allow_running_cancellation=True,
    ),
)
