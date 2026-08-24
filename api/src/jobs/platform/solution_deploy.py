"""Durable scheduler adapter for all Solution deploy and install variants."""

from __future__ import annotations

import tempfile
import logging
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solutions import Solution
from src.services.solutions.deploy_job_storage import SolutionDeployJobStorage

logger = logging.getLogger(__name__)


class SolutionDeployPayload(BaseModel):
    deploy_job_id: UUID
    kind: Literal["deploy", "install", "install_from_repo"]
    install_id: UUID | None = None
    input_sha256: str
    options: dict[str, Any]


async def run_solution_deploy(
    context: PlatformJobContext,
    payload: SolutionDeployPayload,
) -> dict:
    from src.routers.solutions import _run_deploy_job, _run_install_job
    from src.services.builder.deploy_sync import (
        BuilderDeployLinkInvalid,
        parse_builder_deploy_link,
        sync_builder_deploy_state,
    )

    storage = SolutionDeployJobStorage(payload.deploy_job_id)
    await context.report("Loading staged Solution input", percent=2)
    try:
        try:
            builder_link = parse_builder_deploy_link(
                payload.options,
                payload.install_id,
            )
        except BuilderDeployLinkInvalid as exc:
            raise PlatformJobFailure("builder_deploy_link_invalid", str(exc)) from exc
        if builder_link is not None:
            try:
                async with get_db_context() as db:
                    await sync_builder_deploy_state(db, builder_link, running=True)
            except BuilderDeployLinkInvalid as exc:
                raise PlatformJobFailure(
                    "builder_deploy_link_invalid", str(exc)
                ) from exc

        with tempfile.TemporaryDirectory(prefix="bifrost-solution-job-") as tmp:
            zip_path = Path(tmp) / "input.zip"
            await storage.copy_to_path(
                zip_path,
                expected_sha256=payload.input_sha256,
            )
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
                    promotion=bool(payload.options.get("promotion", False)),
                    isolated_app_builds=bool(
                        payload.options.get("isolated_app_builds", False)
                    ),
                    source_revision_id=(
                        builder_link.revision_id if builder_link is not None else None
                    ),
                    requested_by=_requested_by_uuid(context.requested_by_user_id),
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
                )
        failure: PlatformJobFailure | None = None
        link_failure: PlatformJobFailure | None = None
        result: dict[str, Any] = {}
        async with get_db_context() as db:
            projection = await db.get(SolutionDeployJob, payload.deploy_job_id)
            if projection is None:
                raise PlatformJobFailure("deploy_job_missing", "Deploy job is missing.")
            if projection.status != "succeeded":
                if payload.kind == "install_from_repo" and payload.install_id:
                    projection.install_id = None
                    await db.flush()
                    orphan = await db.get(Solution, payload.install_id)
                    if orphan is not None:
                        await db.delete(orphan)
                failure = PlatformJobFailure(
                    "solution_deploy_failed",
                    projection.error or "Solution deploy failed.",
                )
            else:
                result = projection.result or {}
            if builder_link is not None:
                try:
                    await sync_builder_deploy_state(
                        db,
                        builder_link,
                        deploy_job=projection,
                    )
                except BuilderDeployLinkInvalid as exc:
                    link_failure = PlatformJobFailure(
                        "builder_deploy_link_invalid",
                        str(exc),
                    )
        # The failed install cleanup above must commit before the platform job
        # reports failure. Raising inside get_db_context would roll the delete
        # back and leave an orphan that blocks a retry with the same slug.
        if failure is not None:
            raise failure
        if link_failure is not None:
            raise link_failure
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


def _requested_by_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except (TypeError, ValueError):
        return None


SOLUTION_DEPLOY_DEFINITION = PlatformJobDefinition(
    job_type="solution.deploy",
    payload_version=1,
    payload_model=SolutionDeployPayload,
    handler=run_solution_deploy,
    policy=PlatformJobPolicy(
        timeout_seconds=60 * 60,
        max_attempts=2,
        min_memory_headroom_mb=512,
    ),
    encrypt_payload=True,
)
