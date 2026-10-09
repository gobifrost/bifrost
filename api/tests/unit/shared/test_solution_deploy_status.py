from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from shared.solution_deploy_status import project_solution_deploy_job_status


@pytest.mark.parametrize(
    (
        "canonical_status, phase, result, error_message, "
        "expected_status, expected_result, expected_error"
    ),
    [
        (
            "queued",
            None,
            None,
            None,
            "queued",
            None,
            None,
        ),
        (
            "waiting",
            "Waiting for runner capacity",
            None,
            None,
            "running",
            {"phase": "Waiting for runner capacity"},
            None,
        ),
        (
            "cancel_requested",
            "Cancellation requested",
            None,
            None,
            "running",
            {"phase": "Cancellation requested"},
            None,
        ),
        (
            "failed",
            "Failed",
            None,
            "Platform job was stopped before the runner container exceeded its memory limit.",
            "failed",
            None,
            "Platform job was stopped before the runner container exceeded its memory limit.",
        ),
        (
            "cancelled",
            "Cancelled",
            None,
            None,
            "failed",
            None,
            "Platform job was cancelled.",
        ),
        (
            "requires_action",
            "Action required",
            None,
            None,
            "failed",
            None,
            "Platform job requires action.",
        ),
        (
            "succeeded",
            "Completed",
            {"solution_id": "canonical-solution"},
            None,
            "succeeded",
            {"solution_id": "canonical-solution"},
            None,
        ),
        (
            "running",
            "Building application distribution",
            None,
            None,
            "running",
            {"phase": "Building application distribution"},
            None,
        ),
    ],
)
def test_project_solution_deploy_job_status_uses_canonical_state(
    canonical_status,
    phase,
    result,
    error_message,
    expected_status,
    expected_result,
    expected_error,
):
    now = datetime.now(timezone.utc)
    projection = SolutionDeployJob(
        id=uuid4(),
        status="running",
        result={"phase": "stale legacy phase"},
        created_at=now,
        updated_at=now,
    )
    canonical = PlatformJob(
        id=projection.id,
        job_type="solution.deploy",
        payload_version=1,
        payload={"protected": True},
        requested_by_user_id=str(uuid4()),
        requested_by_email="admin@example.com",
        requested_by_name="Admin",
        resource_type="solution_deploy",
        resource_id=str(projection.id),
        title="Solution deploy",
        status=canonical_status,
        phase=phase,
        result=result,
        error_message=error_message,
        created_at=now,
        updated_at=now,
    )

    status = project_solution_deploy_job_status(projection, canonical)

    assert status.status == expected_status
    assert status.result == expected_result
    assert status.error == expected_error


def test_failed_install_preserves_inactive_install_reactivation_hint():
    """The legacy DTO still carries the CLI's install recovery instruction."""
    now = datetime.now(timezone.utc)
    projection = SolutionDeployJob(
        id=uuid4(),
        status="failed",
        error="An inactive install of 'acme' already exists",
        result={
            "reason": "inactive_install_exists",
            "solution_id": "solution-123",
            "slug": "acme",
        },
        created_at=now,
        updated_at=now,
    )
    canonical = PlatformJob(
        id=projection.id,
        job_type="solution.deploy",
        payload_version=1,
        payload={"protected": True},
        requested_by_user_id=str(uuid4()),
        requested_by_email="admin@example.com",
        requested_by_name="Admin",
        resource_type="solution_deploy",
        resource_id=str(projection.id),
        title="Solution deploy",
        status="failed",
        phase="Failed",
        error_message="An inactive install of 'acme' already exists",
        created_at=now,
        updated_at=now,
    )

    status = project_solution_deploy_job_status(projection, canonical)

    assert status.status == "failed"
    assert status.error == "An inactive install of 'acme' already exists"
    assert status.result == {
        "reason": "inactive_install_exists",
        "solution_id": "solution-123",
        "slug": "acme",
    }


def test_running_job_preserves_projection_phase_when_canonical_phase_is_missing():
    """Solution handlers report granular active phases on their projection."""
    now = datetime.now(timezone.utc)
    projection = SolutionDeployJob(
        id=uuid4(),
        status="running",
        result={"phase": "validating bundle and applying resources"},
        created_at=now,
        updated_at=now,
    )
    canonical = PlatformJob(
        id=projection.id,
        job_type="solution.deploy",
        payload_version=1,
        payload={"protected": True},
        requested_by_user_id=str(uuid4()),
        requested_by_email="admin@example.com",
        requested_by_name="Admin",
        resource_type="solution_deploy",
        resource_id=str(projection.id),
        title="Solution deploy",
        status="running",
        phase=None,
        created_at=now,
        updated_at=now,
    )

    status = project_solution_deploy_job_status(projection, canonical)

    assert status.status == "running"
    assert status.result == {"phase": "validating bundle and applying resources"}
