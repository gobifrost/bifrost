"""REST endpoints for Solutions — installable surfaces (success-criteria §3).

An install is created here, then deployed via ``POST /{id}/deploy`` (the single
writer for a disconnected install). Deploy is a full replace by contract and is
non-interactive — it always applies the whole bundle.

Solution-management itself is an admin operation; the deployed *entities* are
what end users see (the Solution is invisible to them — criterion 16).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Body, File, HTTPException, Response, UploadFile, status
from fastapi import Form as FastapiForm
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload
from starlette.background import BackgroundTask

from bifrost.solution_jobs import (
    DEPLOY_JOB_TIMEOUT,
    DEPLOY_JOB_TIMEOUT_ERROR,
    DEPLOY_JOB_TIMEOUT_SECONDS,
)
from src.core.auth import Context, CurrentSuperuser
from src.models.contracts.solutions import (
    Solution as SolutionDTO,
    SolutionAccessUserSummary,
    SolutionCaptureCandidates,
    SolutionCaptureRequest,
    SolutionCaptureResponse,
    SolutionConfigStatus,
    SolutionCreate,
    SolutionDeleteSummary,
    SolutionDeletionSummary,
    SolutionEntityCounts,
    SolutionDependencyPreview,
    SolutionDependencyPreviewRequest,
    SolutionDeployEnqueued,
    SolutionDeployJobStatus,
    SolutionEntities,
    SolutionEntitySummary,
    SolutionFileSummary,
    SolutionExistingInstall,
    SolutionExportJobCreate,
    SolutionExportJobPublic,
    SolutionExportJobsList,
    SolutionInstallPreview,
    PullAckRequest,
    PullAckResponse,
    SolutionReadme,
    SolutionReadmeUpdate,
    SolutionRepoPreviewRequest,
    SolutionSetupStatus,
    SolutionsList,
    SolutionUpdate,
    SolutionUpgradeDiff,
)
from src.models.orm.agents import Agent, AgentRole
from src.models.orm.app_roles import AppRole
from src.models.orm.applications import Application
from src.models.orm.config import Config
from src.models.orm.custom_claims import CustomClaim
from src.models.orm.file_metadata import FileMetadata
from src.models.orm.forms import Form, FormRole
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solution_export_jobs import SolutionExportJob
from src.models.orm.solutions import Solution as SolutionORM
from src.models.orm.tables import Table
from src.models.orm.users import Role, User, UserRole
from src.models.orm.workflow_roles import WorkflowRole
from src.models.orm.workflows import Workflow
from src.services.solutions.deploy import (
    SolutionDeployConflict,
    SolutionDowngradeBlocked,
    SolutionFinalizeIncomplete,
    SolutionWorkflowNameMismatch,
)
from src.services.solutions.export_jobs import (
    create_export_job,
    list_export_jobs,
    public_job,
)

if TYPE_CHECKING:
    from src.services.solutions.zip_install import PreviewResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/solutions", tags=["Solutions"])

DEPLOY_JOB_ORPHAN_THRESHOLD = DEPLOY_JOB_TIMEOUT
UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024
_ZIP_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _cleanup_file(path: str | Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 - best-effort response cleanup
        logger.warning("Failed to remove temporary file %s", path)


def _safe_zip_filename(filename: str) -> str:
    stem = filename.removesuffix(".zip")
    safe_stem = _ZIP_FILENAME_SAFE_RE.sub("-", stem).strip(".-_")
    return f"{safe_stem or 'solution-export'}.zip"


async def _spool_upload_to_temp(file: UploadFile, *, prefix: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(prefix=prefix, suffix=".zip", delete=False)
    path = Path(tmp.name)
    try:
        with tmp:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                tmp.write(chunk)
    except Exception:
        _cleanup_file(path)
        raise
    return path


async def reconcile_orphaned_deploy_jobs(
    db: AsyncSession,
    *,
    older_than: timedelta = DEPLOY_JOB_ORPHAN_THRESHOLD,
    now: datetime | None = None,
) -> int:
    """Fail in-process deploy jobs that cannot survive an API restart."""
    resolved_now = now or datetime.now(timezone.utc)
    cutoff = resolved_now - older_than
    result = await db.execute(
        select(SolutionDeployJob).where(
            SolutionDeployJob.status.in_(("queued", "running")),
            SolutionDeployJob.updated_at < cutoff,
        )
    )
    jobs = list(result.scalars().all())
    error = (
        "Deploy did not finish because the API restarted before its in-process "
        "background task completed. Re-run the deploy; it is idempotent."
    )
    for job in jobs:
        job.status = "failed"
        job.error = error
        job.updated_at = resolved_now
    return len(jobs)


def expire_deploy_job_if_timed_out(
    job: SolutionDeployJob,
    *,
    now: datetime | None = None,
) -> bool:
    """Move an abandoned queued/running job to a terminal failed state."""
    if job.status not in ("queued", "running"):
        return False
    resolved_now = now or datetime.now(timezone.utc)
    created_at = job.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if created_at + DEPLOY_JOB_TIMEOUT > resolved_now:
        return False
    job.status = "failed"
    job.error = DEPLOY_JOB_TIMEOUT_ERROR
    job.result = None
    job.updated_at = resolved_now
    return True


@router.post("", response_model=SolutionDTO, status_code=status.HTTP_201_CREATED, summary="Create a Solution install (admin only)")
async def create_solution(body: SolutionCreate, ctx: Context, user: CurrentSuperuser) -> SolutionDTO:
    # Install kind is DERIVED from organization_id (unified --org standard) —
    # there is no `scope` input. HOME (organization_id absent) => the caller's
    # own org; explicit null => global (org NULL); a UUID => that org.
    if "organization_id" in body.model_fields_set:
        org_id: UUID | None = body.organization_id  # explicit (null == global)
    else:
        org_id = ctx.org_id  # HOME — the caller's own org
        if org_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="install requires an organization (caller has no org; "
                "pass organization_id, or null for a global install)",
            )

    row = SolutionORM(
        slug=body.slug,
        name=body.name,
        organization_id=org_id,
        global_repo_access=body.global_repo_access,
        git_connected=body.git_connected,
        git_repo_url=body.git_repo_url,
        repo_subpath=body.repo_subpath,
        git_ref=body.git_ref,
    )
    ctx.db.add(row)
    try:
        await ctx.db.flush()
    except IntegrityError as exc:
        await ctx.db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await ctx.db.commit()
    await ctx.db.refresh(row)
    return SolutionDTO.model_validate(row)


async def _count_by_solution(ctx: Context, model: type, solution_ids: list[UUID]) -> dict[UUID, int]:
    if not solution_ids:
        return {}
    rows = await ctx.db.execute(
        select(model.solution_id, func.count())  # type: ignore[attr-defined]
        .where(model.solution_id.in_(solution_ids))  # type: ignore[attr-defined]
        .group_by(model.solution_id)  # type: ignore[attr-defined]
    )
    return {solution_id: int(count) for solution_id, count in rows.all() if solution_id is not None}


async def _solution_entity_counts(
    ctx: Context, solution_ids: list[UUID]
) -> dict[UUID, SolutionEntityCounts]:
    counts = {
        solution_id: SolutionEntityCounts()
        for solution_id in solution_ids
    }
    for attr, model in (
        ("workflows", Workflow),
        ("apps", Application),
        ("forms", Form),
        ("agents", Agent),
        ("tables", Table),
        ("claims", CustomClaim),
        ("files", FileMetadata),
    ):
        by_solution = await _count_by_solution(ctx, model, solution_ids)
        for solution_id, count in by_solution.items():
            setattr(counts[solution_id], attr, count)
    return counts


@router.get("", response_model=SolutionsList, summary="List Solution installs (admin only)")
async def list_solutions(ctx: Context, user: CurrentSuperuser) -> SolutionsList:
    rows = (await ctx.db.execute(select(SolutionORM).order_by(SolutionORM.slug))).scalars().all()
    ids = [row.id for row in rows]
    counts = await _solution_entity_counts(ctx, ids)
    return SolutionsList(
        solutions=[
            SolutionDTO.model_validate(row).model_copy(
                update={"entity_counts": counts.get(row.id, SolutionEntityCounts())}
            )
            for row in rows
        ]
    )


@router.get("/{solution_id}", response_model=SolutionDTO, summary="Get a Solution install (admin only)")
async def get_solution(solution_id: UUID, ctx: Context, user: CurrentSuperuser) -> SolutionDTO:
    row = await ctx.db.get(SolutionORM, solution_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")
    return SolutionDTO.model_validate(row)


@router.get(
    "/{solution_id}/logo",
    summary="Get Solution icon",
    responses={
        200: {"content": {"image/png": {}, "image/jpeg": {}, "image/svg+xml": {}}},
        404: {"description": "No icon set"},
    },
)
async def get_solution_logo(
    solution_id: UUID, ctx: Context, user: CurrentSuperuser
) -> Response:
    """The solution-level icon (bifrost.solution.yaml ``logo:``), shown on the
    /solutions catalog cards. Bytes only — mirrors the application logo
    endpoint."""
    row = await ctx.db.get(SolutionORM, solution_id)
    if row is None or not row.logo_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Icon not set")
    return Response(
        content=row.logo_data,
        media_type=row.logo_content_type or "application/octet-stream",
    )


@router.get(
    "/{solution_id}/readme",
    response_model=SolutionReadme,
    summary="Get an install's README markdown (admin only)",
)
async def get_solution_readme(
    solution_id: UUID, ctx: Context, user: CurrentSuperuser
) -> SolutionReadme:
    """The install's long-form README markdown (repo-sourced on deploy, or
    edited directly via PUT). ``null`` when none is set."""
    row = await ctx.db.get(SolutionORM, solution_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")
    return SolutionReadme(readme=row.readme)


@router.put(
    "/{solution_id}/readme",
    response_model=SolutionReadme,
    summary="Set an install's README markdown (admin only)",
)
async def put_solution_readme(
    solution_id: UUID,
    body: SolutionReadmeUpdate,
    ctx: Context,
    user: CurrentSuperuser,
) -> SolutionReadme:
    """Full-replace the install's README markdown (``readme=null`` clears it).

    Normally README is repo-sourced (deploy reads README.md), but the UI can
    edit it directly here on a **disconnected** install. For a git-connected
    install the next auto-pull would clobber any hand edit, so editing the
    README here is refused (409) — the repo owns it. The UI hides the edit
    affordance for connected installs to match."""
    row = await ctx.db.get(SolutionORM, solution_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")
    if row.git_connected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This install is git-connected — its README is owned by the "
                "repository and is refreshed on every pull. Edit README.md in "
                "the repo, or disconnect the install to annotate it here."
            ),
        )
    row.readme = body.readme
    await ctx.db.commit()
    return SolutionReadme(readme=row.readme)


@router.get(
    "/{solution_id}/setup",
    response_model=SolutionSetupStatus,
    summary="Required-config setup status (admin only)",
)
async def solution_setup(
    solution_id: UUID, ctx: Context, user: CurrentSuperuser
) -> SolutionSetupStatus:
    """Return all config declarations for the install paired with whether each
    has a matching Config value in the install's org scope.  ``setup_complete``
    is True only when every required declaration is satisfied."""
    from src.services.solutions.setup_status import compute_setup_status

    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")
    return await compute_setup_status(ctx.db, sol)


@router.post(
    "/{solution_id}/export",
    summary="Download the install's workspace zip (admin only)",
    responses={
        200: {"content": {"application/zip": {}}},
        404: {"description": "Install not found, or it predates export support"},
    },
)
async def export_solution(
    solution_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
    mode: str = "shareable",
    include_values: bool | None = None,
    include_files: bool | None = None,
    include_data: bool = False,
    password: Annotated[str | None, Body(embed=True)] = None,
) -> Response:
    """Rebuild the install's workspace bundle LIVE from the entities it
    currently owns, so the export always reflects present ownership (not the
    last capture/deploy). Directly re-installable via the zip-install path.

    This is a POST (not GET) specifically so the full-backup ``password`` rides
    in the request BODY rather than the URL query string — a query-string secret
    leaks into access logs, proxies, and browser history. ``mode`` and the
    backup-content flags stay in the query (they are not sensitive).

    ``mode=shareable`` (default): portable export, no runtime values.
    ``mode=full``: backup export. ``include_values`` controls config/secret
    values, ``include_files`` controls Solution-owned file payloads, and
    ``include_data`` controls table row data. A password is required whenever a
    backup payload is requested.
    """
    if mode not in ("shareable", "full"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="mode must be 'shareable' or 'full'",
        )

    include_values_flag = mode == "full" if include_values is None else include_values
    # Back-compat: old callers only had include_data; keep that as "include
    # all large runtime data" unless include_files is sent explicitly.
    include_files_flag = include_data if include_files is None else include_files
    wants_backup_payload = include_values_flag or include_files_flag or include_data

    if mode == "shareable" and wants_backup_payload:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="backup content options require mode=full",
        )
    if mode == "full" and wants_backup_payload and not password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="backup export requires a password",
        )

    from src.services.solutions.capture import SolutionCaptureService
    from src.services.solutions.export import (
        add_live_content_to_workspace_zip_file,
        build_workspace_zip_for_export,
    )
    from src.services.solutions.source_artifact import SolutionSourceArtifactStorage

    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")

    artifact = SolutionSourceArtifactStorage(solution_id)
    filename = _safe_zip_filename(f"{sol.slug}-{sol.version or 'unversioned'}.zip")
    tmp = tempfile.NamedTemporaryFile(
        prefix="bifrost-solution-export-",
        suffix=".zip",
        delete=False,
    )
    out_path = Path(tmp.name)
    tmp.close()
    source_path: Path | None = None
    try:
        stored_source_path = tempfile.NamedTemporaryFile(
            prefix="bifrost-solution-source-",
            suffix=".zip",
            delete=False,
        )
        source_path = Path(stored_source_path.name)
        stored_source_path.close()
        has_stored_source = await artifact.copy_to_path(source_path)
        if has_stored_source and mode == "shareable":
            source_path.replace(out_path)
        else:
            bundle = await SolutionCaptureService(ctx.db).bundle_for(
                sol,
                include_imports=True,
                include_values=include_values_flag,
                include_data=include_data,
                include_files=include_files_flag,
            )
            if has_stored_source:
                await add_live_content_to_workspace_zip_file(
                    source_path,
                    bundle,
                    ctx.db,
                    out_path,
                    password=password or "",
                )
            else:
                await build_workspace_zip_for_export(
                    bundle,
                    ctx.db,
                    out_path,
                    password=password if wants_backup_payload else None,
                )
        _cleanup_file(source_path)
        # Commit the SolutionConnectionSchema rows that _connection_entries
        # upserts as a side-effect of a fresh _repo/ scan. Without this the only
        # commit is get_db()'s teardown, which FastAPI runs AFTER the FileResponse
        # body is streamed — so a caller that queries these rows right after the
        # response races the commit (flaky under load), and a deferred-commit
        # failure would silently drop the persisted declarations. Every other
        # mutating endpoint in this router commits explicitly; match that.
        await ctx.db.commit()
    except Exception:
        _cleanup_file(out_path)
        if source_path is not None:
            _cleanup_file(source_path)
        raise
    return FileResponse(
        out_path,
        media_type="application/zip",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        background=BackgroundTask(_cleanup_file, out_path),
    )


@router.post(
    "/{solution_id}/export-jobs",
    response_model=SolutionExportJobPublic,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a durable Solution backup export job (admin only)",
)
async def create_solution_export_job(
    solution_id: UUID,
    body: SolutionExportJobCreate,
    ctx: Context,
    user: CurrentSuperuser,
) -> SolutionExportJobPublic:
    """Create a scheduler-owned backup export job without building the zip."""
    from src.models.contracts.notifications import (
        NotificationCategory,
        NotificationCreate,
    )
    from src.services.notification_service import get_notification_service

    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")

    try:
        created = await create_export_job(ctx.db, sol, user.user_id, body.options)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    try:
        notification = await get_notification_service().create_notification(
            str(user.user_id),
            NotificationCreate(
                category=NotificationCategory.SYSTEM,
                title="Backup queued",
                description="Solution backup export is queued.",
                percent=0,
                metadata={
                    "solution_id": str(solution_id),
                    "job_id": str(created.id),
                    "action": "download_solution_export",
                    "action_label": "Download",
                },
            ),
        )
    except Exception as exc:  # noqa: BLE001 - Redis/notification outage
        await ctx.db.rollback()
        logger.exception("Failed to create Solution export notification")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to create export notification",
        ) from exc

    row = await ctx.db.get(SolutionExportJob, created.id)
    if row is None:
        await ctx.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Export job was not persisted",
        )
    row.notification_id = UUID(notification.id)
    await ctx.db.commit()
    await ctx.db.refresh(row)
    return public_job(row)


@router.get(
    "/{solution_id}/export-jobs",
    response_model=SolutionExportJobsList,
    summary="List durable Solution backup export jobs (admin only)",
)
async def get_solution_export_jobs(
    solution_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
) -> SolutionExportJobsList:
    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")
    return SolutionExportJobsList(jobs=await list_export_jobs(ctx.db, solution_id))


@router.get(
    "/export-jobs/{job_id}",
    response_model=SolutionExportJobPublic,
    summary="Get a durable Solution backup export job (admin only)",
)
async def get_solution_export_job(
    job_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
) -> SolutionExportJobPublic:
    row = await ctx.db.get(SolutionExportJob, job_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export job not found")
    return public_job(row)


@router.get(
    "/export-jobs/{job_id}/download",
    summary="Download a completed durable Solution backup export artifact (admin only)",
    responses={
        200: {"content": {"application/zip": {}}},
        404: {"description": "Export job not found"},
        409: {"description": "Export job is not downloadable"},
    },
)
async def download_solution_export_job(
    job_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
) -> StreamingResponse:
    from src.services.file_storage import FileStorageService

    row = await ctx.db.get(SolutionExportJob, job_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Export job not found")
    if (
        row.status != "completed"
        or not row.artifact_storage_key
        or public_job(row).download_url is None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Export job is not downloadable",
        )

    filename = _safe_zip_filename(row.artifact_filename or f"solution-export-{row.id}.zip")
    return StreamingResponse(
        FileStorageService(ctx.db).iter_raw_s3_chunks(row.artifact_storage_key),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{solution_id}/entities",
    response_model=SolutionEntities,
    summary="Get an install + everything it owns (admin only)",
)
async def get_solution_entities(
    solution_id: UUID, ctx: Context, user: CurrentSuperuser
) -> SolutionEntities:
    """One call for the detail UI: the install, all owned entities, and each
    config declaration paired with whether a value is set in the install's scope
    (plus the derived required-but-unset key list)."""
    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")

    from src.services.solution_files import enumerate_solution_files

    workflows = await _workflow_summaries(ctx, Workflow.solution_id == solution_id)
    apps = await _app_summaries(ctx, Application.solution_id == solution_id)
    forms = await _form_summaries(ctx, Form.solution_id == solution_id)
    agents = await _agent_summaries(ctx, Agent.solution_id == solution_id)
    claims = await _claim_summaries(ctx, CustomClaim.solution_id == solution_id)
    tables = await _table_summaries(ctx, Table.solution_id == solution_id)
    file_entries = await enumerate_solution_files(ctx.db, solution_id)
    files = [
        SolutionFileSummary(location=f.location, path=f.path, size=f.size)
        for f in file_entries
    ]

    decls = (
        await ctx.db.execute(
            select(SolutionConfigSchema)
            .where(SolutionConfigSchema.solution_id == solution_id)
            .order_by(SolutionConfigSchema.position)
        )
    ).scalars().all()

    # A declaration is "satisfied" when an instance Config row exists for the
    # install's org scope (NULL org for a global install) with the same key.
    if sol.organization_id is not None:
        set_keys_q = select(Config.key).where(Config.organization_id == sol.organization_id)
    else:
        set_keys_q = select(Config.key).where(Config.organization_id.is_(None))
    set_keys = set((await ctx.db.execute(set_keys_q)).scalars().all())

    configs = [
        SolutionConfigStatus(
            id=d.id,
            key=d.key,
            type=d.type,
            required=d.required,
            description=d.description,
            value_set=d.key in set_keys,
        )
        for d in decls
    ]
    required_unset = [d.key for d in decls if d.required and d.key not in set_keys]

    return SolutionEntities(
        solution=SolutionDTO.model_validate(sol),
        workflows=workflows,
        apps=apps,
        forms=forms,
        agents=agents,
        claims=claims,
        tables=tables,
        files=files,
        configs=configs,
        required_configs_unset=required_unset,
    )


def _enum_to_str(value: object) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _logo_data_url(data: bytes | None, content_type: str | None) -> str | None:
    """Encode a binary entity logo as a data URL for list-card rendering."""
    if not data:
        return None
    mime = content_type or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


async def _access_details_by_entity(
    ctx: Context,
    junction: type,
    fk_col: str,
    entity_ids: list[UUID],
) -> dict[UUID, tuple[list[UUID], list[str], list[SolutionAccessUserSummary]]]:
    """Return role names and derived users for role-gated solution entities."""
    if not entity_ids:
        return {}

    entity_col = getattr(junction, fk_col)
    rows = (
        await ctx.db.execute(
            select(
                entity_col,
                Role.id,
                Role.name,
                User.id,
                User.name,
                User.email,
            )
            .join(Role, getattr(junction, "role_id") == Role.id)
            .outerjoin(UserRole, UserRole.role_id == Role.id)
            .outerjoin(User, User.id == UserRole.user_id)
            .where(entity_col.in_(entity_ids))
            .order_by(Role.name, User.email)
        )
    ).all()

    role_ids_by_entity: dict[UUID, list[UUID]] = {}
    role_names_by_entity: dict[UUID, list[str]] = {}
    users_by_entity: dict[UUID, dict[UUID, SolutionAccessUserSummary]] = {}
    for entity_id, role_id, role_name, user_id, user_name, user_email in rows:
        role_ids_by_entity.setdefault(entity_id, [])
        role_names_by_entity.setdefault(entity_id, [])
        if role_id not in role_ids_by_entity[entity_id]:
            role_ids_by_entity[entity_id].append(role_id)
            role_names_by_entity[entity_id].append(role_name)
        if user_id is not None and user_email is not None:
            users_by_entity.setdefault(entity_id, {})[user_id] = SolutionAccessUserSummary(
                id=user_id,
                name=user_name,
                email=user_email,
            )

    return {
        entity_id: (
            role_ids_by_entity.get(entity_id, []),
            role_names_by_entity.get(entity_id, []),
            list(users_by_entity.get(entity_id, {}).values()),
        )
        for entity_id in entity_ids
    }


async def _workflow_summaries(ctx: Context, *where) -> list[SolutionEntitySummary]:
    rows = (await ctx.db.execute(select(Workflow).where(*where).order_by(Workflow.name))).scalars().all()
    access = await _access_details_by_entity(ctx, WorkflowRole, "workflow_id", [row.id for row in rows])
    summaries: list[SolutionEntitySummary] = []
    for row in rows:
        role_ids, role_names, access_users = access.get(row.id, ([], [], []))
        summaries.append(
            SolutionEntitySummary(
                id=row.id,
                name=row.name,
                description=row.description,
                organization_id=row.organization_id,
                path=row.path,
                function_name=row.function_name,
                type=row.type,
                category=row.category,
                access_level=row.access_level,
                is_active=row.is_active,
                created_at=row.created_at,
                role_ids=role_ids,
                role_names=role_names,
                access_users=access_users,
            )
        )
    return summaries


async def _app_summaries(ctx: Context, *where) -> list[SolutionEntitySummary]:
    rows = (await ctx.db.execute(select(Application).where(*where).order_by(Application.name))).scalars().all()
    access = await _access_details_by_entity(ctx, AppRole, "app_id", [row.id for row in rows])
    summaries: list[SolutionEntitySummary] = []
    for row in rows:
        role_ids, role_names, access_users = access.get(row.id, ([], [], []))
        summaries.append(
            SolutionEntitySummary(
                id=row.id,
                name=row.name,
                description=row.description,
                organization_id=row.organization_id,
                slug=row.slug,
                path=row.repo_path,
                access_level=row.access_level,
                app_model=row.app_model,
                logo=_logo_data_url(row.logo_data, row.logo_content_type),
                created_at=row.created_at,
                role_ids=role_ids,
                role_names=role_names,
                access_users=access_users,
            )
        )
    return summaries


async def _form_summaries(ctx: Context, *where) -> list[SolutionEntitySummary]:
    rows = (await ctx.db.execute(select(Form).where(*where).order_by(Form.name))).scalars().all()
    access = await _access_details_by_entity(ctx, FormRole, "form_id", [row.id for row in rows])
    summaries: list[SolutionEntitySummary] = []
    for row in rows:
        role_ids, role_names, access_users = access.get(row.id, ([], [], []))
        summaries.append(
            SolutionEntitySummary(
                id=row.id,
                name=row.name,
                description=row.description,
                organization_id=row.organization_id,
                access_level=_enum_to_str(row.access_level),
                is_active=row.is_active,
                path=row.workflow_path,
                function_name=row.workflow_function_name,
                created_at=row.created_at,
                role_ids=role_ids,
                role_names=role_names,
                access_users=access_users,
            )
        )
    return summaries


async def _agent_summaries(ctx: Context, *where) -> list[SolutionEntitySummary]:
    rows = (await ctx.db.execute(select(Agent).where(*where).order_by(Agent.name))).scalars().all()
    access = await _access_details_by_entity(ctx, AgentRole, "agent_id", [row.id for row in rows])
    summaries: list[SolutionEntitySummary] = []
    for row in rows:
        role_ids, role_names, access_users = access.get(row.id, ([], [], []))
        summaries.append(
            SolutionEntitySummary(
                id=row.id,
                name=row.name,
                description=row.description,
                organization_id=row.organization_id,
                access_level=_enum_to_str(row.access_level),
                is_active=row.is_active,
                created_at=row.created_at,
                role_ids=role_ids,
                role_names=role_names,
                access_users=access_users,
            )
        )
    return summaries


async def _table_summaries(ctx: Context, *where) -> list[SolutionEntitySummary]:
    rows = (await ctx.db.execute(select(Table).where(*where).order_by(Table.name))).scalars().all()
    return [
        SolutionEntitySummary(
            id=row.id,
            name=row.name,
            description=row.description,
            organization_id=row.organization_id,
            created_at=row.created_at,
        )
        for row in rows
    ]


async def _claim_summaries(ctx: Context, *where) -> list[SolutionEntitySummary]:
    rows = (await ctx.db.execute(select(CustomClaim).where(*where).order_by(CustomClaim.name))).scalars().all()
    return [
        SolutionEntitySummary(
            id=row.id,
            name=row.name,
            description=row.description,
            organization_id=row.organization_id,
            type=row.type,
            source_table=row.query.get("table") if isinstance(row.query, dict) else None,
            select=row.query.get("select") if isinstance(row.query, dict) else None,
            created_at=row.created_at,
        )
        for row in rows
    ]


def _same_scope(model: type, org_id: UUID | None):
    if org_id is None:
        return model.organization_id.is_(None)  # type: ignore[attr-defined]
    return model.organization_id == org_id  # type: ignore[attr-defined]


@router.get(
    "/{solution_id}/capture/candidates",
    response_model=SolutionCaptureCandidates,
    summary="List loose same-scope entities capturable by an install (admin only)",
)
async def get_solution_capture_candidates(
    solution_id: UUID, ctx: Context, user: CurrentSuperuser
) -> SolutionCaptureCandidates:
    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")

    config_rows = (
        await ctx.db.execute(
            select(Config).where(
                _same_scope(Config, sol.organization_id),
                Config.integration_id.is_(None),
                Config.config_schema_id.is_(None),
            ).order_by(Config.key)
        )
    ).scalars().all()

    existing_config_keys = set(
        (
            await ctx.db.execute(
                select(SolutionConfigSchema.key).where(SolutionConfigSchema.solution_id == solution_id)
            )
        ).scalars().all()
    )

    return SolutionCaptureCandidates(
        workflows=await _workflow_summaries(ctx, Workflow.solution_id.is_(None), _same_scope(Workflow, sol.organization_id)),
        apps=await _app_summaries(ctx, Application.solution_id.is_(None), _same_scope(Application, sol.organization_id)),
        forms=await _form_summaries(ctx, Form.solution_id.is_(None), _same_scope(Form, sol.organization_id)),
        agents=await _agent_summaries(ctx, Agent.solution_id.is_(None), _same_scope(Agent, sol.organization_id)),
        claims=await _claim_summaries(ctx, CustomClaim.solution_id.is_(None), _same_scope(CustomClaim, sol.organization_id)),
        tables=await _table_summaries(ctx, Table.solution_id.is_(None), _same_scope(Table, sol.organization_id)),
        configs=[
            SolutionConfigStatus(
                id=row.id,
                key=row.key,
                type=_enum_to_str(row.config_type) or "string",
                required=False,
                description=row.description,
                value_set=True,
            )
            for row in config_rows
            if row.key not in existing_config_keys
        ],
    )


@router.post(
    "/{solution_id}/capture/preview",
    response_model=SolutionDependencyPreview,
    summary="Preview what a capture selection pulls in + outside references (admin only)",
)
async def preview_solution_capture(
    solution_id: UUID,
    body: SolutionDependencyPreviewRequest,
    ctx: Context,
    user: CurrentSuperuser,
) -> SolutionDependencyPreview:
    """Dependency preview for a capture selection (§3.2/§3.3).

    Returns the forward dependency closure the selection drags in (beyond what's
    already selected) and reverse-reference warnings (loose entities outside the
    selection that point at something inside it). The preview is the guard:
    everything is deselectable in the UI; nothing is silently blocked. The scan
    is static, so computed/dynamic refs are invisible — the UI says so.
    """
    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")

    from src.services.solutions.dependency_walker import SolutionDependencyWalker

    return await SolutionDependencyWalker(ctx.db).preview(
        sol,
        workflows=body.workflows,
        tables=body.tables,
        apps=body.apps,
        forms=body.forms,
        agents=body.agents,
        claims=body.claims,
        configs=body.configs,
        include_imports=body.include_imports,
    )


@router.patch(
    "/{solution_id}",
    response_model=SolutionDTO,
    summary="Update an install's local fields (admin only)",
)
async def update_solution(
    solution_id: UUID, body: SolutionUpdate, ctx: Context, user: CurrentSuperuser
) -> SolutionDTO:
    """Edit INSTALL-LOCAL fields only (name/scope/global_repo_access/git fields).

    Portable content (workflows/apps/forms/agents/tables/config declarations) is
    owned by the bundle/git and is never touched here. Changing the install's
    ``organization_id`` (scope) re-stamps every owned entity's org to match —
    owned entities inherit the install's org from the deployer — done under the
    per-install write-lock so it can't race a concurrent deploy.

    DELIBERATELY NOT re-homed on scope change: config VALUES. Config values are
    instance-owned, scope-local data keyed by (org, key) — not FK-tied to the
    install — so a scope change does NOT migrate them to the new org. The
    operator re-enters the values in the new scope. (The 5 entity tables above
    ARE re-homed because they carry ``solution_id`` and are owned by the bundle.)
    """
    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")

    # PATCH semantics: only fields explicitly present in the request are applied.
    # organization_id=None is a legitimate value (global scope), distinguished
    # from "not provided" via model_fields_set (exclude_unset).
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return SolutionDTO.model_validate(sol)  # nothing to do

    from src.services.solutions.write_lock import (
        SolutionWriteLockHeld,
        solution_write_lock,
    )

    try:
        async with solution_write_lock(solution_id):
            scope_changing = (
                "organization_id" in fields
                and fields["organization_id"] != sol.organization_id
            )
            new_org = fields.get("organization_id", sol.organization_id)
            for key, value in fields.items():
                setattr(sol, key, value)
            if scope_changing:
                # Owned entities inherit the install's org → re-stamp them all.
                for model in (Workflow, Application, Form, Agent, CustomClaim, Table):
                    await ctx.db.execute(
                        update(model)
                        .where(model.solution_id == solution_id)
                        .values(organization_id=new_org)
                    )
            await ctx.db.commit()
    except SolutionWriteLockHeld as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A write is already in progress for this install; retry shortly.",
        ) from exc
    await ctx.db.refresh(sol)
    return SolutionDTO.model_validate(sol)


@router.post(
    "/{solution_id}/uninstall",
    response_model=SolutionDTO,
    summary="Uninstall: flip status to inactive, data frozen in place (admin only)",
)
async def uninstall_solution(
    solution_id: UUID, ctx: Context, user: CurrentSuperuser
) -> SolutionDTO:
    """Flip the install's lifecycle status to ``inactive``.

    This is the NON-DESTRUCTIVE uninstall path. Owned entities (tables/workflows/
    forms/agents/apps) stay exactly where they are, still owned by this install
    (``solution_id`` is NOT cleared). No S3 ops. No data mutation of any kind.

    An already-inactive install returns 200 unchanged (idempotent).

    To permanently destroy an install and all of its owned data, use the hard-delete
    path: ``DELETE /{id}?confirm=<slug>``.
    """
    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")
    await ctx.db.execute(
        update(SolutionORM)
        .where(SolutionORM.id == solution_id)
        .values(status="inactive")
    )
    await ctx.db.commit()
    await ctx.db.refresh(sol)
    return SolutionDTO.model_validate(sol)


@router.get(
    "/{solution_id}/deletion-summary",
    response_model=SolutionDeletionSummary,
    summary="Preview counts of what a hard-delete would destroy (admin only)",
)
async def get_solution_deletion_summary(
    solution_id: UUID, ctx: Context, user: CurrentSuperuser
) -> SolutionDeletionSummary:
    """Return per-entity counts of what ``DELETE /{id}?confirm=<slug>`` would destroy.

    Intended for the confirmation modal: the UI fetches this, shows "you are about
    to delete N tables, M workflows, …", then requires the operator to type the
    install slug before issuing the hard-delete.
    """
    from src.models.orm.events import EventSource, EventSubscription
    from src.services.solution_files import enumerate_solution_files

    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")

    async def _count(model: type) -> int:
        result = await ctx.db.execute(
            select(func.count()).select_from(model).where(model.solution_id == solution_id)  # type: ignore[attr-defined]
        )
        return result.scalar_one()

    file_entries = await enumerate_solution_files(ctx.db, solution_id)

    return SolutionDeletionSummary(
        solution_id=solution_id,
        files=len(file_entries),
        tables=await _count(Table),
        workflows=await _count(Workflow),
        apps=await _count(Application),
        forms=await _count(Form),
        agents=await _count(Agent),
        claims=await _count(CustomClaim),
        config_declarations=await _count(SolutionConfigSchema),
        events=await _count(EventSource) + await _count(EventSubscription),
    )


@router.delete(
    "/{solution_id}",
    response_model=SolutionDeleteSummary,
    summary="Hard-delete an install and ALL owned data — irreversible (admin only)",
)
async def delete_solution(
    solution_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
    confirm: str = "",
) -> SolutionDeleteSummary:
    """Confirmed hard-delete: drops the Solution row and ALL owned entities.

    **This is irreversible.** Every owned row (tables, workflows, forms, agents,
    apps, claims, config declarations, events) is removed via the existing
    ``solution_id ondelete=CASCADE`` FKs when the Solution row is deleted.
    S3 bytes are swept after the DB commit: the install's own
    ``_solutions/{id}/`` manifest prefix, its source artifact, its compiled
    app dists, AND every declared-location file object (each ``file_entries``
    row's ``s3_key``, e.g. ``{location}/{id}/{path}``) — those live outside
    the ``_solutions/{id}/`` prefix and are swept individually.

    Requires ``?confirm=<slug>`` equal to the install's slug. A mismatch returns
    422 immediately — nothing is touched.

    To uninstall non-destructively (freeze data, flip status only), use:
    ``POST /{id}/uninstall``.
    """
    # Load WITHOUT eager-loading selectin child relationships.
    # If the children are loaded, the relationship's ``delete-orphan`` cascade marks
    # them in ``session.deleted`` at flush and the Solutions read-only backstop
    # rejects them (drive F3). With ``noload`` the children are never loaded, so the
    # cascade has nothing to orphan and the DB-level ``ondelete=CASCADE`` removes
    # them when the install row goes (exactly like workflows/apps). NOTE: do NOT add
    # ``passive_deletes`` to the relationship to "help" here — it breaks deploy's
    # full-replace stale-removal (``_upsert_connection_declarations`` ORM-deletes a
    # dropped declaration); ``noload`` on this query is the whole fix.
    sol = (
        await ctx.db.execute(
            select(SolutionORM)
            .where(SolutionORM.id == solution_id)
            .options(
                noload(SolutionORM.connection_schema),
                noload(SolutionORM.file_locations),
            )
        )
    ).scalar_one_or_none()
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")

    # Server-side confirm: the caller must echo the install's slug.
    if confirm != sol.slug:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"confirm mismatch: expected '{sol.slug}', got '{confirm}'. "
                "Pass ?confirm=<slug> to confirm the hard-delete."
            ),
        )

    from src.services.file_storage import FileStorageService
    from src.services.solution_files import enumerate_solution_files
    from src.services.solutions.app_build import SolutionAppBuilder
    from src.services.solutions.source_artifact import SolutionSourceArtifactStorage
    from src.services.solutions.storage import SolutionStorage
    from src.services.solutions.write_lock import (
        SolutionWriteLockHeld,
        solution_write_lock,
    )

    try:
        # One writer per install: hold the per-install lock across the DB delete
        # AND the S3 sweep so deletion can't interleave with a concurrent deploy.
        async with solution_write_lock(solution_id):
            # Count + collect app ids BEFORE the cascade delete — for the summary
            # and the S3 app-dist sweep (the rows are gone after the delete).
            async def _count(model: type) -> int:
                result = await ctx.db.execute(
                    select(func.count()).select_from(model).where(model.solution_id == solution_id)  # type: ignore[attr-defined]
                )
                return result.scalar_one()

            app_ids = list(
                (
                    await ctx.db.execute(
                        select(Application.id).where(
                            Application.solution_id == solution_id
                        )
                    )
                ).scalars().all()
            )

            # Enumerate S3 files BEFORE the DB delete (file_metadata rows cascade away).
            file_entries = await enumerate_solution_files(ctx.db, solution_id)

            summary = SolutionDeleteSummary(
                solution_id=solution_id,
                workflows_deleted=await _count(Workflow),
                apps_deleted=len(app_ids),
                forms_deleted=await _count(Form),
                agents_deleted=await _count(Agent),
                claims_deleted=await _count(CustomClaim),
                config_declarations_deleted=await _count(SolutionConfigSchema),
                tables_deleted=await _count(Table),
                files_swept=len(file_entries),
            )

            # Hard-delete: the existing ondelete=CASCADE FKs remove all owned rows.
            # No orphan/detach logic — this is the destructive path.
            await ctx.db.delete(sol)
            await ctx.db.commit()

            # S3 sweep only after the DB is durable (mirrors deploy's DB-then-S3).
            storage = SolutionStorage(solution_id)
            for rel in await storage.list(""):
                await storage.delete(rel)
            await SolutionSourceArtifactStorage(solution_id).delete()
            builder = SolutionAppBuilder()
            for app_id in app_ids:
                await builder.delete_dist(app_id)

            # Declared-location file bytes live outside the _solutions/{id}/
            # prefix (at {location}/{id}/{path}) and are not covered by the
            # storage.list("") sweep above — clear them individually using the
            # keys captured before the DB delete. delete_raw_from_s3 is
            # idempotent, so a missing object is a no-op.
            file_storage = FileStorageService(ctx.db)
            for entry in file_entries:
                if entry.s3_key:
                    await file_storage.delete_raw_from_s3(entry.s3_key)
    except SolutionWriteLockHeld as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A write is already in progress for this install; retry shortly.",
        ) from exc
    return summary


async def _run_deploy_job(
    job_id: UUID,
    solution_id: UUID,
    zip_path: Path,
    *,
    force: bool,
) -> None:
    """Execute the deploy under a fresh session (background task).

    Flips the job ``running`` → ``succeeded`` / ``failed``. Deploy errors that
    were previously surfaced as HTTP status codes are captured into ``error``
    so the polling caller can inspect them (the HTTP request returned 202 long
    before this ran). The whole deploy — including the per-install write lock,
    the DB commit, and the post-commit S3 finalize — happens here so the (often
    >100s) work no longer blocks the request and times out the CLI (Task 7).
    """
    from src.core.database import get_db_context
    from src.services.solutions.zip_install import (
        UnmetDependency,
        deploy_zip_to_solution_path,
    )
    from src.services.solutions.write_lock import (
        SolutionWriteLockHeld,
        solution_write_lock,
    )

    async def _set_status(
        status_value: str,
        error: str | None = None,
        result: dict | None = None,
    ) -> bool:
        async with get_db_context() as db:
            job = await db.get(SolutionDeployJob, job_id)
            if job is None:
                return False
            if job.status in ("succeeded", "failed"):
                return False
            job.status = status_value
            job.error = error
            job.result = result
            return True

    async def _set_phase(phase: str) -> None:
        await _set_status("running", result={"phase": phase})

    if not await _set_status("running"):
        _cleanup_file(zip_path)
        return
    deploy_result: dict | None = None

    async def _execute() -> None:
        nonlocal deploy_result
        async with get_db_context() as db:
            async with solution_write_lock(solution_id):
                solution = await db.get(SolutionORM, solution_id)
                if solution is None:
                    raise SolutionDeployConflict("Solution not found")
                await _set_phase("parsing workspace zip and building app dist")
                result = await deploy_zip_to_solution_path(
                    db, solution, zip_path, force=force
                )
                await db.commit()
                # S3 only after the DB is durable — a failed commit changes no running
                # code (P1-c). Still inside the lock so finalize can't race another deploy.
                await _set_phase("storing source artifact and runtime files")
                await result.finalize_s3()
                deploy_result = {
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
                }
    try:
        await asyncio.wait_for(
            _execute(),
            timeout=DEPLOY_JOB_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        await _set_status("failed", DEPLOY_JOB_TIMEOUT_ERROR)
    except SolutionWriteLockHeld:
        await _set_status(
            "failed",
            "A deploy is already in progress for this install; retry shortly.",
        )
    except SolutionFinalizeIncomplete:
        # Storage failed every retry (a real outage). The DB is committed and the
        # deploy is full-replace + idempotent, so re-running heals it.
        await _set_status(
            "failed",
            "Deploy committed but storage was unavailable after retries. "
            "Re-run the deploy to complete it (it is idempotent).",
        )
    except (
        SolutionDowngradeBlocked,
        SolutionDeployConflict,
        SolutionWorkflowNameMismatch,
        UnmetDependency,
    ) as exc:
        # Caller errors (downgrade without force, invalid bundle, workflow-name
        # mismatch). Surfaced as the job error string for the poller to read.
        await _set_status("failed", str(exc))
    except Exception:  # noqa: BLE001 — capture any deploy failure onto the job
        logger.exception("Solution deploy job %s failed", job_id)
        await _set_status("failed", "Deploy failed unexpectedly; see server logs.")
    else:
        await _set_status("succeeded", result=deploy_result)
    finally:
        _cleanup_file(zip_path)


async def _run_install_job(
    job_id: UUID,
    zip_path: Path,
    *,
    organization_id: UUID | None,
    config_values: dict,
    deployer_email: str,
    force: bool,
    password: str | None,
    replace_secrets: bool,
    replace_data: bool,
    reactivate: bool,
) -> None:
    """Execute a zip install under a fresh session (background task).

    Mirrors ``_run_deploy_job``: flips the job ``running`` → ``succeeded`` /
    ``failed`` and captures build/deploy errors that were previously surfaced as
    HTTP status codes into ``error`` for the poller. ``install_zip_path`` owns the
    per-install write lock, DB commit, and S3 finalize internally, so the whole
    (often >30s) build no longer blocks the request and times out the CLI (Task
    H1). Fail-fast caller-input validation (config JSON, password decrypt) already
    ran synchronously at the endpoint before this job row existed.
    """
    from src.core.database import get_db_context
    from src.services.solutions.deploy import (
        SolutionDeployConflict,
        SolutionDowngradeBlocked,
        SolutionFinalizeIncomplete,
        SolutionWorkflowNameMismatch,
    )
    from src.services.solutions.write_lock import SolutionWriteLockHeld
    from src.services.solutions.zip_install import (
        BadExportPassword,
        ContentCollision,
        GitConnectedInstallError,
        InactiveInstallExists,
        UnmetDependency,
        install_zip_path,
    )

    async def _set_status(
        status_value: str,
        error: str | None = None,
        result: dict | None = None,
    ) -> bool:
        async with get_db_context() as db:
            job = await db.get(SolutionDeployJob, job_id)
            if job is None:
                return False
            if job.status in ("succeeded", "failed"):
                return False
            job.status = status_value
            job.error = error
            job.result = result
            return True

    if not await _set_status(
        "running", result={"phase": "building and deploying bundle"}
    ):
        _cleanup_file(zip_path)
        return
    install_result: dict | None = None

    async def _execute() -> None:
        nonlocal install_result
        async with get_db_context() as db:
            solution = await install_zip_path(
                db,
                zip_path,
                organization_id=organization_id,
                config_values=config_values,
                deployer_email=deployer_email,
                force=force,
                password=password,
                replace_secrets=replace_secrets,
                replace_data=replace_data,
                reactivate=reactivate,
            )
            install_result = {"solution_id": str(solution.id), "slug": solution.slug}
    try:
        await asyncio.wait_for(
            _execute(),
            timeout=DEPLOY_JOB_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        await _set_status("failed", DEPLOY_JOB_TIMEOUT_ERROR)
    except InactiveInstallExists as exc:
        await _set_status(
            "failed",
            str(exc),
            result={
                "reason": "inactive_install_exists",
                "solution_id": str(exc.solution_id),
                "slug": exc.slug,
            },
        )
    except SolutionWriteLockHeld:
        await _set_status(
            "failed",
            "A deploy is already in progress for this install; retry shortly.",
        )
    except SolutionFinalizeIncomplete:
        await _set_status(
            "failed",
            "Install committed but storage was unavailable after retries. "
            "Re-run the install to complete it (it is idempotent).",
        )
    except (
        UnmetDependency,
        BadExportPassword,
        ContentCollision,
        GitConnectedInstallError,
        SolutionDowngradeBlocked,
        SolutionDeployConflict,
        SolutionWorkflowNameMismatch,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        await _set_status("failed", str(exc) or "Install rejected.")
    except Exception:  # noqa: BLE001 — capture any install failure onto the job
        logger.exception("Solution install job %s failed", job_id)
        await _set_status("failed", "Install failed unexpectedly; see server logs.")
    else:
        await _set_status("succeeded", result=install_result)
    finally:
        _cleanup_file(zip_path)


async def _run_install_from_repo_job(
    job_id: UUID,
    solution_id: UUID,
    checkout_root: str,
    clone_tmp: str,
) -> None:
    """Deploy a freshly-created git-connected install from an already-cloned
    checkout (background task). Mirrors ``_run_deploy_job``.

    The clone + slug/scope 409 already ran synchronously at the endpoint, which
    also created the ``SolutionORM`` row. This job runs the build/deploy/finalize
    under the per-install write lock (closes the from-repo lock race, audit M7). A
    brand-new install whose first deploy fails must not persist as an empty
    git_connected orphan, so on failure the job deletes the row it was deploying.

    ``checkout_root`` is the workspace dir to deploy (may be a subpath of the
    clone); ``clone_tmp`` is the top-level temp dir to remove when done.
    """
    import shutil

    from src.core.database import get_db_context
    from src.services.solutions.git_sync import deploy_from_workspace
    from src.services.solutions.write_lock import (
        SolutionWriteLockHeld,
        solution_write_lock,
    )

    async def _set_status(
        status_value: str,
        error: str | None = None,
        result: dict | None = None,
    ) -> bool:
        async with get_db_context() as db:
            job = await db.get(SolutionDeployJob, job_id)
            if job is None:
                return False
            if job.status in ("succeeded", "failed"):
                return False
            job.status = status_value
            job.error = error
            job.result = result
            return True

    async def _delete_orphan_install() -> None:
        async with get_db_context() as db:
            # Detach the job row from the install FIRST: solution_deploy_jobs
            # carries an ondelete=CASCADE FK to solutions, so deleting the orphan
            # would otherwise sweep the job row with it and the poller would 404
            # instead of observing the terminal failed state.
            job = await db.get(SolutionDeployJob, job_id)
            if job is not None:
                job.install_id = None
                await db.flush()
            row = await db.get(SolutionORM, solution_id)
            if row is not None:
                await db.delete(row)

    if not await _set_status(
        "running", result={"phase": "deploying from repo checkout"}
    ):
        shutil.rmtree(clone_tmp, ignore_errors=True)
        return
    install_result: dict | None = None

    async def _execute() -> None:
        nonlocal install_result
        async with get_db_context() as db:
            async with solution_write_lock(solution_id):
                solution = await db.get(SolutionORM, solution_id)
                if solution is None:
                    raise SolutionDeployConflict("Solution not found")
                result = await deploy_from_workspace(db, solution, Path(checkout_root))
                await db.commit()
                await result.finalize_s3()
                install_result = {
                    "solution_id": str(solution_id),
                    "slug": solution.slug,
                }
    try:
        await asyncio.wait_for(
            _execute(),
            timeout=DEPLOY_JOB_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        await _delete_orphan_install()
        await _set_status("failed", DEPLOY_JOB_TIMEOUT_ERROR)
    except SolutionWriteLockHeld:
        await _set_status(
            "failed",
            "A deploy is already in progress for this install; retry shortly.",
        )
    except Exception as exc:  # noqa: BLE001 — capture any deploy failure onto the job
        logger.exception("Solution install-from-repo job %s failed", job_id)
        await _delete_orphan_install()
        await _set_status("failed", f"Install cloned but deploy failed: {exc}")
    else:
        await _set_status("succeeded", result=install_result)
    finally:
        shutil.rmtree(clone_tmp, ignore_errors=True)


@router.post(
    "/{solution_id}/deploy",
    response_model=SolutionDeployEnqueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue a deploy to an install (async, full replace, admin only)",
)
async def deploy_solution(
    solution_id: UUID,
    file: Annotated[UploadFile, File(description="Solution workspace zip")],
    ctx: Context,
    user: CurrentSuperuser,
    background_tasks: BackgroundTasks,
    force: bool = False,
) -> SolutionDeployEnqueued:
    solution = await ctx.db.get(SolutionORM, solution_id)
    if solution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")

    # One-writer invariant: a git-connected install is written only by auto-pull
    # (Sub-plan 5); deploy is refused for it.
    if solution.git_connected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This install is git-connected; deploy is disabled (auto-pull is the only writer).",
        )

    from src.services.solutions.zip_install import preview_zip_path

    zip_path = await _spool_upload_to_temp(file, prefix="bifrost-solution-deploy-")
    try:
        preview = preview_zip_path(zip_path)
    except (ValueError, zipfile.BadZipFile) as exc:
        _cleanup_file(zip_path)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid solution zip: {exc}",
        ) from exc

    # Capture round-trip guard: an entity captured (UI/CLI) into this install but
    # not yet pulled into source has a pending_captures row. If such an entity is
    # absent from the incoming full-replace manifest, the reconcile sweep would
    # silently DELETE it — so we 409-block instead and tell the caller to pull
    # first. An entity absent with NO pending row is a genuine delete (source has
    # demonstrably seen it), and proceeds unchanged. force=True bypasses the block.
    # These are immediate caller-input errors, so they stay synchronous (the
    # caller gets a 409 from the request, not a failed job).
    if not force:
        from src.models.orm.pending_capture import PendingCaptureORM
        from src.services.solutions.pending import unpulled_blockers

        manifest_ids: dict[str, set[str]] = {
            "table": {str(t["id"]) for t in preview.tables if t.get("id")},
            "form": {str(f["id"]) for f in preview.forms if f.get("id")},
            "agent": {str(a["id"]) for a in preview.agents if a.get("id")},
            "config": {str(c["key"]) for c in preview.config_schemas if c.get("key")},
            "event": {str(e["id"]) for e in preview.events if e.get("id")},
            "claim": {str(c["id"]) for c in preview.claims if c.get("id")},
        }
        pending_rows = (
            await ctx.db.execute(
                select(PendingCaptureORM.entity_type, PendingCaptureORM.entity_id).where(
                    PendingCaptureORM.solution_id == solution_id
                )
            )
        ).all()
        blockers = unpulled_blockers([(t, i) for t, i in pending_rows], manifest_ids)
        if blockers:
            _cleanup_file(zip_path)
            detail = ", ".join(f"{t}:{i}" for t, i in blockers)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"{len(blockers)} entity(ies) were captured into this solution but are "
                    f"not in your source manifest: {detail}. Run `bifrost solution pull`, "
                    f"then deploy (or deploy with force to override)."
                ),
            )

    # Persist the orchestration row before scheduling the task so the caller can
    # poll for status the instant it has the id.
    job = SolutionDeployJob(install_id=solution_id, status="queued")
    ctx.db.add(job)
    await ctx.db.commit()
    await ctx.db.refresh(job)

    background_tasks.add_task(_run_deploy_job, job.id, solution_id, zip_path, force=force)
    return SolutionDeployEnqueued(deploy_job_id=job.id)


@router.get(
    "/deploy-jobs/{job_id}",
    response_model=SolutionDeployJobStatus,
    summary="Poll the status of an async deploy job (admin only)",
)
async def get_deploy_job(
    job_id: UUID, ctx: Context, user: CurrentSuperuser
) -> SolutionDeployJobStatus:
    job = await ctx.db.get(SolutionDeployJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Deploy job not found"
        )
    if expire_deploy_job_if_timed_out(job):
        await ctx.db.commit()
        await ctx.db.refresh(job)
    return SolutionDeployJobStatus.model_validate(job)


@router.post(
    "/{solution_id}/capture",
    response_model=SolutionCaptureResponse,
    summary="Capture existing loose entities into an install (admin only)",
)
async def capture_solution_entities(
    solution_id: UUID, body: SolutionCaptureRequest, ctx: Context, user: CurrentSuperuser
) -> SolutionCaptureResponse:
    """Adopt existing `_repo/` entities into this install in place.

    This is the backend migration primitive for turning legacy app/table/workflow
    clusters into a Solution. It stamps compatible loose entities with
    ``solution_id`` and stores an export zip containing the captured definitions.
    """
    solution = await ctx.db.get(SolutionORM, solution_id)
    if solution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")

    from src.services.solutions.capture import (
        SolutionCaptureConflict,
        SolutionCaptureSelectors,
        SolutionCaptureService,
    )
    from src.services.solutions.write_lock import (
        SolutionWriteLockHeld,
        solution_write_lock,
    )

    try:
        async with solution_write_lock(solution_id):
            result = await SolutionCaptureService(ctx.db).capture(
                solution,
                SolutionCaptureSelectors(
                    workflows=body.workflows,
                    tables=body.tables,
                    apps=body.apps,
                    forms=body.forms,
                    agents=body.agents,
                    claims=body.claims,
                    configs=body.configs,
                ),
                include_imports=body.include_imports,
                captured_by=user.user_id,
            )
            await ctx.db.commit()
    except SolutionWriteLockHeld as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A write is already in progress for this install; retry shortly.",
        ) from exc
    except SolutionCaptureConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return SolutionCaptureResponse(
        solution_id=solution_id,
        workflows_captured=result.workflows_captured,
        tables_captured=result.tables_captured,
        apps_captured=result.apps_captured,
        forms_captured=result.forms_captured,
        agents_captured=result.agents_captured,
        claims_captured=result.claims_captured,
        config_declarations_captured=result.config_declarations_captured,
    )


@router.post(
    "/{solution_id}/pull/ack",
    response_model=PullAckResponse,
    summary="Clear pending_captures rows the client pulled into source (admin only)",
)
async def ack_pulled_captures(
    solution_id: UUID, body: PullAckRequest, ctx: Context, user: CurrentSuperuser
) -> PullAckResponse:
    """Server-authoritative clear of pending_captures rows.

    ``bifrost solution pull`` materializes captured entities into the workspace
    ``.bifrost/`` manifest, then POSTs exactly what it wrote here so the server
    deletes those queue rows. A stale client can only clear rows it names, so it
    can't double-clear another client's un-pulled captures.
    """
    from sqlalchemy import and_, delete

    from src.models.orm.pending_capture import PendingCaptureORM

    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")

    cleared = 0
    for ent in body.entities:
        res = await ctx.db.execute(
            delete(PendingCaptureORM).where(
                and_(
                    PendingCaptureORM.solution_id == solution_id,
                    PendingCaptureORM.entity_type == ent.entity_type,
                    PendingCaptureORM.entity_id == ent.entity_id,
                )
            )
        )
        cleared += res.rowcount or 0
    await ctx.db.commit()
    return PullAckResponse(cleared=cleared)


@router.post(
    "/{solution_id}/sync",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Auto-pull a git-connected install from its repo (admin only)",
)
async def sync_solution(solution_id: UUID, ctx: Context, user: CurrentSuperuser) -> dict:
    """Pull the connected install's repo ``main`` and deploy it (criterion 13).

    This is the auto-pull entry point (webhook/poll/manual). It is the ONLY
    writer for a connected install — the deploy endpoint is refused for it. For a
    disconnected install there is nothing to pull, so this is refused in turn.
    """
    solution = await ctx.db.get(SolutionORM, solution_id)
    if solution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")
    if not solution.git_connected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This install is not git-connected; use deploy instead.",
        )
    if not solution.git_repo_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This git-connected install has no git_repo_url to pull from.",
        )

    from src.services.solutions.git_sync import NotASolutionWorkspace
    from src.services.solutions.git_sync import sync as git_sync

    try:
        # git_sync commits + runs the S3 phase itself (inside its per-install
        # lock, DB-commit-before-S3 per P1-c), so the router does not commit here.
        await git_sync(ctx.db, solution)
        # A successful pull means the install is now at the repo HEAD — clear any
        # pending "update available" signal so the badge disappears.
        if solution.update_available_version is not None:
            solution.update_available_version = None
            await ctx.db.commit()
    except NotASolutionWorkspace as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return {"solution_id": str(solution_id), "status": "synced"}


async def _preview_to_dto(
    ctx: Context, result: "PreviewResult", org_id: UUID | None
) -> SolutionInstallPreview:
    """Assemble the install-plan DTO from a parsed workspace: detect an existing
    install for upgrade routing, then return the SolutionInstallPreview. Shared
    by the zip-upload and git-repo preview endpoints (no DB write)."""
    from src.services.solutions.zip_install import compute_upgrade_diff, find_install

    existing_install: SolutionExistingInstall | None = None
    diff: SolutionUpgradeDiff | None = None
    existing = (
        await find_install(ctx.db, slug=result.slug, organization_id=org_id)
        if result.slug
        else None
    )
    if existing is not None:
        # Read-only lookups of the install's current solution-owned rows — the
        # preview never writes (no flush/commit anywhere on this path).
        installed: dict[str, list[tuple[UUID, str]]] = {}
        for etype, model in (
            ("workflows", Workflow),
            ("tables", Table),
            ("forms", Form),
            ("agents", Agent),
            ("claims", CustomClaim),
            ("apps", Application),
        ):
            rows = (
                await ctx.db.execute(
                    select(model.id, model.name).where(model.solution_id == existing.id)
                )
            ).all()
            installed[etype] = [(row_id, name) for row_id, name in rows]
        decls = (
            await ctx.db.execute(
                select(
                    SolutionConfigSchema.key,
                    SolutionConfigSchema.type,
                    SolutionConfigSchema.required,
                ).where(SolutionConfigSchema.solution_id == existing.id)
            )
        ).all()
        existing_install = SolutionExistingInstall(
            id=existing.id, name=existing.name, version=existing.version
        )
        diff = compute_upgrade_diff(
            result,
            install_id=existing.id,
            installed=installed,
            installed_config_schemas=[(k, t, r) for k, t, r in decls],
        )

    return SolutionInstallPreview(
        slug=result.slug,
        name=result.name,
        scope=result.scope,  # type: ignore[arg-type]
        version=result.version,
        workflows=result.workflows,
        tables=result.tables,
        apps=result.apps,
        forms=result.forms,
        agents=result.agents,
        claims=result.claims,
        config_schemas=result.config_schemas,
        connection_schemas=result.connection_schemas,
        existing_install=existing_install,
        diff=diff,
        requires_password=result.requires_password,
        readme=result.readme,
    )


@router.post(
    "/install/preview",
    response_model=SolutionInstallPreview,
    summary="Preview a Solution install zip (parse-only, admin only)",
)
async def install_preview(
    file: Annotated[UploadFile, File(description="Solution workspace zip")],
    ctx: Context,
    user: CurrentSuperuser,
    organization_id: Annotated[str | None, FastapiForm()] = None,
) -> SolutionInstallPreview:
    """Unzip + parse a Solution workspace zip and report what it would create.

    Parse-only: no DB write, no S3, no build. The drag-and-drop UI calls this to
    show the install plan + declared configs before committing.

    When an install already exists for the zip's slug at the requested scope
    (``organization_id`` resolved exactly as the install endpoint does:
    empty/absent → global NULL), the response also carries ``existing_install``
    + ``diff`` so the UI routes to UPGRADE instead of a second install (Task 22).
    """
    from src.services.solutions.zip_install import preview_zip_path

    org_id: UUID | None = None
    if organization_id:
        try:
            org_id = UUID(organization_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid organization_id: {organization_id}",
            ) from exc

    zip_path = await _spool_upload_to_temp(file, prefix="bifrost-solution-preview-")
    try:
        result = preview_zip_path(zip_path)
    except (ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid solution zip: {exc}",
        ) from exc
    finally:
        _cleanup_file(zip_path)

    return await _preview_to_dto(ctx, result, org_id)


@router.post(
    "/install/preview-repo",
    response_model=SolutionInstallPreview,
    summary="Preview a Solution install from a git repo (parse-only, admin only)",
)
async def install_preview_repo(
    body: SolutionRepoPreviewRequest, ctx: Context, user: CurrentSuperuser
) -> SolutionInstallPreview:
    """Clone the repo (+ optional subpath/ref), parse the workspace, and report
    the install plan — the same plan the zip preview returns. No DB write."""
    import tempfile
    from pathlib import Path

    from src.services.solutions.git_sync import (
        NotASolutionWorkspace,
        clone_repo_to_dir,
        resolve_repo_subpath,
    )
    from src.services.solutions.zip_install import _parse_workspace

    with tempfile.TemporaryDirectory(prefix="bifrost-repo-preview-") as tmp:
        work = Path(tmp)
        try:
            await clone_repo_to_dir(body.repo_url, work, ref=body.git_ref)
        except Exception as exc:  # GitPython GitCommandError etc.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Could not clone {body.repo_url}: {exc}",
            ) from exc
        try:
            root = resolve_repo_subpath(work, body.repo_subpath)
        except NotASolutionWorkspace as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        _r = os.path.realpath(root)
        _marker = os.path.realpath(os.path.join(_r, "bifrost.solution.yaml"))
        if not _marker.startswith(_r + os.sep) or not os.path.isfile(_marker):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"No bifrost.solution.yaml at "
                f"{body.repo_subpath or '<repo root>'} in {body.repo_url}",
            )
        result = _parse_workspace(root)
    return await _preview_to_dto(ctx, result, None)


@router.post(
    "/install/from-repo",
    response_model=SolutionDeployEnqueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue a Solution install from a git repo (git-connected, admin only)",
)
async def install_from_repo(
    body: SolutionRepoPreviewRequest,
    ctx: Context,
    user: CurrentSuperuser,
    background_tasks: BackgroundTasks,
) -> SolutionDeployEnqueued:
    """Create a git-connected install from a repo (+ optional subpath/ref) and
    enqueue its first deploy as an async job. git-connected from birth: deploy is
    refused, auto-pull is the only writer. 409 if an install of the same
    (slug, scope) already exists.

    Fail-fast synchronous validation runs BEFORE the job row / install row exist:
    the clone (+ subpath/ref/descriptor checks) and the slug+scope 409. The
    install row is then created and the build/deploy/finalize runs as a background
    job under the per-install write lock (closes the from-repo lock race, audit
    M7). Poll ``GET /deploy-jobs/{deploy_job_id}`` for the result; the terminal
    ``result`` carries the installed ``solution_id``. If the first deploy fails,
    the job removes the brand-new install so no empty git_connected orphan remains.
    """
    import tempfile
    from pathlib import Path

    from src.services.solutions.git_sync import (
        NotASolutionWorkspace,
        clone_repo_to_dir,
        resolve_repo_subpath,
    )
    from src.services.solutions.zip_install import _parse_workspace, find_install

    # Clone synchronously into a PERSISTENT temp dir (the async job reads it and
    # cleans it up). ONE clone: read the descriptor here AND deploy from the same
    # checkout in the job — no second clone, no TOCTOU window.
    tmp = tempfile.mkdtemp(prefix="bifrost-repo-install-")
    cleanup_tmp = True
    try:
        work = Path(tmp)
        try:
            await clone_repo_to_dir(body.repo_url, work, ref=body.git_ref)
        except Exception as exc:
            # GitPython raises various exc subtypes (GitCommandError,
            # InvalidGitRepositoryError, ...) — catch-all intentional for a
            # user-supplied URL.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Could not clone {body.repo_url}: {exc}",
            ) from exc
        try:
            root = resolve_repo_subpath(work, body.repo_subpath)
        except NotASolutionWorkspace as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        _r = os.path.realpath(root)
        _marker = os.path.realpath(os.path.join(_r, "bifrost.solution.yaml"))
        if not _marker.startswith(_r + os.sep) or not os.path.isfile(_marker):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"No bifrost.solution.yaml at "
                f"{body.repo_subpath or '<repo root>'} in {body.repo_url}",
            )
        parsed = _parse_workspace(root)
        if not parsed.slug:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Repo has no valid bifrost.solution.yaml (missing slug)",
            )

        # Install kind comes from the REQUEST (unified --org standard), not the
        # descriptor: HOME (organization_id absent) => the caller's own org;
        # explicit null => global; a UUID => that org.
        if "organization_id" in body.model_fields_set:
            org_id: UUID | None = body.organization_id
        else:
            org_id = ctx.org_id
            if org_id is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="install requires an organization (caller has no org; "
                    "pass organization_id, or null for a global install)",
                )

        # Fast-path 409 with a clear message for the common sequential case; the
        # flush() catch below covers the concurrent race on the unique index.
        existing = await find_install(ctx.db, slug=parsed.slug, organization_id=org_id)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An install of '{parsed.slug}' already exists for this scope; "
                f"reconnect or update it instead.",
            )

        solution = SolutionORM(
            slug=parsed.slug,
            name=parsed.name or parsed.slug,
            organization_id=org_id,
            git_connected=True,
            git_repo_url=body.repo_url,
            repo_subpath=body.repo_subpath,
            git_ref=body.git_ref,
        )
        ctx.db.add(solution)
        try:
            await ctx.db.flush()  # surfaces the unique (slug, org) violation now
        except IntegrityError as exc:
            await ctx.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An install of '{parsed.slug}' already exists for this scope.",
            ) from exc

        # Persist the orchestration row alongside the install so the caller can
        # poll immediately. The build/deploy/finalize runs in the job.
        job = SolutionDeployJob(install_id=solution.id, status="queued")
        ctx.db.add(job)
        await ctx.db.commit()
        await ctx.db.refresh(job)

        background_tasks.add_task(
            _run_install_from_repo_job,
            job.id,
            solution.id,
            str(root),
            tmp,
        )
        # The job owns the checkout dir teardown now — don't rmtree it here.
        cleanup_tmp = False
        return SolutionDeployEnqueued(deploy_job_id=job.id)
    finally:
        if cleanup_tmp:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


@router.post(
    "/install",
    response_model=SolutionDeployEnqueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue a Solution zip install (async deploy + config values, admin only)",
)
async def install_solution(
    file: Annotated[UploadFile, File(description="Solution workspace zip")],
    ctx: Context,
    user: CurrentSuperuser,
    background_tasks: BackgroundTasks,
    organization_id: Annotated[str | None, FastapiForm()] = None,
    config_values: Annotated[str, FastapiForm()] = "{}",
    password: Annotated[str | None, FastapiForm()] = None,
    replace_secrets: Annotated[bool, FastapiForm()] = False,
    replace_data: Annotated[bool, FastapiForm()] = False,
    force: bool = False,
    reactivate: bool = False,
) -> SolutionDeployEnqueued:
    """Enqueue an async install of a Solution from a workspace zip.

    Fail-fast, synchronous validation runs BEFORE a job row exists: the
    ``organization_id`` / ``config_values`` shape, the zip being a Solution
    workspace, and — for a full-backup zip carrying ``.bifrost/secrets.enc`` — a
    password decrypt-check (a wrong/missing password is a synchronous 422). The
    heavy build/deploy/finalize then runs as a background job (``SolutionDeployJob``)
    so the often-slow build no longer times out the CLI's HTTP request (Task H1).
    Poll ``GET /deploy-jobs/{deploy_job_id}`` for the result; the terminal
    ``result`` carries the installed ``solution_id``.

    The job resolves-or-creates the install at the chosen scope (empty/absent
    ``organization_id`` → global NULL), runs the proven deploy under the
    per-install write lock, and — in the same locked section after the S3 finalize
    — applies the provided ``config_values``. A missing required config does NOT
    block the install (warn-not-block). Build-time refusals (unmet dependency,
    content collision, git-connected install, downgrade) surface as a ``failed``
    job with the error, mirroring the deploy job.

    The inactive-install conflict is a synchronous 409 (structured
    ``reason=inactive_install_exists`` detail): it is a caller-decision prompt —
    pass ``?reactivate=true`` or delete the install first — so it must refuse on
    the request itself, before a job row exists.
    """
    from src.services.solutions.zip_install import (
        BadExportPassword,
        find_install,
        validate_install_zip,
    )

    org_id: UUID | None = None
    if organization_id:
        try:
            org_id = UUID(organization_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid organization_id: {organization_id}",
            ) from exc

    try:
        values = json.loads(config_values) if config_values else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"config_values must be a JSON object: {exc}",
        ) from exc
    if not isinstance(values, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="config_values must be a JSON object mapping key → value",
        )

    zip_path = await _spool_upload_to_temp(file, prefix="bifrost-solution-install-")
    # Fail-fast validation BEFORE the job row exists: a corrupt zip / non-workspace
    # / wrong-or-missing secrets password returns a synchronous 4xx, not a failed
    # job (mirrors deploy's synchronous preview_zip_path guard).
    try:
        preview = validate_install_zip(zip_path, password=password)
    except BadExportPassword as exc:
        _cleanup_file(zip_path)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except (ValueError, zipfile.BadZipFile) as exc:
        _cleanup_file(zip_path)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid solution zip: {exc}",
        ) from exc

    # Inactive-install conflict is a synchronous, structured 409 prompt (the
    # caller must choose: reactivate or delete first) — refuse BEFORE a job row
    # exists. The job re-checks under the write lock (race backstop); this
    # fast-path keeps the interactive contract the UI/CLI prompt on.
    if not reactivate:
        assert preview.slug is not None  # validate_install_zip guarantees it
        existing = await find_install(ctx.db, slug=preview.slug, organization_id=org_id)
        if existing is not None and existing.status == "inactive":
            _cleanup_file(zip_path)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "reason": "inactive_install_exists",
                    "solution_id": str(existing.id),
                    "slug": existing.slug,
                    "message": (
                        f"An inactive install of '{existing.slug}' already exists "
                        f"(id={existing.id}). Pass reactivate=true to reactivate it, "
                        "or delete the existing install first."
                    ),
                },
            )

    # Persist the orchestration row before scheduling the task so the caller can
    # poll for status the instant it has the id. install_id is nullable — a zip
    # install resolves-or-creates its target install inside the job, so the row
    # isn't known yet (the succeeded result carries the solution_id).
    job = SolutionDeployJob(install_id=None, status="queued")
    ctx.db.add(job)
    await ctx.db.commit()
    await ctx.db.refresh(job)

    background_tasks.add_task(
        _run_install_job,
        job.id,
        zip_path,
        organization_id=org_id,
        config_values=values,
        deployer_email=user.email,
        force=force,
        password=password,
        replace_secrets=replace_secrets,
        replace_data=replace_data,
        reactivate=reactivate,
    )
    return SolutionDeployEnqueued(deploy_job_id=job.id)
