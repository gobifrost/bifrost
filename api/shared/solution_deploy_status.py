"""Legacy Solution deploy-status projection from canonical PlatformJob state."""

from src.models.contracts.solutions import SolutionDeployJobStatus
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_deploy_jobs import SolutionDeployJob


def project_solution_deploy_job_status(
    projection: SolutionDeployJob,
    canonical: PlatformJob,
) -> SolutionDeployJobStatus:
    """Preserve the legacy DTO while reading durable state from PlatformJob."""
    if canonical.status == "succeeded":
        status = "succeeded"
    elif canonical.status in {"queued", "running", "waiting", "cancel_requested"}:
        status = "queued" if canonical.status == "queued" else "running"
    else:
        status = "failed"

    error = canonical.error_message
    if status == "failed" and error is None:
        error = {
            "cancelled": "Platform job was cancelled.",
            "requires_action": "Platform job requires action.",
        }.get(canonical.status, "Solution deploy failed.")

    result = canonical.result
    if (
        status == "failed"
        and projection.result is not None
        and projection.result.get("reason") == "inactive_install_exists"
    ):
        # ``_run_install_job`` records this caller-decision hint on its legacy
        # projection before raising. The canonical failure owns terminal state
        # and error, while the CLI still needs this metadata to offer
        # ``--reactivate``.
        result = projection.result
    if status == "running":
        if canonical.phase:
            result = {"phase": canonical.phase}
        elif projection.result is not None and "phase" in projection.result:
            # Solution handlers retain their granular build phase here. The
            # canonical job has no phase for those in-flight updates yet.
            result = {"phase": projection.result["phase"]}

    return SolutionDeployJobStatus(
        id=projection.id,
        install_id=projection.install_id,
        status=status,
        error=error,
        result=result,
        created_at=canonical.created_at,
        updated_at=canonical.updated_at,
    )
