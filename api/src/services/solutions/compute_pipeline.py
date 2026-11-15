"""Prepare Solution source and App receipts without holding a DB transaction."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass
from uuid import UUID, uuid5
from pathlib import Path

from src.core.database import get_db_context
from src.jobs.platform.base import PlatformJobContext, PlatformJobFailure
from src.models.orm.solutions import Solution
from src.services.solutions.archive_compute import VerifiedSolutionArchive, inspect_solution_archive
from src.services.solutions.deploy import (
    PreparedSolutionAppBuilds,
    SolutionBundle,
    SolutionDeployer,
    SolutionDeployConflict,
    compile_solution_app_builds,
)
from src.services.solutions.export import build_workspace_zip
from src.services.solutions.write_lock import solution_write_lock
from src.services.solutions.zip_install import (
    InactiveInstallExists,
    _build_bundle,
    find_install,
)


@dataclass(frozen=True)
class PreparedSolutionDeployment:
    archive: VerifiedSolutionArchive
    bundle: SolutionBundle
    app_builds: PreparedSolutionAppBuilds
    source_artifact: bytes


@asynccontextmanager
async def prepare_solution_deployment(
    context: PlatformJobContext,
    zip_path: Path,
    *,
    solution_id: UUID | None,
    organization_id: UUID | None = None,
    reactivate: bool = False,
    allow_connected_install: bool = False,
    lock_already_held: bool = False,
):
    """Keep one writer across a short snapshot, physical compute and fresh apply.

    New installs use a job-derived UUID so a retried attempt keeps its resource
    identity without committing an empty install before compilation succeeds.
    The caller owns fresh-session authorization and publication inside the yield.
    """
    if context.file_compute is None or context.app_build_compute is None:
        raise PlatformJobFailure("compute_unavailable", "Solution deployment requires its worker compute stages.")
    async with inspect_solution_archive(zip_path, context.file_compute) as archive:
        preview = archive.preview
        if not preview.slug or not preview.name:
            raise ValueError("zip is not a Solution workspace (missing bifrost.solution.yaml slug/name)")
        if solution_id is None:
            async with get_db_context() as db:
                existing = await find_install(db, slug=preview.slug, organization_id=organization_id)
                target_id = existing.id if existing is not None else uuid5(context.job_id, "solution.install")
        else:
            target_id = solution_id
        async with nullcontext() if lock_already_held else solution_write_lock(target_id):
            async with get_db_context() as db:
                if solution_id is None:
                    solution = await find_install(db, slug=preview.slug, organization_id=organization_id)
                    if solution is not None and solution.id != target_id:
                        raise SolutionDeployConflict("Solution install target changed while preparing deployment.")
                    if solution is None:
                        solution = Solution(
                            id=target_id, slug=preview.slug, name=preview.name,
                            organization_id=organization_id, allow_outbound_access=False,
                            git_connected=False, status="active",
                        )
                    elif solution.status == "inactive" and not reactivate:
                        raise InactiveInstallExists(solution.id, solution.slug)
                else:
                    solution = await db.get(Solution, target_id)
                    if solution is None:
                        raise SolutionDeployConflict("Solution not found")
                if solution.git_connected and not allow_connected_install:
                    raise SolutionDeployConflict("This install is git-connected; only auto-pull may deploy it.")
            # Bundle hydration and source packing can be large; neither owns a DB
            # connection, and all source came from the API-verified snapshot.
            bundle = await asyncio.to_thread(_build_bundle, solution, preview, archive.workspace)
            async with get_db_context() as db:
                plan = await SolutionDeployer(db).prepare_app_builds(bundle)
            await context.report("Compiling Solution applications", percent=15)
            app_builds = await compile_solution_app_builds(plan, context.app_build_compute)
            source_artifact = await asyncio.to_thread(build_workspace_zip, bundle)
            yield PreparedSolutionDeployment(archive, bundle, app_builds, source_artifact)


class DeferredSolutionDeployObservations:
    """Do not open progress transactions while publication holds the job lock."""

    def __init__(self):
        self.progress: list[tuple[str, float]] = []
        self.timings: list[tuple[str, int]] = []

    async def record_progress(self, phase: str, percent: float) -> None:
        self.progress.append((phase, percent))

    async def record_timing(self, category: str, elapsed_ms: int) -> None:
        self.timings.append((category, elapsed_ms))

    async def flush(self, progress_callback, timing_callback) -> None:
        if progress_callback is not None:
            for phase, percent in self.progress:
                await progress_callback(phase, percent)
        if timing_callback is not None:
            for category, elapsed_ms in self.timings:
                await timing_callback(category, elapsed_ms)
