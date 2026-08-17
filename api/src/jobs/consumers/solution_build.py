"""Run canonical Solution app builds on the existing Bifrost Worker."""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from src.config import get_settings
from src.jobs.rabbitmq import BaseConsumer
from src.models.contracts.solution_builder import BuildJobStatusUpdate
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_build_jobs import SolutionBuildJob
from src.services.builder.build_completion import (
    BuildCompletionConflict,
    complete_build_attempt,
)
from src.services.builder.fs_tools import WorkspaceViolation
from src.services.builder.local_app_build import (
    LocalBuildCancelled,
    LocalBuildError,
    LocalBuildTimeout,
    materialize_build_input,
    run_local_app_build,
)
from src.services.builder.staged_artifacts import (
    BuildOutputTooLarge,
    StagedBuildArtifactStorage,
)

logger = logging.getLogger(__name__)

QUEUE_NAME = "solution-builds"


@dataclass(frozen=True)
class _BuildRuntime:
    job_id: UUID
    dispatch_attempt: int
    app_id: UUID
    source_sha256: str


class SolutionBuildConsumer(BaseConsumer):
    """Consume bounded npm/Vite builds without adding another container."""

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(
            queue_name=QUEUE_NAME,
            prefetch_count=settings.builder_max_concurrent_builds,
        )
        from src.core.database import get_session_factory

        self._session_factory = get_session_factory()
        self._settings = settings

    async def process_message(self, body: dict[str, Any]) -> None:
        job_id = UUID(str(body["job_id"]))
        dispatch_attempt = int(body["dispatch_attempt"])
        if dispatch_attempt < 1:
            raise ValueError("dispatch_attempt must be positive")

        runtime = await self._load_runtime(job_id, dispatch_attempt)
        if runtime is None:
            logger.info(
                "Ignoring stale Solution build dispatch",
                extra={"platform_job_id": str(job_id), "attempt": dispatch_attempt},
            )
            return

        storage = StagedBuildArtifactStorage(job_id)
        with tempfile.TemporaryDirectory(prefix="bifrost-app-build-") as name:
            scratch = Path(name)
            os.chmod(scratch, 0o700)
            workspace = scratch / "workspace"
            workspace.mkdir(mode=0o700)
            try:
                await self._report(runtime, "Preparing application source", 0, None)
                await materialize_build_input(
                    storage,
                    workspace,
                    expected_sha256=runtime.source_sha256,
                )
                manifest, log_excerpt = await run_local_app_build(
                    workspace=workspace,
                    storage=storage,
                    app_id=runtime.app_id,
                    timeout_seconds=self._settings.builder_build_timeout_s,
                    log_limit_bytes=self._settings.builder_log_limit_bytes,
                    output_limit_bytes=self._settings.builder_output_limit_bytes,
                    report=lambda phase, current, total: self._report(
                        runtime,
                        phase,
                        current,
                        total,
                    ),
                    is_cancelled=lambda: self._is_cancelled(runtime),
                )
            except LocalBuildCancelled as exc:
                await self._complete(
                    runtime,
                    BuildJobStatusUpdate(
                        status="cancelled",
                        log_excerpt=exc.log_excerpt,
                    ),
                )
                return
            except LocalBuildTimeout as exc:
                await self._complete(
                    runtime,
                    BuildJobStatusUpdate(
                        status="timeout",
                        error=str(exc),
                        log_excerpt=exc.log_excerpt,
                    ),
                )
                return
            except (LocalBuildError, WorkspaceViolation, BuildOutputTooLarge) as exc:
                await self._complete(
                    runtime,
                    BuildJobStatusUpdate(
                        status="failed",
                        error=str(exc),
                        log_excerpt=getattr(exc, "log_excerpt", ""),
                    ),
                )
                return
            except Exception:
                logger.exception(
                    "Local Solution build failed unexpectedly",
                    extra={"platform_job_id": str(job_id)},
                )
                await self._complete(
                    runtime,
                    BuildJobStatusUpdate(
                        status="failed",
                        error="Build infrastructure failed unexpectedly; see server logs.",
                        retryable=True,
                    ),
                )
                return

        await self._complete(
            runtime,
            BuildJobStatusUpdate(
                status="succeeded",
                output_manifest=manifest,
                log_excerpt=log_excerpt,
            ),
        )

    async def _load_runtime(
        self,
        job_id: UUID,
        dispatch_attempt: int,
    ) -> _BuildRuntime | None:
        async with self._session_factory() as db:
            platform_job = await db.get(PlatformJob, job_id)
            build_job = await db.get(SolutionBuildJob, job_id)
            if (
                platform_job is None
                or platform_job.job_type != "solution.build"
                or platform_job.attempt != dispatch_attempt
                or platform_job.status not in {"running", "waiting"}
                or build_job is None
                or build_job.status != "running"
                or build_job.app_id is None
            ):
                return None
            return _BuildRuntime(
                job_id=job_id,
                dispatch_attempt=dispatch_attempt,
                app_id=build_job.app_id,
                source_sha256=build_job.source_sha256,
            )

    async def _report(
        self,
        runtime: _BuildRuntime,
        phase: str,
        current: int,
        total: int | None,
    ) -> None:
        from src.services.platform_jobs import update_external_platform_job_progress

        percent = None if total is None else (100.0 if total == 0 else current / total * 100)
        updated = await update_external_platform_job_progress(
            runtime.job_id,
            runtime.dispatch_attempt,
            phase=phase,
            current=current,
            total=total,
            percent=percent,
        )
        if not updated:
            raise LocalBuildCancelled("Build was cancelled")
        async with self._session_factory() as db:
            build_job = await db.get(SolutionBuildJob, runtime.job_id)
            if build_job is not None and build_job.status == "running":
                build_job.last_progress_at = datetime.now(timezone.utc)
                await db.commit()

    async def _is_cancelled(self, runtime: _BuildRuntime) -> bool:
        async with self._session_factory() as db:
            platform_job = await db.get(PlatformJob, runtime.job_id)
            return platform_job is None or platform_job.status in {
                "cancel_requested",
                "cancelled",
                "failed",
                "succeeded",
            }

    async def _complete(
        self,
        runtime: _BuildRuntime,
        update: BuildJobStatusUpdate,
    ) -> None:
        async with self._session_factory() as db:
            try:
                await complete_build_attempt(
                    db,
                    job_id=runtime.job_id,
                    dispatch_attempt=runtime.dispatch_attempt,
                    update=update,
                )
            except BuildCompletionConflict:
                logger.info(
                    "Solution build completion was fenced out",
                    extra={
                        "platform_job_id": str(runtime.job_id),
                        "attempt": runtime.dispatch_attempt,
                    },
                )


__all__ = ["QUEUE_NAME", "SolutionBuildConsumer"]
