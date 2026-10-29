"""Durable scheduler adapter for all Solution deploy and install variants."""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobFailure,
    define_platform_job,
)
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.services.solutions.deploy_job_storage import SolutionDeployJobStorage

logger = logging.getLogger(__name__)


class SolutionDeployPayload(BaseModel):
    deploy_job_id: UUID
    kind: Literal["deploy", "install", "install_from_repo"]
    install_id: UUID | None = None
    input_sha256: str
    options: dict[str, Any]


@dataclass
class _SolutionDeployReporter:
    """Throttle bounded deployment progress onto the shared job record."""

    context: PlatformJobContext
    last_percent: float = 0

    async def progress(self, phase: str, percent: float) -> None:
        bounded_percent = min(99, max(self.last_percent, percent))
        if bounded_percent == self.last_percent:
            return
        self.last_percent = bounded_percent
        await self.context.report(phase, percent=bounded_percent)

    async def timing(self, category: str, elapsed_ms: int) -> None:
        try:
            await self.context.log(
                "info",
                "solution_deploy_timing",
                f"Solution deploy {category} elapsed {elapsed_ms} ms",
            )
        except Exception as exc:  # noqa: BLE001 - diagnostics must not abort publication
            logger.warning(
                "Failed to publish Solution deploy timing category %s (%s)",
                category,
                type(exc).__name__,
            )


async def run_solution_deploy(
    context: PlatformJobContext,
    payload: SolutionDeployPayload,
) -> dict:
    from src.routers.solutions import _run_deploy_job, _run_install_job

    storage = SolutionDeployJobStorage(payload.deploy_job_id)
    await context.report("Loading staged Solution input", percent=2)
    reporter = _SolutionDeployReporter(context, last_percent=2)
    try:
        with tempfile.TemporaryDirectory(prefix="bifrost-solution-job-") as tmp:
            zip_path = Path(tmp) / "input.zip"
            await storage.copy_to_path(
                zip_path,
                expected_sha256=payload.input_sha256,
            )
            await reporter.progress("Preparing Solution deployment", 8)
            if payload.kind in {"deploy", "install_from_repo"}:
                if payload.install_id is None:
                    raise PlatformJobFailure(
                        "solution_not_found", "Solution install is missing."
                    )
                await _run_deploy_job(
                    payload.deploy_job_id,
                    payload.install_id,
                    zip_path,
                    force=bool(payload.options.get("force", False)),
                    # A from-repo install is created git-connected by the same
                    # request that enqueues this first deploy; without the
                    # exemption the one-writer refusal would deadlock creation.
                    # Manual deploys (kind="deploy") stay refused.
                    allow_connected_install=payload.kind == "install_from_repo",
                    # Same first-deploy-only exemption: if it fails, the
                    # brand-new install is deleted atomically with the job's
                    # failed status (see _run_deploy_job docstring).
                    delete_install_on_failure=payload.kind == "install_from_repo",
                    progress_callback=reporter.progress,
                    timing_callback=reporter.timing,
                )
            else:
                raw_org_id = payload.options.get("organization_id")
                await _run_install_job(
                    payload.deploy_job_id,
                    zip_path,
                    organization_id=UUID(raw_org_id) if raw_org_id else None,
                    config_values=payload.options.get("config_values", {}),
                    deployer_email=str(payload.options["deployer_email"]),
                    force=bool(payload.options.get("force", False)),
                    password=payload.options.get("password"),
                    replace_secrets=bool(payload.options.get("replace_secrets", False)),
                    replace_data=bool(payload.options.get("replace_data", False)),
                    reactivate=bool(payload.options.get("reactivate", False)),
                    progress_callback=reporter.progress,
                    timing_callback=reporter.timing,
                )
        failure: PlatformJobFailure | None = None
        result: dict[str, Any] = {}
        async with get_db_context() as db:
            projection = await db.get(SolutionDeployJob, payload.deploy_job_id)
            if projection is None:
                raise PlatformJobFailure("deploy_job_missing", "Deploy job is missing.")
            if projection.status != "succeeded":
                # A from-repo install's brand-new row is deleted by
                # _run_deploy_job itself, atomically with this job's failed
                # status write (see its docstring) — nothing to clean up here.
                failure = PlatformJobFailure(
                    "solution_deploy_failed",
                    projection.error or "Solution deploy failed.",
                )
            else:
                result = projection.result or {}
        if failure is not None:
            raise failure
        await context.report("Solution deploy complete", percent=100)
        await context.log(
            "info",
            "solution_deploy_completed",
            f"Solution {payload.kind} job {payload.deploy_job_id} completed",
        )
        return result
    finally:
        try:
            await storage.delete()
        except Exception:
            logger.warning(
                "Failed to delete staged Solution input for job %s",
                payload.deploy_job_id,
                exc_info=True,
            )


SOLUTION_DEPLOY_DEFINITION = define_platform_job(
    "solution.deploy", SolutionDeployPayload, run_solution_deploy
)
