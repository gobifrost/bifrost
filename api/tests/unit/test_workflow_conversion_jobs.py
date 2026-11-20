"""Conversion uses the shared durable runner and safe recovery policies."""

import pytest
from pydantic import ValidationError

from src.jobs.platform.registry import get_platform_job_definition
from src.jobs.platform.workflow_conversion import (
    ANALYZE_JOB_DEFINITION,
    APPLY_JOB_DEFINITION,
    CUTOVER_JOB_DEFINITION,
)


@pytest.mark.parametrize(
    "definition", [ANALYZE_JOB_DEFINITION, APPLY_JOB_DEFINITION, CUTOVER_JOB_DEFINITION]
)
def test_registered_conversion_jobs_support_fenced_recovery_and_cancellation(
    definition,
):
    assert get_platform_job_definition(definition.job_type) is definition
    assert definition.policy.retry_on_runner_loss
    assert definition.policy.max_attempts > 1
    assert not definition.policy.retry_on_failure
    assert definition.policy.allow_running_cancellation
    assert definition.policy.min_memory_headroom_mb >= 384
    assert 0 < definition.policy.hard_memory_ratio < 1


def test_durable_analysis_payload_cannot_widen_partial_scope_into_platform_cutover():
    with pytest.raises(ValidationError, match="whole platform inventory"):
        ANALYZE_JOB_DEFINITION.payload_model.model_validate(
            {"scope": "global", "platform_cutover": True}
        )


def test_cutover_is_bounded_api_work_and_creates_no_physical_demand():
    from src.jobs.platform.catalog import api_platform_job_types, executor_platform_job_types, get_platform_job_metadata
    metadata = get_platform_job_metadata(CUTOVER_JOB_DEFINITION.job_type)
    assert metadata.execution_owner == "api"
    assert not metadata.required_compute_capabilities
    assert metadata.job_type in api_platform_job_types()
    assert metadata.job_type not in executor_platform_job_types()
    # Long source review must not consume the reserved maintenance control slot.
    assert not metadata.api_control
