"""Durable server-side build and atomic activation for independent Apps."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel

from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.services.application_build import rebuild_application_from_source
from src.services.application_deploy_storage import ApplicationDeployStorage
from src.services.application_source_archive import (
    InvalidApplicationSource,
    prepare_application_source_archive,
)
from src.services.application_source_resolver import ResolvedApplicationSource

logger = logging.getLogger(__name__)


class ApplicationDeployPayload(BaseModel):
    application_id: UUID
    deployment_id: UUID
    input_sha256: str


async def run_application_deploy(
    context: PlatformJobContext, payload: ApplicationDeployPayload
) -> dict[str, str]:
    storage = ApplicationDeployStorage(context.job_id)
    try:
        await context.report("Loading App source", percent=5)
        with tempfile.TemporaryDirectory(prefix="bifrost-app-deploy-") as tmp:
            source_zip = Path(tmp) / "source.zip"
            await storage.copy_to_path(
                source_zip, expected_sha256=payload.input_sha256
            )
            retained_source_zip = Path(tmp) / "retained-source.zip"
            try:
                source_files = await asyncio.to_thread(
                    prepare_application_source_archive, source_zip, retained_source_zip
                )
            except InvalidApplicationSource as exc:
                raise PlatformJobFailure(exc.code, exc.message) from exc

            await context.report("Building App", percent=20)
            result = await rebuild_application_from_source(
                application_id=payload.application_id,
                deployment_id=payload.deployment_id,
                source=ResolvedApplicationSource(
                    files=source_files,
                    dependencies={},
                    source_kind="independent",
                ),
                retained_source_zip=retained_source_zip,
            )

        await context.report("App deployed", percent=100)
        return result
    finally:
        try:
            await storage.delete()
        except Exception:
            logger.warning(
                "Failed to delete transient App source for job %s",
                context.job_id,
                exc_info=True,
            )


APPLICATION_DEPLOY_DEFINITION = PlatformJobDefinition(
    job_type="application.deploy",
    payload_version=1,
    payload_model=ApplicationDeployPayload,
    handler=run_application_deploy,
    policy=PlatformJobPolicy(
        timeout_seconds=20 * 60,
        max_attempts=1,
        max_concurrency=1,
        min_memory_headroom_mb=512,
        execution_class="build",
    ),
    display_name="App deploys",
    description=(
        "Compile and publish independent apps (npm install + vite build) "
        "in isolated pods instead of the scheduler."
    ),
)
