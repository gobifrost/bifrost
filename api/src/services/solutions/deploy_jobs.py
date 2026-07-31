"""Durable orchestration and execution for Solution deploy jobs.

The API validates input, stages it in object storage, persists an encrypted job
document, and publishes only the job id. Workers claim rows with a lease and
execute the same proven deploy/install functions used by the old in-process
background tasks. All terminal transitions are claim-token guarded, making
duplicate RabbitMQ delivery safe.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost.solution_jobs import (
    DEPLOY_JOB_TIMEOUT_ERROR,
    DEPLOY_JOB_TIMEOUT_SECONDS,
)
from src.core.database import get_db_context
from src.core.security import decrypt_secret, encrypt_secret
from src.jobs.rabbitmq import publish_message
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

DEPLOY_QUEUE = "solution-deploys"
TERMINAL_STATUSES = frozenset({"succeeded", "failed"})
JOB_KINDS = frozenset({"deploy", "install", "install_from_repo"})
DeployJobKind = Literal["deploy", "install", "install_from_repo"]
CLAIM_LEASE = timedelta(seconds=DEPLOY_JOB_TIMEOUT_SECONDS + 60)
_ACTIVE_CLAIM_POLL_SECONDS = 2.0


@dataclass(frozen=True)
class ClaimedDeployJob:
    id: UUID
    install_id: UUID | None
    kind: str
    input_key: str | None
    input_sha256: str | None
    encrypted_options: str | None
    claim_token: UUID


async def create_staged_deploy_job(
    db: AsyncSession,
    *,
    kind: DeployJobKind,
    install_id: UUID | None,
    options: dict[str, Any],
    input_path: Path | None = None,
    input_bytes: bytes | None = None,
) -> SolutionDeployJob:
    """Stage validated input, persist the job, then durably publish its id."""
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

    # Publishing happens only after both S3 and PostgreSQL are durable. If the
    # broker is unavailable, the queued row remains recoverable by the worker's
    # startup/periodic recovery pass; publish_message already retries transient
    # failures.
    await publish_message(DEPLOY_QUEUE, {"job_id": str(job.id)})
    job.published_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(job)
    return job


async def recover_deploy_jobs(
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> list[UUID]:
    """Return all queued work and requeue only expired running claims."""
    resolved_now = now or datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(SolutionDeployJob)
            .where(
                or_(
                    and_(
                        SolutionDeployJob.status == "queued",
                        SolutionDeployJob.published_at.is_(None),
                    ),
                    and_(
                        SolutionDeployJob.status == "running",
                        or_(
                            SolutionDeployJob.lease_expires_at.is_(None),
                            SolutionDeployJob.lease_expires_at <= resolved_now,
                        ),
                    ),
                )
            )
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    ids: list[UUID] = []
    for job in rows:
        if job.status == "running":
            job.status = "queued"
            job.claim_token = None
            job.claimed_at = None
            job.lease_expires_at = None
            job.published_at = None
            job.result = {"phase": "requeued after an interrupted worker"}
        ids.append(job.id)
    return ids


async def mark_deploy_job_published(job_id: UUID) -> None:
    """Record a confirmed durable publish unless the job was already claimed."""
    async with get_db_context() as db:
        job = (
            await db.execute(
                select(SolutionDeployJob)
                .where(
                    SolutionDeployJob.id == job_id,
                    SolutionDeployJob.status == "queued",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is not None:
            job.published_at = datetime.now(timezone.utc)


async def _claim_deploy_job(
    job_id: UUID,
    *,
    now: datetime | None = None,
) -> ClaimedDeployJob | None:
    resolved_now = now or datetime.now(timezone.utc)
    async with get_db_context() as db:
        job = (
            await db.execute(
                select(SolutionDeployJob)
                .where(SolutionDeployJob.id == job_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None or job.status in TERMINAL_STATUSES:
            return None
        if (
            job.status == "running"
            and job.lease_expires_at is not None
            and job.lease_expires_at > resolved_now
        ):
            return None

        token = uuid4()
        job.status = "running"
        job.claim_token = token
        job.claimed_at = resolved_now
        job.started_at = resolved_now
        job.lease_expires_at = resolved_now + CLAIM_LEASE
        job.attempt_count += 1
        job.error = None
        job.result = {"phase": "loading staged input"}
        return ClaimedDeployJob(
            id=job.id,
            install_id=job.install_id,
            kind=job.kind,
            input_key=job.input_key,
            input_sha256=job.input_sha256,
            encrypted_options=job.encrypted_options,
            claim_token=token,
        )


async def _job_state(
    job_id: UUID,
) -> tuple[str | None, datetime | None]:
    async with get_db_context() as db:
        job = await db.get(SolutionDeployJob, job_id)
        if job is None:
            return None, None
        return job.status, job.lease_expires_at


async def _wait_for_claim(job_id: UUID) -> ClaimedDeployJob | None:
    """Wait out another live claim instead of acknowledging its duplicate."""
    while True:
        claimed = await _claim_deploy_job(job_id)
        if claimed is not None:
            return claimed
        status, lease_expires_at = await _job_state(job_id)
        if status is None or status in TERMINAL_STATUSES:
            return None
        now = datetime.now(timezone.utc)
        if lease_expires_at is None or lease_expires_at <= now:
            continue
        await asyncio.sleep(
            min(
                _ACTIVE_CLAIM_POLL_SECONDS,
                max(0.05, (lease_expires_at - now).total_seconds()),
            )
        )


async def _update_claimed_job(
    job_id: UUID,
    claim_token: UUID,
    *,
    status: str | None = None,
    error: str | None = None,
    result: dict[str, Any] | None = None,
    install_id: UUID | None = None,
) -> bool:
    async with get_db_context() as db:
        job = (
            await db.execute(
                select(SolutionDeployJob)
                .where(
                    SolutionDeployJob.id == job_id,
                    SolutionDeployJob.status == "running",
                    SolutionDeployJob.claim_token == claim_token,
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
        if status in TERMINAL_STATUSES:
            job.lease_expires_at = None
        return True


async def _set_phase(claimed: ClaimedDeployJob, phase: str) -> None:
    await _update_claimed_job(
        claimed.id,
        claimed.claim_token,
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


async def execute_deploy_job(job_id: UUID) -> None:
    """Claim and execute one job id; safe under duplicate delivery."""
    claimed = await _wait_for_claim(job_id)
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
            claimed.claim_token,
            status="succeeded",
            result=result,
            install_id=install_id,
        )
    except TimeoutError:
        if claimed.kind == "install_from_repo":
            await _delete_orphan_install(claimed)
        terminal_written = await _update_claimed_job(
            claimed.id,
            claimed.claim_token,
            status="failed",
            error=DEPLOY_JOB_TIMEOUT_ERROR,
        )
    except InactiveInstallExists as exc:
        terminal_written = await _update_claimed_job(
            claimed.id,
            claimed.claim_token,
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
            claimed.claim_token,
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
            claimed.claim_token,
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
            claimed.claim_token,
            status="failed",
            error=error,
        )
    except Exception:  # noqa: BLE001 - terminal failure must stay observable
        logger.exception("Solution deploy job %s failed", job_id)
        if claimed.kind == "install_from_repo":
            await _delete_orphan_install(claimed)
        terminal_written = await _update_claimed_job(
            claimed.id,
            claimed.claim_token,
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
