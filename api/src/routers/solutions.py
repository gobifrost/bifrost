"""REST endpoints for Solutions — installable surfaces (success-criteria §3).

An install is created here, then deployed via ``POST /{id}/deploy`` (the single
writer for a disconnected install). Deploy is a full replace by contract and is
non-interactive — it always applies the whole bundle.

Solution-management itself is an admin operation; the deployed *entities* are
what end users see (the Solution is invisible to them — criterion 16).
"""

from __future__ import annotations

import base64
import json
import logging
import zipfile
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from fastapi import APIRouter, Body, File, HTTPException, Response, UploadFile, status
from fastapi import Form as FastapiForm
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from src.core.auth import Context, CurrentSuperuser
from src.models.contracts.solutions import (
    Solution as SolutionDTO,
    SolutionCaptureCandidates,
    SolutionCaptureRequest,
    SolutionCaptureResponse,
    SolutionConfigStatus,
    SolutionCreate,
    SolutionDeleteSummary,
    SolutionDependencyPreview,
    SolutionDependencyPreviewRequest,
    SolutionDeployRequest,
    SolutionDeployResponse,
    SolutionEntities,
    SolutionEntitySummary,
    SolutionExistingInstall,
    SolutionInstallPreview,
    SolutionReadme,
    SolutionReadmeUpdate,
    SolutionRepoPreviewRequest,
    SolutionSetupStatus,
    SolutionsList,
    SolutionUpdate,
    SolutionUpgradeDiff,
)
from src.models.orm.agents import Agent
from src.models.orm.applications import Application
from src.models.orm.config import Config
from src.models.orm.custom_claims import CustomClaim
from src.models.orm.forms import Form
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solutions import Solution as SolutionORM
from src.models.orm.tables import Table
from src.models.orm.workflows import Workflow
from src.services.solutions.deploy import (
    SolutionBundle,
    SolutionDeployer,
    SolutionDeployConflict,
    SolutionDowngradeBlocked,
    SolutionFinalizeIncomplete,
)

if TYPE_CHECKING:
    from src.services.solutions.zip_install import PreviewResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/solutions", tags=["Solutions"])


@router.post("", response_model=SolutionDTO, status_code=status.HTTP_201_CREATED, summary="Create a Solution install (admin only)")
async def create_solution(body: SolutionCreate, ctx: Context, user: CurrentSuperuser) -> SolutionDTO:
    # Scope: global → org NULL; org → explicit organization_id or caller's org.
    if body.scope == "global":
        org_id: UUID | None = None
    else:
        org_id = body.organization_id or ctx.org_id
        if org_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="org-scoped install requires an organization_id",
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


@router.get("", response_model=SolutionsList, summary="List Solution installs (admin only)")
async def list_solutions(ctx: Context, user: CurrentSuperuser) -> SolutionsList:
    rows = (await ctx.db.execute(select(SolutionORM).order_by(SolutionORM.slug))).scalars().all()
    return SolutionsList(solutions=[SolutionDTO.model_validate(r) for r in rows])


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
    edit it directly here on a disconnected install."""
    row = await ctx.db.get(SolutionORM, solution_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")
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
    include_data: bool = False,
    password: Annotated[str | None, Body(embed=True)] = None,
) -> Response:
    """Rebuild the install's workspace bundle LIVE from the entities it
    currently owns, so the export always reflects present ownership (not the
    last capture/deploy). Directly re-installable via the zip-install path.

    This is a POST (not GET) specifically so the full-backup ``password`` rides
    in the request BODY rather than the URL query string — a query-string secret
    leaks into access logs, proxies, and browser history. ``mode`` and
    ``include_data`` stay in the query (they are not sensitive).

    ``mode=shareable`` (default): portable export, no sensitive values.
    ``mode=full``: includes an encrypted ``.bifrost/secrets.enc`` blob carrying
    the config values set for this install; requires ``password`` (in the body).
    ``include_data=true``: include table row data in the encrypted blob.
    Requires ``mode=full`` (data must be encrypted).
    """
    if mode not in ("shareable", "full"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="mode must be 'shareable' or 'full'",
        )
    if mode == "full" and not password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="full export requires a password",
        )
    if include_data and mode != "full":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="include_data requires mode=full (data must be encrypted)",
        )

    from src.services.solutions.capture import SolutionCaptureService
    from src.services.solutions.export import build_workspace_zip

    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")

    include_values = mode == "full"
    bundle = await SolutionCaptureService(ctx.db).bundle_for(
        sol, include_imports=True, include_values=include_values, include_data=include_data
    )
    data = build_workspace_zip(bundle, password=password if include_values else None)
    filename = f"{sol.slug}-{sol.version or 'unversioned'}.zip"
    return Response(
        content=data,
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

    workflows = await _workflow_summaries(ctx, Workflow.solution_id == solution_id)
    apps = await _app_summaries(ctx, Application.solution_id == solution_id)
    forms = await _form_summaries(ctx, Form.solution_id == solution_id)
    agents = await _agent_summaries(ctx, Agent.solution_id == solution_id)
    claims = await _claim_summaries(ctx, CustomClaim.solution_id == solution_id)
    tables = await _table_summaries(ctx, Table.solution_id == solution_id)

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


async def _workflow_summaries(ctx: Context, *where) -> list[SolutionEntitySummary]:
    rows = (await ctx.db.execute(select(Workflow).where(*where).order_by(Workflow.name))).scalars().all()
    return [
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
        )
        for row in rows
    ]


async def _app_summaries(ctx: Context, *where) -> list[SolutionEntitySummary]:
    rows = (await ctx.db.execute(select(Application).where(*where).order_by(Application.name))).scalars().all()
    return [
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
        )
        for row in rows
    ]


async def _form_summaries(ctx: Context, *where) -> list[SolutionEntitySummary]:
    rows = (await ctx.db.execute(select(Form).where(*where).order_by(Form.name))).scalars().all()
    return [
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
        )
        for row in rows
    ]


async def _agent_summaries(ctx: Context, *where) -> list[SolutionEntitySummary]:
    rows = (await ctx.db.execute(select(Agent).where(*where).order_by(Agent.name))).scalars().all()
    return [
        SolutionEntitySummary(
            id=row.id,
            name=row.name,
            description=row.description,
            organization_id=row.organization_id,
            access_level=_enum_to_str(row.access_level),
            is_active=row.is_active,
            created_at=row.created_at,
        )
        for row in rows
    ]


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


@router.delete(
    "/{solution_id}",
    response_model=SolutionDeleteSummary,
    summary="Delete an install and everything it owns (admin only)",
)
async def delete_solution(
    solution_id: UUID, ctx: Context, user: CurrentSuperuser
) -> SolutionDeleteSummary:
    """Delete an install non-destructively for customer data.

    Pure-code entities (workflows/apps/forms/agents) and the install's config
    DECLARATIONS cascade away via the ``solution_id`` FK ``ondelete=CASCADE``.
    Data-bearing entities are ORPHANED instead of cascaded:

    - Owned tables are DETACHED before the Solution delete (``solution_id`` set
      to NULL so the cascade can't reach them) and survive as ordinary org
      tables. Their documents are untouched — they hang off the surviving table.
    - The install's config VALUES (Config rows in the install's org scope whose
      key matches a declaration) are stamped with orphan provenance and survive
      (Config has no ``solution_id`` FK, so they were never cascade-tied).

    Both carry ``origin_solution_slug``/``origin_solution_id``/``orphaned_at`` so
    a reinstall can reattach them. The install's S3 artifacts are swept. The git
    repo is NEVER touched — a git-connected install is deletable; only the install
    and its local artifacts go, the upstream repo is left alone.
    """
    sol = await ctx.db.get(SolutionORM, solution_id)
    if sol is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Solution not found")

    from src.services.solutions.app_build import SolutionAppBuilder
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
                return len(
                    (
                        await ctx.db.execute(
                            select(model.id).where(model.solution_id == solution_id)
                        )
                    ).scalars().all()
                )

            app_ids = set(
                (
                    await ctx.db.execute(
                        select(Application.id).where(
                            Application.solution_id == solution_id
                        )
                    )
                ).scalars().all()
            )

            # Owned table ids (for the orphan count) — captured BEFORE we detach
            # them, since the detach update clears ``solution_id``.
            table_ids = set(
                (
                    await ctx.db.execute(
                        select(Table.id).where(Table.solution_id == solution_id)
                    )
                ).scalars().all()
            )

            # The install's config DECLARATION keys — used both to count the
            # cascaded declarations and to find the config VALUES to orphan.
            decl_keys = set(
                (
                    await ctx.db.execute(
                        select(SolutionConfigSchema.key).where(
                            SolutionConfigSchema.solution_id == solution_id
                        )
                    )
                ).scalars().all()
            )

            now = datetime.now(timezone.utc)

            # DETACH TABLES (before the Solution delete so the FK cascade can't
            # reach them). They survive as ordinary org tables; documents are
            # untouched (they hang off the surviving table row).
            await ctx.db.execute(
                update(Table)
                .where(Table.solution_id == solution_id)
                .values(
                    solution_id=None,
                    organization_id=sol.organization_id,
                    origin_solution_slug=sol.slug,
                    origin_solution_id=sol.id,
                    orphaned_at=now,
                )
            )

            # STAMP CONFIG VALUES with orphan provenance (Config has no
            # solution_id FK, so "detach" is just the tattoo — the row already
            # survives the Solution delete). Match the install's declared keys in
            # the install's org scope.
            #
            # KNOWN LIMITATION of the keyed-not-FK'd model: a Config VALUE is
            # shared by key, so if another LIVE install in the same org declares
            # the same key, that value backs both installs. We guard the common
            # case by NOT orphaning keys still declared by another live install
            # in this org (leaving the shared value live). The residual edge —
            # two installs declaring the same key where only this one is being
            # removed — is handled; a value mis-stamped despite the guard (e.g.
            # an install added after a partial-failure) would need a manual
            # un-orphan or a re-set in scope (which heals it).
            config_values_orphaned = 0
            still_declared_keys: set[str] = set()
            if decl_keys:
                org_match = (
                    SolutionORM.organization_id == sol.organization_id
                    if sol.organization_id is not None
                    else SolutionORM.organization_id.is_(None)
                )
                still_declared_keys = set(
                    (
                        await ctx.db.execute(
                            select(SolutionConfigSchema.key)
                            .join(
                                SolutionORM,
                                SolutionConfigSchema.solution_id == SolutionORM.id,
                            )
                            .where(
                                SolutionConfigSchema.solution_id != solution_id,
                                SolutionConfigSchema.key.in_(decl_keys),
                                org_match,
                            )
                        )
                    ).scalars().all()
                )

            keys_to_orphan = decl_keys - still_declared_keys
            if keys_to_orphan:
                org_pred = (
                    Config.organization_id == sol.organization_id
                    if sol.organization_id is not None
                    else Config.organization_id.is_(None)
                )
                result = await ctx.db.execute(
                    update(Config)
                    .where(org_pred, Config.key.in_(keys_to_orphan))
                    .values(
                        origin_solution_slug=sol.slug,
                        origin_solution_id=sol.id,
                        orphaned_at=now,
                    )
                )
                config_values_orphaned = result.rowcount or 0

            summary = SolutionDeleteSummary(
                solution_id=solution_id,
                workflows_deleted=await _count(Workflow),
                apps_deleted=len(app_ids),
                forms_deleted=await _count(Form),
                agents_deleted=await _count(Agent),
                claims_deleted=await _count(CustomClaim),
                config_declarations_deleted=len(decl_keys),
                tables_orphaned=len(table_ids),
                config_values_orphaned=config_values_orphaned,
            )

            # Capture the org before the delete — accessing attributes on a
            # deleted+committed instance would trip an expired-attribute refresh.
            sol_org_id = sol.organization_id

            # Solution delete: cascades workflows/apps/forms/agents + the config
            # DECLARATIONS. Tables already have solution_id=NULL, so they are NOT
            # cascaded; config values were never FK-tied to the Solution.
            await ctx.db.delete(sol)
            await ctx.db.commit()

            # The orphan stamp is a Core UPDATE that does NOT go through
            # set_config/upsert_config, so it never bumped the config cache.
            # Without this, merged_for_sdk could keep serving the now-orphaned
            # value (incl. a leftover SECRET) from Redis until TTL. Invalidate
            # the install's org scope so runtime reads re-resolve against the DB.
            if config_values_orphaned:
                from src.core.cache import invalidate_all_config

                await invalidate_all_config(
                    str(sol_org_id) if sol_org_id is not None else None
                )

            # S3 sweep only after the DB is durable (mirrors deploy's DB-then-S3).
            storage = SolutionStorage(solution_id)
            for rel in await storage.list(""):
                await storage.delete(rel)
            builder = SolutionAppBuilder()
            for app_id in app_ids:
                await builder.delete_dist(app_id)
    except SolutionWriteLockHeld as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A write is already in progress for this install; retry shortly.",
        ) from exc
    return summary


@router.post(
    "/{solution_id}/deploy",
    response_model=SolutionDeployResponse,
    summary="Deploy a bundle to an install (full replace, non-interactive, admin only)",
)
async def deploy_solution(
    solution_id: UUID, body: SolutionDeployRequest, ctx: Context, user: CurrentSuperuser
) -> SolutionDeployResponse:
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

    # One writer per install (criterion 6): hold a per-install lock ACROSS the DB
    # commit AND the post-commit S3 finalize, so two concurrent deploys can't
    # interleave (A commits, B commits, then A's finalize uploads last → DB from
    # B but artifacts from A). The app-slug advisory lock inside deploy() is
    # transaction-scoped and releases at commit, before finalize — so it does NOT
    # cover this (Codex #12). The git-connected sync holds the same lock.
    from src.services.solutions.write_lock import (
        SolutionWriteLockHeld,
        solution_write_lock,
    )

    try:
        async with solution_write_lock(solution_id):
            deployer = SolutionDeployer(ctx.db)
            result = await deployer.deploy(
                SolutionBundle(
                    solution=solution,
                    python_files=body.python_files,
                    workflows=body.workflows,
                    tables=body.tables,
                    apps=body.apps,
                    forms=body.forms,
                    agents=body.agents,
                    claims=body.claims,
                    config_schemas=body.config_schemas,
                    connection_schemas=body.connection_schemas,
                    version=body.version,
                    logo_b64=body.logo_b64,
                    logo_content_type=body.logo_content_type,
                    readme=body.readme,
                ),
                force=body.force,
            )
            await ctx.db.commit()
            # S3 only after the DB is durable — a failed commit changes no running
            # code (P1-c). Still inside the lock so finalize can't race another deploy.
            await result.finalize_s3()
    except SolutionWriteLockHeld as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A deploy is already in progress for this install; retry shortly.",
        ) from exc
    except SolutionDowngradeBlocked as exc:
        # The bundle's version is older than installed (Task 20). The caller can
        # re-run with force=true to apply the downgrade deliberately.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SolutionDeployConflict as exc:
        # The bundle is invalid for this install: a foreign/owned entity id, an
        # app-slug collision with a visible app, or a non-standalone_v2 app. These
        # are caller errors → 409 with the reason, not an unhandled 500 (Codex #13).
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SolutionFinalizeIncomplete as exc:
        # Reached only when storage failed every retry (a real outage), not a
        # transient blip. The DB is committed and the deploy is full-replace +
        # idempotent, so re-running heals it; surface 502 so the operator retries.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Deploy committed but storage was unavailable after retries. "
                "Re-run the deploy to complete it (it is idempotent)."
            ),
        ) from exc
    return SolutionDeployResponse(
        solution_id=solution_id,
        workflows_upserted=result.workflows_upserted,
        workflows_deleted=result.workflows_deleted,
        tables_upserted=result.tables_upserted,
        tables_deleted=result.tables_deleted,
        apps_upserted=result.apps_upserted,
        apps_deleted=result.apps_deleted,
        forms_upserted=result.forms_upserted,
        forms_deleted=result.forms_deleted,
        agents_upserted=result.agents_upserted,
        agents_deleted=result.agents_deleted,
        claims_upserted=result.claims_upserted,
        claims_deleted=result.claims_deleted,
        integrations_shell_created=result.integrations_shell_created,
    )


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
    from src.services.solutions.zip_install import preview_zip

    org_id: UUID | None = None
    if organization_id:
        try:
            org_id = UUID(organization_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid organization_id: {organization_id}",
            ) from exc

    data = await file.read()
    try:
        result = preview_zip(data)
    except (ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid solution zip: {exc}",
        ) from exc

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
        if not (root / "bifrost.solution.yaml").is_file():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"No bifrost.solution.yaml at "
                f"{body.repo_subpath or '<repo root>'} in {body.repo_url}",
            )
        result = _parse_workspace(root)
    return await _preview_to_dto(ctx, result, None)


@router.post(
    "/install",
    response_model=SolutionDTO,
    summary="Install a Solution zip (atomic deploy + config values, admin only)",
)
async def install_solution(
    file: Annotated[UploadFile, File(description="Solution workspace zip")],
    ctx: Context,
    user: CurrentSuperuser,
    organization_id: Annotated[str | None, FastapiForm()] = None,
    config_values: Annotated[str, FastapiForm()] = "{}",
    password: Annotated[str | None, FastapiForm()] = None,
    replace_secrets: Annotated[bool, FastapiForm()] = False,
    replace_data: Annotated[bool, FastapiForm()] = False,
    force: bool = False,
) -> SolutionDTO:
    """Atomically install a Solution from a workspace zip.

    Resolves-or-creates the install at the chosen scope (empty/absent
    ``organization_id`` → global NULL), runs the proven deploy under the
    per-install write lock, and — in the same locked section after the S3 finalize
    — applies the provided ``config_values`` (a JSON object of key→value). A
    missing required config does NOT block the install (warn-not-block).

    Full-backup zips carry a ``.bifrost/secrets.enc`` blob; ``password`` is
    required to decrypt it.  A wrong password is refused with 422 before
    anything is written.  If the blob contains values for keys that already
    have a Config row in the target org, the import is refused with 409 unless
    ``replace_secrets=true`` (config values) or ``replace_data=true`` (table
    data, Phase 4).

    A zip whose descriptor ``version`` is OLDER than the installed version is
    refused with 409 (downgrade gate, Task 20) unless ``?force=true``.
    """
    from src.services.solutions.deploy import (
        SolutionDeployConflict,
        SolutionDowngradeBlocked,
        SolutionFinalizeIncomplete,
    )
    from src.services.solutions.write_lock import SolutionWriteLockHeld
    from src.services.solutions.zip_install import (
        BadExportPassword,
        ContentCollision,
        GitConnectedInstallError,
        UnmetDependency,
        install_zip,
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

    data = await file.read()
    try:
        solution = await install_zip(
            ctx.db,
            data,
            organization_id=org_id,
            config_values=values,
            deployer_email=user.email,
            force=force,
            password=password,
            replace_secrets=replace_secrets,
            replace_data=replace_data,
        )
    except UnmetDependency as exc:
        # A bundle imports a modules.X that isn't shipped — refuse before
        # anything lands, naming the missing module.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except BadExportPassword as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ContentCollision as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except GitConnectedInstallError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SolutionDowngradeBlocked as exc:
        # Older descriptor version than installed (Task 20); ?force=true overrides.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid solution zip: {exc}",
        ) from exc
    except SolutionWriteLockHeld as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A deploy is already in progress for this install; retry shortly.",
        ) from exc
    except SolutionDeployConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SolutionFinalizeIncomplete as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Install committed but storage was unavailable after retries. "
                "Re-run the install to complete it (it is idempotent)."
            ),
        ) from exc
    return SolutionDTO.model_validate(solution)
