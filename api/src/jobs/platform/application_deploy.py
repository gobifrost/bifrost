"""Durable server-side build and atomic activation for independent Apps."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import UUID

from pydantic import BaseModel

from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.models.orm.applications import Application
from src.services.application_deploy_storage import ApplicationDeployStorage
from src.services.solutions.app_build import SolutionAppBuilder

logger = logging.getLogger(__name__)
MAX_EXPANDED_SOURCE_BYTES = 256 * 1024 * 1024


class ApplicationDeployPayload(BaseModel):
    application_id: UUID
    deployment_id: UUID
    input_sha256: str


def _read_source_zip(path: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    expanded = 0
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise PlatformJobFailure("invalid_app_source", "App source is not a valid zip file.") from exc
    with archive:
        for info in archive.infolist():
            rel = PurePosixPath(info.filename)
            if info.is_dir():
                continue
            if rel.is_absolute() or ".." in rel.parts or not rel.parts:
                raise PlatformJobFailure(
                    "invalid_app_source", f"Unsafe path in App source: {info.filename}"
                )
            expanded += info.file_size
            if expanded > MAX_EXPANDED_SOURCE_BYTES:
                raise PlatformJobFailure(
                    "app_source_too_large",
                    "Expanded App source exceeds the 256 MiB limit.",
                )
            files[rel.as_posix()] = archive.read(info)
    if "package.json" not in files or "index.html" not in files:
        raise PlatformJobFailure(
            "invalid_app_source",
            "App source must be a Vite project with package.json and index.html at its root.",
        )
    return files


async def run_application_deploy(
    context: PlatformJobContext, payload: ApplicationDeployPayload
) -> dict[str, str]:
    storage = ApplicationDeployStorage(context.job_id)
    builder = SolutionAppBuilder()
    activated = False
    old_deployment_id: UUID | None = None
    try:
        await context.report("Loading App source", percent=5)
        with tempfile.TemporaryDirectory(prefix="bifrost-app-deploy-") as tmp:
            source_zip = Path(tmp) / "source.zip"
            await storage.copy_to_path(
                source_zip, expected_sha256=payload.input_sha256
            )
            source_files = _read_source_zip(source_zip)

        await context.report("Building App", percent=20)
        try:
            dist = await asyncio.to_thread(
                builder.compile_dist,
                payload.application_id,
                source_files,
                {},
            )
        except PlatformJobFailure:
            raise
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or b"").decode(errors="replace")[-4000:]
            raise PlatformJobFailure(
                "app_build_failed",
                f"Vite build failed.\n{detail}" if detail else "Vite build failed.",
            ) from exc
        except Exception as exc:
            raise PlatformJobFailure("app_build_failed", str(exc)) from exc

        await context.report("Uploading App artifact", percent=70)
        await builder.upload_deployment(
            payload.application_id, payload.deployment_id, dist
        )

        await context.report("Activating App", percent=95)
        async with get_db_context() as db:
            app = await db.get(Application, payload.application_id)
            if app is None or app.solution_id is not None or app.app_model != "standalone_v2":
                raise PlatformJobFailure(
                    "app_not_deployable", "The App no longer supports independent deployment."
                )
            old_deployment_id = app.active_deployment_id
            app.active_deployment_id = payload.deployment_id
            app.deployed_at = datetime.now(timezone.utc)
            await db.flush()
        activated = True

        if old_deployment_id and old_deployment_id != payload.deployment_id:
            try:
                await builder.delete_deployment(payload.application_id, old_deployment_id)
            except Exception:
                logger.warning(
                    "Failed to remove superseded App deployment %s",
                    old_deployment_id,
                    exc_info=True,
                )
        await context.report("App deployed", percent=100)
        return {
            "application_id": str(payload.application_id),
            "deployment_id": str(payload.deployment_id),
        }
    finally:
        try:
            await storage.delete()
        except Exception:
            logger.warning(
                "Failed to delete transient App source for job %s",
                context.job_id,
                exc_info=True,
            )
        if not activated:
            try:
                await builder.delete_deployment(
                    payload.application_id, payload.deployment_id
                )
            except Exception:
                logger.warning(
                    "Failed to clean incomplete App deployment %s",
                    payload.deployment_id,
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
        min_memory_headroom_mb=512,
    ),
)
