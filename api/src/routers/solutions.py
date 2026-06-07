"""REST endpoints for Solutions — installable surfaces (success-criteria §3).

An install is created here, then deployed via ``POST /{id}/deploy`` (the single
writer for a disconnected install). Deploy is a full replace by contract and is
non-interactive — it always applies the whole bundle.

Solution-management itself is an admin operation; the deployed *entities* are
what end users see (the Solution is invisible to them — criterion 16).
"""

from __future__ import annotations

import json
import logging
import zipfile
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi import Form as FastapiForm
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from src.core.auth import Context, CurrentSuperuser
from src.models.contracts.solutions import (
    Solution as SolutionDTO,
    SolutionConfigStatus,
    SolutionCreate,
    SolutionDeleteSummary,
    SolutionDeployRequest,
    SolutionDeployResponse,
    SolutionEntities,
    SolutionEntitySummary,
    SolutionInstallPreview,
    SolutionsList,
    SolutionUpdate,
)
from src.models.orm.agents import Agent
from src.models.orm.applications import Application
from src.models.orm.config import Config
from src.models.orm.forms import Form
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solutions import Solution as SolutionORM
from src.models.orm.tables import Table
from src.models.orm.workflows import Workflow
from src.services.solutions.deploy import (
    SolutionBundle,
    SolutionDeployer,
    SolutionDeployConflict,
    SolutionFinalizeIncomplete,
)

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

    async def _summaries(model: type) -> list[SolutionEntitySummary]:
        rows = (
            await ctx.db.execute(
                select(model.id, model.name).where(model.solution_id == solution_id)
            )
        ).all()
        return [SolutionEntitySummary(id=id_, name=name) for id_, name in rows]

    workflows = await _summaries(Workflow)
    apps = await _summaries(Application)
    forms = await _summaries(Form)
    agents = await _summaries(Agent)
    tables = await _summaries(Table)

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
        tables=tables,
        configs=configs,
        required_configs_unset=required_unset,
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
                for model in (Workflow, Application, Form, Agent, Table):
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
            config_values_orphaned = 0
            if decl_keys:
                org_pred = (
                    Config.organization_id == sol.organization_id
                    if sol.organization_id is not None
                    else Config.organization_id.is_(None)
                )
                result = await ctx.db.execute(
                    update(Config)
                    .where(org_pred, Config.key.in_(decl_keys))
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
                config_declarations_deleted=len(decl_keys),
                tables_orphaned=len(table_ids),
                config_values_orphaned=config_values_orphaned,
            )

            # Solution delete: cascades workflows/apps/forms/agents + the config
            # DECLARATIONS. Tables already have solution_id=NULL, so they are NOT
            # cascaded; config values were never FK-tied to the Solution.
            await ctx.db.delete(sol)
            await ctx.db.commit()

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
                    config_schemas=body.config_schemas,
                )
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


@router.post(
    "/install/preview",
    response_model=SolutionInstallPreview,
    summary="Preview a Solution install zip (parse-only, admin only)",
)
async def install_preview(
    file: Annotated[UploadFile, File(description="Solution workspace zip")],
    ctx: Context,
    user: CurrentSuperuser,
) -> SolutionInstallPreview:
    """Unzip + parse a Solution workspace zip and report what it would create.

    Parse-only: no DB write, no S3, no build. The drag-and-drop UI calls this to
    show the install plan + declared configs before committing.
    """
    from src.services.solutions.zip_install import preview_zip

    data = await file.read()
    try:
        result = preview_zip(data)
    except (ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid solution zip: {exc}",
        ) from exc
    return SolutionInstallPreview(
        slug=result.slug,
        name=result.name,
        scope=result.scope,  # type: ignore[arg-type]
        workflows=result.workflows,
        tables=result.tables,
        apps=result.apps,
        forms=result.forms,
        agents=result.agents,
        config_schemas=result.config_schemas,
    )


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
) -> SolutionDTO:
    """Atomically install a Solution from a workspace zip.

    Resolves-or-creates the install at the chosen scope (empty/absent
    ``organization_id`` → global NULL), runs the proven deploy under the
    per-install write lock, and — in the same locked section after the S3 finalize
    — applies the provided ``config_values`` (a JSON object of key→value). A
    missing required config does NOT block the install (warn-not-block).
    """
    from src.services.solutions.deploy import (
        SolutionDeployConflict,
        SolutionFinalizeIncomplete,
    )
    from src.services.solutions.write_lock import SolutionWriteLockHeld
    from src.services.solutions.zip_install import (
        GitConnectedInstallError,
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
        )
    except GitConnectedInstallError as exc:
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
