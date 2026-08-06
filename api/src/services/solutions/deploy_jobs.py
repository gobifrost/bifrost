"""Projection and execution helpers for central Solution deploy jobs.

The API validates input, stages it in object storage, persists an encrypted job
document, and creates a scheduler-owned platform job with the same UUID. The
legacy SolutionDeployJob row remains only as the existing polling projection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost.solution_jobs import (
    DEPLOY_JOB_TIMEOUT_ERROR,
    DEPLOY_JOB_TIMEOUT_SECONDS,
)
from src.core.database import get_db_context
from src.core.security import decrypt_secret, encrypt_secret
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solutions import Solution
from src.services.solutions.deploy import (
    SolutionDeployConflict,
    SolutionDowngradeBlocked,
    SolutionFinalizeIncomplete,
    SolutionWorkflowNameMismatch,
)
from src.services.solutions.deploy_job_storage import (
    DeployJobInputIntegrityError,
    SolutionDeployJobStorage,
)
from src.services.solutions.write_lock import (
    SolutionWriteLockHeld,
    solution_write_lock,
)
from src.services.solutions.zip_install import (
    BadExportPassword,
    ContentCollision,
    GitConnectedInstallError,
    InactiveInstallExists,
    UnmetDependency,
    deploy_zip_to_solution_path,
    install_zip_path,
)

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset({"succeeded", "failed"})
JOB_KINDS = frozenset({"deploy", "install", "install_from_repo"})
DeployJobKind = Literal["deploy", "install", "install_from_repo"]


@dataclass(frozen=True)
class ClaimedDeployJob:
    id: UUID
    install_id: UUID | None
    kind: str
    input_key: str | None
    input_sha256: str | None
    encrypted_options: str | None
    lease_token: UUID


async def create_staged_deploy_job(
    db: AsyncSession,
    *,
    kind: DeployJobKind,
    install_id: UUID | None,
    organization_id: UUID | None,
    requested_by_user_id: UUID | str,
    requested_by_email: str,
    requested_by_name: str,
    options: dict[str, Any],
    input_path: Path | None = None,
    input_bytes: bytes | None = None,
) -> SolutionDeployJob:
    """Stage input and atomically create its central job plus polling projection."""
    if kind not in JOB_KINDS:
        raise ValueError(f"unsupported Solution deploy job kind: {kind}")
    if (input_path is None) == (input_bytes is None):
        raise ValueError("exactly one of input_path or input_bytes is required")

    job_id = uuid4()
    storage = SolutionDeployJobStorage(job_id)
    if input_path is not None:
        digest, _ = await storage.write_path(input_path)
    else:
        assert input_bytes is not None
        digest, _ = await storage.write_bytes(input_bytes)

    job = SolutionDeployJob(
        id=job_id,
        install_id=install_id,
        status="queued",
        kind=kind,
        encrypted_options=encrypt_secret(
            json.dumps(options, separators=(",", ":"), sort_keys=True)
        ),
        input_key=storage.key,
        input_sha256=digest,
    )
    db.add(job)
    try:
        from src.jobs.platform.solution_deploy import (
            SOLUTION_DEPLOY_DEFINITION,
            SolutionDeployPayload,
        )
        from src.services.platform_jobs import enqueue_platform_job

        platform_job, _ = await enqueue_platform_job(
            db,
            SOLUTION_DEPLOY_DEFINITION,
            SolutionDeployPayload(
                deploy_job_id=job_id,
                kind=kind,
                install_id=install_id,
                input_sha256=digest,
                options=options,
            ),
            dedupe_key=str(job_id),
            resource_lock_key=f"solution:{install_id}" if install_id else None,
            priority=500,
            organization_id=organization_id,
            requested_by_user_id=requested_by_user_id,
            requested_by_email=requested_by_email,
            requested_by_name=requested_by_name,
            resource_type="solution_deploy",
            resource_id=str(job_id),
            title=f"Solution {kind.replace('_', ' ')}",
            action_url=f"/solutions/{install_id}" if install_id else "/solutions",
            job_id=job_id,
        )
        await db.commit()
        await db.refresh(job)
    except Exception:
        try:
            await storage.delete()
        except Exception:  # noqa: BLE001 - preserve the database failure
            logger.warning(
                "Failed to clean staged input for uncommitted deploy job %s",
                job_id,
                exc_info=True,
            )
        raise

    from src.services.platform_jobs import publish_platform_job_update

    await publish_platform_job_update(platform_job)
    return job


async def _claim_deploy_job(
    job_id: UUID,
    *,
    lease_token: UUID,
) -> ClaimedDeployJob | None:
    from src.models.orm.platform_jobs import PlatformJob

    async with get_db_context() as db:
        platform_job = await db.get(PlatformJob, job_id)
        if (
            platform_job is None
            or platform_job.status not in {"running", "cancel_requested"}
            or platform_job.lease_token != lease_token
        ):
            return None
        job = (
            await db.execute(
                select(SolutionDeployJob)
                .where(SolutionDeployJob.id == job_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None or job.status in TERMINAL_STATUSES:
            return None

        job.status = "running"
        job.error = None
        job.result = {"phase": "loading staged input"}
        return ClaimedDeployJob(
            id=job.id,
            install_id=job.install_id,
            kind=job.kind,
            input_key=job.input_key,
            input_sha256=job.input_sha256,
            encrypted_options=job.encrypted_options,
            lease_token=lease_token,
        )


async def _update_claimed_job(
    job_id: UUID,
    lease_token: UUID,
    *,
    status: str | None = None,
    error: str | None = None,
    result: dict[str, Any] | None = None,
    install_id: UUID | None = None,
) -> bool:
    from src.models.orm.platform_jobs import PlatformJob

    async with get_db_context() as db:
        platform_job = await db.get(PlatformJob, job_id)
        if (
            platform_job is None
            or platform_job.status not in {"running", "cancel_requested"}
            or platform_job.lease_token != lease_token
        ):
            return False
        job = (
            await db.execute(
                select(SolutionDeployJob)
                .where(
                    SolutionDeployJob.id == job_id,
                    SolutionDeployJob.status == "running",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            return False
        if status is not None:
            job.status = status
        job.error = error
        job.result = result
        if install_id is not None:
            job.install_id = install_id
        return True


async def _set_phase(claimed: ClaimedDeployJob, phase: str) -> None:
    await _update_claimed_job(
        claimed.id,
        claimed.lease_token,
        result={"phase": phase},
    )


def _decrypt_options(claimed: ClaimedDeployJob) -> dict[str, Any]:
    if claimed.encrypted_options is None:
        return {}
    value = json.loads(decrypt_secret(claimed.encrypted_options))
    if not isinstance(value, dict):
        raise ValueError("deploy job options are not an object")
    return value


def _deploy_counts(solution_id: UUID, result: Any) -> dict[str, Any]:
    return {
        "solution_id": str(solution_id),
        "workflows_upserted": result.workflows_upserted,
        "workflows_deleted": result.workflows_deleted,
        "tables_upserted": result.tables_upserted,
        "tables_deleted": result.tables_deleted,
        "apps_upserted": result.apps_upserted,
        "apps_deleted": result.apps_deleted,
        "forms_upserted": result.forms_upserted,
        "forms_deleted": result.forms_deleted,
        "agents_upserted": result.agents_upserted,
        "agents_deleted": result.agents_deleted,
        "claims_upserted": result.claims_upserted,
        "claims_deleted": result.claims_deleted,
        "integrations_shell_created": result.integrations_shell_created,
        "roles_created": list(result.roles_created),
        "roles_unresolved": list(result.roles_unresolved),
        "build_job_ids": [str(job_id) for job_id in result.build_job_ids],
    }


async def _sync_builder_turn(
    claimed: ClaimedDeployJob,
    *,
    running: bool = False,
) -> None:
    """Mirror durable deploy state onto the builder turn/project pointers."""
    if claimed.kind != "deploy":
        return
    try:
        options = _decrypt_options(claimed)
        raw_turn_id = options.get("builder_turn_id")
        raw_revision_id = options.get("source_revision_id")
        if not raw_turn_id or not raw_revision_id or claimed.install_id is None:
            return
        turn_id = UUID(str(raw_turn_id))
        revision_id = UUID(str(raw_revision_id))
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return

    from src.models.orm.solution_builder import (
        SolutionBuilderProject,
        SolutionBuilderTurn,
        SolutionSourceRevision,
    )

    async with get_db_context() as db:
        turn = await db.get(SolutionBuilderTurn, turn_id)
        project = await db.get(SolutionBuilderProject, claimed.install_id)
        revision = await db.get(SolutionSourceRevision, revision_id)
        if (
            turn is None
            or project is None
            or revision is None
            or revision.solution_id != claimed.install_id
        ):
            return
        if running:
            turn.status = "running"
            turn.error = None
            return

        job = await db.get(SolutionDeployJob, claimed.id)
        if job is None or job.status not in TERMINAL_STATUSES:
            return
        turn.completed_at = datetime.now(timezone.utc)
        if job.status == "succeeded":
            turn.status = "succeeded"
            turn.error = None
            build_ids = (job.result or {}).get("build_job_ids") or []
            turn.build_job_id = UUID(str(build_ids[0])) if build_ids else None
            project.deployed_revision_id = revision_id
        else:
            turn.status = "failed"
            turn.error = job.error or "Deploy failed"


async def _execute_existing_deploy(
    claimed: ClaimedDeployJob,
    zip_path: Path,
    options: dict[str, Any],
) -> dict[str, Any]:
    if claimed.install_id is None:
        raise SolutionDeployConflict("Solution not found")
    solution_id = claimed.install_id
    async with get_db_context() as db:
        async with solution_write_lock(solution_id):
            solution = await db.get(Solution, solution_id)
            if solution is None:
                raise SolutionDeployConflict("Solution not found")
            await _set_phase(claimed, "parsing workspace zip and building app dist")
            result = await deploy_zip_to_solution_path(
                db,
                solution,
                zip_path,
                force=bool(options.get("force", False)),
            )
            await db.commit()
            await _set_phase(claimed, "storing source artifact and runtime files")
            await result.finalize_s3()
            return _deploy_counts(solution_id, result)


async def _execute_zip_install(
    claimed: ClaimedDeployJob,
    zip_path: Path,
    options: dict[str, Any],
) -> tuple[dict[str, Any], UUID]:
    await _set_phase(claimed, "building and deploying bundle")
    organization_id = options.get("organization_id")
    org_uuid = UUID(organization_id) if organization_id else None
    async with get_db_context() as db:
        solution = await install_zip_path(
            db,
            zip_path,
            organization_id=org_uuid,
            config_values=options.get("config_values", {}),
            deployer_email=str(options["deployer_email"]),
            force=bool(options.get("force", False)),
            password=options.get("password"),
            replace_secrets=bool(options.get("replace_secrets", False)),
            replace_data=bool(options.get("replace_data", False)),
            reactivate=bool(options.get("reactivate", False)),
        )
        return {"solution_id": str(solution.id), "slug": solution.slug}, solution.id


async def _delete_orphan_install(claimed: ClaimedDeployJob) -> None:
    if claimed.install_id is None:
        return
    async with get_db_context() as db:
        job = await db.get(SolutionDeployJob, claimed.id)
        if job is not None:
            job.install_id = None
            await db.flush()
        solution = await db.get(Solution, claimed.install_id)
        if solution is not None:
            await db.delete(solution)


async def _execute_repo_install(
    claimed: ClaimedDeployJob,
    zip_path: Path,
) -> dict[str, Any]:
    if claimed.install_id is None:
        raise SolutionDeployConflict("Solution not found")
    solution_id = claimed.install_id
    await _set_phase(claimed, "deploying from staged repo checkout")
    async with get_db_context() as db:
        async with solution_write_lock(solution_id):
            solution = await db.get(Solution, solution_id)
            if solution is None:
                raise SolutionDeployConflict("Solution not found")
            result = await deploy_zip_to_solution_path(
                db,
                solution,
                zip_path,
                force=True,
            )
            await db.commit()
            await result.finalize_s3()
            return {"solution_id": str(solution_id), "slug": solution.slug}


async def _run_claimed_job(
    claimed: ClaimedDeployJob,
    zip_path: Path,
) -> tuple[dict[str, Any], UUID | None]:
    options = _decrypt_options(claimed)
    if claimed.kind == "deploy":
        return await _execute_existing_deploy(claimed, zip_path, options), None
    if claimed.kind == "install":
        return await _execute_zip_install(claimed, zip_path, options)
    if claimed.kind == "install_from_repo":
        return await _execute_repo_install(claimed, zip_path), None
    raise ValueError(f"unsupported Solution deploy job kind: {claimed.kind}")


async def execute_deploy_job(job_id: UUID, lease_token: UUID) -> None:
    """Execute one projection while its central scheduler lease is current."""
    claimed = await _claim_deploy_job(job_id, lease_token=lease_token)
    if claimed is None:
        return

    storage = SolutionDeployJobStorage(job_id)
    terminal_written = False
    await _sync_builder_turn(claimed, running=True)
    try:
        if claimed.input_key != storage.key or not claimed.input_sha256:
            raise DeployJobInputIntegrityError(
                f"deploy job {job_id} has no valid staged input"
            )
        with tempfile.TemporaryDirectory(prefix="bifrost-deploy-job-") as tmp:
            zip_path = Path(tmp) / "input.zip"
            await storage.copy_to_path(
                zip_path,
                expected_sha256=claimed.input_sha256,
            )
            result, install_id = await asyncio.wait_for(
                _run_claimed_job(claimed, zip_path),
                timeout=DEPLOY_JOB_TIMEOUT_SECONDS,
            )
        terminal_written = await _update_claimed_job(
            claimed.id,
            claimed.lease_token,
            status="succeeded",
            result=result,
            install_id=install_id,
        )
    except TimeoutError:
        if claimed.kind == "install_from_repo":
            await _delete_orphan_install(claimed)
        terminal_written = await _update_claimed_job(
            claimed.id,
            claimed.lease_token,
            status="failed",
            error=DEPLOY_JOB_TIMEOUT_ERROR,
        )
    except InactiveInstallExists as exc:
        terminal_written = await _update_claimed_job(
            claimed.id,
            claimed.lease_token,
            status="failed",
            error=str(exc),
            result={
                "reason": "inactive_install_exists",
                "solution_id": str(exc.solution_id),
                "slug": exc.slug,
            },
        )
    except SolutionWriteLockHeld:
        if claimed.kind == "install_from_repo":
            await _delete_orphan_install(claimed)
        terminal_written = await _update_claimed_job(
            claimed.id,
            claimed.lease_token,
            status="failed",
            error="A deploy is already in progress for this install; retry shortly.",
        )
    except SolutionFinalizeIncomplete:
        if claimed.kind == "install_from_repo":
            await _delete_orphan_install(claimed)
        storage_error = (
            "Deploy committed but storage was unavailable after retries. "
            "Re-run the deploy to complete it (it is idempotent)."
        )
        if claimed.kind == "install_from_repo":
            storage_error = f"Install cloned but deploy failed: {storage_error}"
        terminal_written = await _update_claimed_job(
            claimed.id,
            claimed.lease_token,
            status="failed",
            error=storage_error,
        )
    except (
        UnmetDependency,
        BadExportPassword,
        ContentCollision,
        GitConnectedInstallError,
        SolutionDowngradeBlocked,
        SolutionDeployConflict,
        SolutionWorkflowNameMismatch,
        DeployJobInputIntegrityError,
        FileNotFoundError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ) as exc:
        if claimed.kind == "install_from_repo":
            await _delete_orphan_install(claimed)
        error = str(exc) or "Install rejected."
        if claimed.kind == "install_from_repo":
            error = f"Install cloned but deploy failed: {error}"
        terminal_written = await _update_claimed_job(
            claimed.id,
            claimed.lease_token,
            status="failed",
            error=error,
        )
    except Exception:  # noqa: BLE001 - terminal failure must stay observable
        logger.exception("Solution deploy job %s failed", job_id)
        if claimed.kind == "install_from_repo":
            await _delete_orphan_install(claimed)
        terminal_written = await _update_claimed_job(
            claimed.id,
            claimed.lease_token,
            status="failed",
            error=(
                "Install cloned but deploy failed unexpectedly; see server logs."
                if claimed.kind == "install_from_repo"
                else "Deploy failed unexpectedly; see server logs."
            ),
        )
    finally:
        # Terminal status is durable before cleanup. A crash between those two
        # steps can leak one staged object, but can never make a retry read
        # missing input or double-execute a terminal job.
        if terminal_written:
            await _sync_builder_turn(claimed)
            try:
                await storage.delete()
            except Exception:  # noqa: BLE001 - best-effort terminal cleanup
                logger.warning(
                    "Failed to delete staged input for deploy job %s",
                    job_id,
                    exc_info=True,
                )
