"""Durable, conservative workflow-permission conversion operations.

The service keeps source parsing behind ``shared.workflow_conversion_analysis``
and stores only its compact output.  It never executes workspace code.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import AsyncExitStack
from datetime import date, datetime, timedelta, timezone
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from botocore.exceptions import ClientError
from fastapi import HTTPException, status
from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.sdk_modules import ModuleSourceScope, resolve_module_name
from shared.runtime_modules import RuntimeModuleInventory, read_runtime_modules
from src.core.database import get_db_context
from src.core.principal import UserPrincipal
from src.jobs.platform.base import (
    PlatformJobCancelled,
    PlatformJobContext,
    PlatformJobFailure,
)
from shared.models import (
    WorkflowConversionAnalyzeRequest,
    WorkflowConversionReport,
    WorkflowConversionReportItem,
    WorkflowConversionReportSummary,
)
from src.models.orm.config import SystemConfig
from src.models.orm.file_metadata import FilePolicy
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_workflow_permission_requests import (
    SolutionWorkflowPermissionRequest,
)
from src.models.orm.solutions import Solution
from src.models.orm.workflow_conversions import (
    WorkflowConversionAnalysisCache,
    WorkflowConversionReport as ReportRow,
)
from src.models.orm.workflow_operation_usage import WorkflowOperationUsage
from src.models.orm.workflow_permissions import WorkflowPermissionGrant
from src.models.orm.tables import Table
from src.models.orm.users import RolePermission, User, UserRole, UserRoleBoundary
from src.models.orm.workflow_roles import WorkflowRole
from src.models.orm.workflows import Workflow
from src.models.contracts.workflow_permissions import (
    RequestedWorkflowPermissions,
    RequestStatus,
    WorkflowPermissionGrantSpec,
    WorkflowPermissionMode,
)
from src.services.audit import emit_audit
from src.services.audit_context import ActorContext
from src.services.authorization.callers import CallerUnavailable, load_human_caller
from src.services.authorization.enforce import Caller, require_operation
from src.services.authorization.evaluator import GLOBAL
from src.services.solutions.write_lock import SolutionWriteLockHeld, solution_write_lock
from src.services.platform_jobs import (
    enqueue_platform_job,
    ensure_platform_job_notification,
    publish_platform_job_update,
)
from src.services.platform_job_access import (
    PlatformJobAccessDenied,
    require_platform_job_access,
)
from src.services.workflow_permissions import (
    DEFAULT_MODE_CONFIG_KEY,
    WORKFLOW_PERMISSIONS_CONFIG_CATEGORY,
    effective_workflow_permission_mode,
    get_default_workflow_permission_mode,
    request_digest,
)

ANALYZE_JOB_TYPE = "workflow.permissions.analyze"
APPLY_JOB_TYPE = "workflow.permissions.apply"
CUTOVER_JOB_TYPE = "workflow.permissions.cutover"
OBSERVATION_WINDOW_DAYS = 30
_WORKFLOW_REVIEW_FIELDS = (
    "id",
    "is_active",
    "path",
    "function_name",
    "access_level",
    "organization_id",
    "solution_id",
    "permission_mode",
    "run_identity_id",
)


def _workflow_review_state(workflow: Workflow) -> tuple:
    """Authority/source metadata; routine API-key usage is not a new proposal."""
    return tuple(getattr(workflow, field) for field in _WORKFLOW_REVIEW_FIELDS)


def _analysis_version() -> str:
    # Imported lazily so route/schema imports do not initialize an AST analyzer.
    from shared.workflow_conversion_analysis import ANALYZER_VERSION

    return ANALYZER_VERSION


def _cache_source_key(source_key: str) -> str:
    """Keep cache identity bounded without retaining a source namespace/path."""
    return hashlib.sha256(source_key.encode()).hexdigest()


async def _source_for_workflow(workflow: Workflow):
    from shared.workflow_conversion_analysis import SourceFile
    from src.services.repo_storage import RepoStorage
    from src.services.solutions.storage import SolutionStorage

    storage = (
        SolutionStorage(workflow.solution_id) if workflow.solution_id else RepoStorage()
    )
    source = (await storage.read(workflow.path)).decode("utf-8")
    storage_path = (
        f"_solutions/{workflow.solution_id}/{workflow.path}"
        if workflow.solution_id
        else workflow.path
    )
    return SourceFile(
        key=storage_path,
        path=workflow.path,
        source=source,
        content_hash=hashlib.sha256(source.encode()).hexdigest(),
    )


def _is_missing_source_error(exc: Exception) -> bool:
    """Recognize only storage providers' ordinary missing-object responses."""
    if isinstance(exc, FileNotFoundError):
        return True
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code")
        return code in {"NoSuchKey", "404", "NotFound", "Not Found"}
    return type(exc).__name__ == "NoSuchKey"


async def _summary(db: AsyncSession, source_file) -> dict:
    # SDK shapes and resource defaults participate in source summaries.
    cache_version = hashlib.sha256(
        f"{_analysis_version()}:{_sdk_permission_fingerprint()}".encode()
    ).hexdigest()
    cached = (
        await db.execute(
            select(WorkflowConversionAnalysisCache).where(
                WorkflowConversionAnalysisCache.source_key
                == _cache_source_key(source_file.key),
                WorkflowConversionAnalysisCache.content_hash
                == source_file.content_hash,
                WorkflowConversionAnalysisCache.analyzer_version == cache_version,
            )
        )
    ).scalar_one_or_none()
    if cached is not None:
        return dict(cached.summary)
    from shared.workflow_conversion_analysis import summarize_source

    compact = summarize_source(source_file.source)
    db.add(
        WorkflowConversionAnalysisCache(
            source_key=_cache_source_key(source_file.key),
            content_hash=source_file.content_hash,
            analyzer_version=cache_version,
            summary=compact,
        )
    )
    await db.flush()
    # Content-addressed summaries are reusable, but a file edited forever must
    # not make this cache an unbounded history store. Reports retain the hash
    # they reviewed; only the eight most recent reusable source versions stay.
    expired = list(
        (
            await db.execute(
                select(WorkflowConversionAnalysisCache.id)
                .where(
                    WorkflowConversionAnalysisCache.source_key
                    == _cache_source_key(source_file.key),
                )
                .order_by(
                    WorkflowConversionAnalysisCache.updated_at.desc(),
                    WorkflowConversionAnalysisCache.id.desc(),
                )
                .offset(8)
            )
        ).scalars()
    )
    if expired:
        await db.execute(
            delete(WorkflowConversionAnalysisCache).where(
                WorkflowConversionAnalysisCache.id.in_(expired)
            )
        )
    return compact


async def _resolve_import(
    source_file,
    module: str,
    level: int,
    *,
    workflow: Workflow,
    allow_outbound_access: bool,
):
    """Use the runtime module resolver's exact Solution-before-repository scope."""
    if level:
        importer = PurePosixPath(source_file.path)
        package_path = importer if source_file.kind == "namespace" else importer.parent
        package_parts = list(package_path.parts)
        # ``from .x`` resolves in the current package; each added dot climbs
        # one parent.  Package initializers themselves are their own package.
        climb = level - 1
        if climb > len(package_parts):
            return None
        base = package_parts[: len(package_parts) - climb] if climb else package_parts
        module = ".".join([*base, *([module] if module else [])])
        if not module:
            return None
    scope = ModuleSourceScope(
        solution_id=str(workflow.solution_id) if workflow.solution_id else None,
        global_repo_access=allow_outbound_access,
    )
    resolved = await resolve_module_name(module, scope=scope)
    kind = resolved.get("kind")
    if kind not in {"module", "package", "namespace"}:
        return None
    from shared.workflow_conversion_analysis import SourceFile

    source_path = resolved.get("path")
    if kind == "namespace" and isinstance(source_path, str):
        storage_key = (
            f"_solutions/{workflow.solution_id}/{source_path}/"
            if workflow.solution_id
            else f"{source_path}/"
        )
        return SourceFile(
            key=storage_key,
            path=source_path,
            source="",
            content_hash=hashlib.sha256(f"namespace:{storage_key}".encode()).hexdigest(),
            kind="namespace",
        )
    content = resolved.get("content")
    storage_path = resolved.get("storage_path")
    if (
        not isinstance(content, str)
        or not isinstance(storage_path, str)
        or not isinstance(source_path, str)
    ):
        return None
    return SourceFile(
        key=storage_path,
        path=source_path,
        source=content,
        content_hash=str(
            resolved.get("hash") or hashlib.sha256(content.encode()).hexdigest()
        ),
        kind=str(kind),
    )


def _permissions_for_observed_operations(
    operations: list[str],
) -> tuple[list[str], list[str]]:
    """Resolve recorded route keys and operation IDs from the same SDK catalog."""
    from shared.workflow_conversion_sdk import permissions_for_recorded_operations

    return permissions_for_recorded_operations(operations)


def _fingerprint(
    *,
    source_hash: str,
    dependencies: dict[str, str],
    workflow: Workflow,
    grants: list[str],
    request: SolutionWorkflowPermissionRequest | None,
    authority: dict,
    policies: dict,
    proposed_permissions: list[str],
    observed_operations: list[str],
    unknowns: list[str],
    runtime_modules_hash: str = "",
) -> str:
    payload = {
        "source_hash": source_hash,
        "dependencies": dict(sorted(dependencies.items())),
        "analyzer_version": _analysis_version(),
        "sdk_permission_fingerprint": _sdk_permission_fingerprint(),
        "runtime_modules_hash": runtime_modules_hash,
        "workflow": {
            field: str(value) if isinstance(value, UUID) else value
            for field, value in zip(
                _WORKFLOW_REVIEW_FIELDS, _workflow_review_state(workflow)
            )
        },
        "grants": sorted(grants),
        "authority": authority,
        "policies": policies,
        "proposed_permissions": sorted(proposed_permissions),
        "observed_operations": sorted(observed_operations),
        "unknowns": sorted(unknowns),
        "solution_request": None
        if request is None
        else {
            "request_digest": request.request_digest,
            "approved_digest": request.approved_digest,
            "status": request.status,
            "requested_mode": request.requested_mode,
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _sdk_permission_fingerprint() -> str:
    from shared.workflow_conversion_sdk import sdk_permission_fingerprint

    return sdk_permission_fingerprint()


async def _authority_snapshot(
    db: AsyncSession, workflow: Workflow
) -> tuple[dict, dict | None]:
    """Capture every durable authorization row this conversion can mutate/use."""
    from shared.run_lineage import unattended_lineage

    lineage = await unattended_lineage(db, workflow.id)
    identity = await db.get(User, lineage.run_user_id)
    role_rows = list(
        (
            await db.execute(
                select(UserRole.role_id).where(UserRole.user_id == lineage.run_user_id)
            )
        ).scalars()
    )
    boundaries = list(
        (
            await db.execute(
                select(
                    UserRoleBoundary.role_id,
                    UserRoleBoundary.kind,
                    UserRoleBoundary.organization_id,
                ).where(UserRoleBoundary.user_id == lineage.run_user_id)
            )
        ).all()
    )
    workflow_roles = list(
        (
            await db.execute(
                select(WorkflowRole.role_id).where(
                    WorkflowRole.workflow_id == workflow.id
                )
            )
        ).scalars()
    )
    role_ids = set(role_rows) | set(workflow_roles)
    if identity is not None:
        role_ids.add(identity.base_role_id)
    role_permissions = (
        list(
            (
                await db.execute(
                    select(RolePermission.role_id, RolePermission.permission).where(
                        RolePermission.role_id.in_(role_ids)
                    )
                )
            ).all()
        )
        if role_ids
        else []
    )
    snapshot = {
        "identity_id": str(lineage.run_user_id),
        "identity_updated_at": None
        if identity is None
        else identity.updated_at.isoformat(),
        "identity_is_active": None if identity is None else identity.is_active,
        "identity_organization_id": None
        if identity is None or identity.organization_id is None
        else str(identity.organization_id),
        "identity_is_external": None if identity is None else identity.is_external,
        "identity_role_ids": sorted(str(value) for value in role_rows),
        "identity_base_role_id": None
        if identity is None
        else str(identity.base_role_id),
        "identity_boundaries": sorted(
            (str(role_id), kind, str(org_id) if org_id else None)
            for role_id, kind, org_id in boundaries
        ),
        "workflow_role_ids": sorted(str(value) for value in workflow_roles),
        "role_permissions": sorted(
            (str(role_id), permission) for role_id, permission in role_permissions
        ),
    }
    public = (
        None
        if identity is None
        else {
            "id": str(identity.id),
            "name": identity.name,
            "identity_kind": identity.identity_kind,
            "organization_id": str(identity.organization_id)
            if identity.organization_id
            else None,
            "shared": workflow.run_identity_id is None,
        }
    )
    return snapshot, public


def _identity_warnings(identity: dict | None, authority: dict) -> list[str]:
    """Describe existing run-identity configuration without blocking conversion."""
    if identity is None:
        return ["The workflow run identity is unavailable."]
    if not authority["identity_is_active"]:
        return ["The workflow run identity is inactive."]
    return []


async def _policy_revision_snapshot(db: AsyncSession, workflow: Workflow) -> dict:
    """Conservatively bind proposals to policy metadata that can affect runs."""

    async def digest_rows(model) -> dict[str, int | str]:
        digest = hashlib.sha256()
        count = 0
        rows = await db.stream(select(model.id, model.updated_at).order_by(model.id))
        async for row_id, updated_at in rows:
            digest.update(str(row_id).encode())
            digest.update(b"\\0")
            digest.update(updated_at.isoformat().encode())
            digest.update(b"\\n")
            count += 1
        await rows.close()
        return {"count": count, "digest": digest.hexdigest()}

    solution = (
        await db.get(Solution, workflow.solution_id) if workflow.solution_id else None
    )
    return {
        "solution_scope": None
        if solution is None
        else {
            "allow_outbound_access": solution.allow_outbound_access,
            "allow_inbound_access": solution.allow_inbound_access,
            "status": solution.status,
            "updated_at": solution.updated_at.isoformat(),
        },
        "workflow_access": [
            workflow.access_level,
            str(workflow.organization_id) if workflow.organization_id else None,
            str(workflow.solution_id) if workflow.solution_id else None,
        ],
        "tables": await digest_rows(Table),
        "file_policies": await digest_rows(FilePolicy),
        "policy_rules": await digest_rows(PolicyRule),
        # Per-call Solution selectors may target an install other than the
        # workflow's own. Its resolution and inbound admission are review inputs.
        "solutions": await digest_rows(Solution),
    }


async def _report_for_workflow(
    db: AsyncSession, workflow: Workflow, *, source=None,
    module_inventory: RuntimeModuleInventory | None = None,
) -> dict:
    source = source or await _source_for_workflow(workflow)
    from shared.workflow_conversion_analysis import analyze_workflow

    if module_inventory is None:
        module_inventory = await read_runtime_modules()
    runtime_modules_hash = hashlib.sha256(json.dumps({
        "roots": sorted(module_inventory.roots),
        "available": module_inventory.available,
    }, sort_keys=True).encode()).hexdigest()
    allow_outbound_access = workflow.solution_id is None or bool(
        await db.scalar(
            select(Solution.allow_outbound_access).where(
                Solution.id == workflow.solution_id
            )
        )
    )
    analysis = await analyze_workflow(
        source,
        workflow.function_name,
        lambda sf, module, level: _resolve_import(
            sf,
            module,
            level,
            workflow=workflow,
            allow_outbound_access=allow_outbound_access,
        ),
        lambda sf: _summary(db, sf),
        external_module_roots=module_inventory.roots,
    )
    cutoff = date.today() - timedelta(days=OBSERVATION_WINDOW_DAYS)
    usages = (
        await db.execute(
            select(
                WorkflowOperationUsage.operation_key,
                func.sum(WorkflowOperationUsage.count),
            )
            .where(
                WorkflowOperationUsage.workflow_id == workflow.id,
                WorkflowOperationUsage.day >= cutoff,
            )
            .group_by(WorkflowOperationUsage.operation_key)
        )
    ).all()
    observed_operations = sorted(str(operation) for operation, count in usages if count)
    observed_permissions, mapping_unknowns = _permissions_for_observed_operations(
        observed_operations
    )
    source_calls = sorted(set(str(call) for call in analysis.get("sdk_calls", [])))
    # The mapping follows the SDK transport bindings and AccessList, maintained
    # separately from this durable job so source analysis cannot invent powers.
    from shared.workflow_conversion_sdk import permissions_for_sdk_calls

    source_permissions, source_unknowns = permissions_for_sdk_calls(
        source_calls, invocations=analysis.get("resource_calls", []),
    )
    request = (
        (
            await db.execute(
                select(SolutionWorkflowPermissionRequest).where(
                    SolutionWorkflowPermissionRequest.workflow_id == workflow.id,
                    SolutionWorkflowPermissionRequest.solution_id
                    == workflow.solution_id,
                )
            )
        ).scalar_one_or_none()
        if workflow.solution_id
        else None
    )
    grants = list(
        (
            await db.execute(
                select(WorkflowPermissionGrant.permission).where(
                    WorkflowPermissionGrant.workflow_id == workflow.id
                )
            )
        ).scalars()
    )
    authority, identity = await _authority_snapshot(db, workflow)
    policies = await _policy_revision_snapshot(db, workflow)
    from shared.workflow_conversion_access import recorded_permission_requirements

    observed_permissions = sorted(set(observed_permissions) | set(
        await recorded_permission_requirements(
            db, workflow.id, window_days=OBSERVATION_WINDOW_DAYS
        )
    ))
    proposed_permissions = sorted(set(source_permissions) | set(observed_permissions))
    unknowns = list(
        dict.fromkeys(
            [
                *analysis.get("unknowns", []),
                *source_unknowns,
                *mapping_unknowns,
            ]
        )
    )
    warnings = list(dict.fromkeys([
        *_identity_warnings(identity, authority),
        *analysis.get("warnings", []),
    ]))
    current_mode = (await effective_workflow_permission_mode(db, workflow)).value
    status_value = (
        "blocked"
        if not analysis["entrypoint_found"]
        else "already_declared"
        if current_mode == "restricted" and not unknowns
        else ("needs_review" if unknowns else "ready")
    )
    report = {
        "workflow_id": str(workflow.id),
        "workflow_name": workflow.display_name or workflow.name,
        "solution_id": str(workflow.solution_id) if workflow.solution_id else None,
        "organization_id": str(workflow.organization_id)
        if workflow.organization_id
        else None,
        "source_fingerprint": _fingerprint(
            source_hash=source.content_hash,
            dependencies=analysis.get("dependencies", {}),
            workflow=workflow,
            grants=grants,
            request=request,
            authority=authority,
            policies=policies,
            proposed_permissions=proposed_permissions,
            observed_operations=observed_operations,
            unknowns=unknowns,
            runtime_modules_hash=runtime_modules_hash,
        ),
        "status": status_value,
        "current_permission_mode": current_mode,
        "proposed_permissions": proposed_permissions,
        "source_permissions": source_permissions,
        "observed_permissions": observed_permissions,
        "observed_operations": observed_operations,
        "evidence": analysis.get("evidence", []),
        "unknowns": unknowns,
        "warnings": warnings,
        "identity": identity,
        "solution_permission_request": None
        if request is None
        else {
            "status": request.status,
            "request_digest": request.request_digest,
            "approved_digest": request.approved_digest,
            "requested_mode": request.requested_mode,
            "requested_permissions": [
                str(item.get("permission")) for item in request.requested_grants
            ],
        },
        "error": None if analysis["entrypoint_found"] else (
            "The registered workflow entry point is unavailable in its current source."
        ),
        # Private durable inputs for an applied-state refresh. They are hashes
        # and compact dependency metadata, never source or AST content.
        "_conversion_source_hash": source.content_hash,
        "_conversion_dependencies": analysis.get("dependencies", {}),
        "_conversion_runtime_modules_hash": runtime_modules_hash,
    }
    return report


async def _blocked_report_for_source(db: AsyncSession, workflow: Workflow) -> dict:
    """Persist a deliberately non-sensitive result for unreadable source."""
    current_mode = (await effective_workflow_permission_mode(db, workflow)).value
    return {
        "workflow_id": str(workflow.id),
        "workflow_name": workflow.display_name or workflow.name,
        "solution_id": str(workflow.solution_id) if workflow.solution_id else None,
        "organization_id": str(workflow.organization_id)
        if workflow.organization_id
        else None,
        "source_fingerprint": hashlib.sha256(
            f"blocked-source:{workflow.id}:{workflow.updated_at.isoformat()}".encode()
        ).hexdigest(),
        "status": "blocked",
        "current_permission_mode": current_mode,
        "proposed_permissions": [],
        "source_permissions": [],
        "observed_permissions": [],
        "observed_operations": [],
        "evidence": [],
        "unknowns": [
            "Workflow source could not be analyzed; review source availability and encoding."
        ],
        "warnings": [],
        "identity": None,
        "solution_permission_request": None,
        "error": "Workflow source could not be analyzed.",
    }


async def _refresh_applied_report(
    db: AsyncSession,
    row: ReportRow,
    workflow: Workflow,
    request: SolutionWorkflowPermissionRequest | None,
) -> dict:
    """Refresh mutable authority after Apply without repeating source/S3 work under a job lock."""
    report = dict(row.report)
    authority, identity = await _authority_snapshot(db, workflow)
    policies = await _policy_revision_snapshot(db, workflow)
    grants = list(
        (
            await db.execute(
                select(WorkflowPermissionGrant.permission).where(
                    WorkflowPermissionGrant.workflow_id == workflow.id
                )
            )
        ).scalars()
    )
    source_hash = report.get("_conversion_source_hash")
    dependencies = report.get("_conversion_dependencies")
    runtime_modules_hash = report.get("_conversion_runtime_modules_hash")
    if (
        not isinstance(source_hash, str)
        or not isinstance(dependencies, dict)
        or not isinstance(runtime_modules_hash, str)
    ):
        raise PlatformJobFailure(
            "report_corrupt", "The saved conversion report cannot be refreshed safely"
        )
    report["current_permission_mode"] = (
        await effective_workflow_permission_mode(db, workflow)
    ).value
    report["identity"] = identity
    report["source_fingerprint"] = _fingerprint(
        source_hash=source_hash,
        dependencies=dependencies,
        workflow=workflow,
        grants=grants,
        request=request,
        authority=authority,
        policies=policies,
        proposed_permissions=report["proposed_permissions"],
        observed_operations=report["observed_operations"],
        unknowns=report["unknowns"],
        runtime_modules_hash=runtime_modules_hash,
    )
    return report


async def _fenced_job(db: AsyncSession, context: PlatformJobContext) -> PlatformJob:
    job = (
        await db.execute(
            select(PlatformJob)
            .where(
                PlatformJob.id == context.job_id,
                PlatformJob.lease_token == context.lease_token,
                PlatformJob.status.in_(("running", "cancel_requested")),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if job is None or job.status == "cancel_requested":
        raise PlatformJobCancelled
    return job


async def _require_current_conversion_admin(
    db: AsyncSession, context: PlatformJobContext, operation: str
) -> Caller:
    """Reauthorize a durable request before it reads or mutates workspace state."""
    try:
        requester = UUID(context.requested_by_user_id)
        caller = await load_human_caller(db, requester)
    except (ValueError, CallerUnavailable) as exc:
        raise PlatformJobFailure(
            "requester_unavailable",
            "The requesting administrator is no longer available",
        ) from exc
    if not caller.is_platform_admin:
        raise PlatformJobFailure(
            "authorization_changed", "Platform administrator permission is required"
        )
    try:
        require_operation(caller, operation, GLOBAL)
    except HTTPException as exc:
        raise PlatformJobFailure(
            "authorization_changed",
            "The requester no longer has permission for workflow conversion",
        ) from exc
    return caller


async def _mark_stale(
    db: AsyncSession, context: PlatformJobContext, row: ReportRow
) -> None:
    await _fenced_job(db, context)
    row.status = "stale"
    row.report = {
        **row.report,
        "status": "stale",
        "error": "Source or authorization changed after review. Analyze this workflow again.",
    }
    await db.commit()


def _conversion_actor(
    context: PlatformJobContext, caller: Caller, requester: UUID
) -> ActorContext:
    return ActorContext(
        user_id=requester,
        organization_id=caller.principal.organization_id,
        email=context.requested_by_email,
        name=context.requested_by_name,
        source="scheduler",
        surface="web",
    )


async def run_analysis(context: PlatformJobContext, payload) -> dict:
    module_inventory = await read_runtime_modules()
    state = dict(context.checkpoint or {"completed": 0})
    selected_ids = list(payload.workflow_ids) if payload.workflow_ids else None
    if selected_ids is not None:
        state.setdefault("selected_ids", [str(value) for value in selected_ids])
    else:
        # A frozen upper timestamp ensures workflows created after this request
        # do not silently join a resumed analysis or its later cutover review.
        state.setdefault("snapshot_before", datetime.now(timezone.utc).isoformat())
    await context.save_checkpoint(state, phase="Preparing workflow inventory")
    while True:
        async with get_db_context() as db:
            await _require_current_conversion_admin(
                db, context, "workflows.conversions.create"
            )
            query = (
                select(Workflow.id)
                .where(Workflow.is_active.is_(True))
                .order_by(Workflow.created_at, Workflow.id)
                .limit(100)
            )
            if selected_ids is not None:
                query = query.where(Workflow.id.in_(selected_ids))
            else:
                query = query.where(
                    Workflow.created_at
                    <= datetime.fromisoformat(state["snapshot_before"])
                )
                if payload.scope == "global":
                    query = query.where(Workflow.organization_id.is_(None))
                elif payload.scope not in (None, "all"):
                    query = query.where(Workflow.organization_id == UUID(payload.scope))
            if "total" not in state:
                state["total"] = int(
                    (
                        await db.execute(
                            select(func.count()).select_from(
                                query.order_by(None).limit(None).subquery()
                            )
                        )
                    ).scalar_one()
                )
            if state.get("cursor_created_at"):
                cursor_time = datetime.fromisoformat(state["cursor_created_at"])
                cursor_id = UUID(state["cursor_id"])
                query = query.where(
                    or_(
                        Workflow.created_at > cursor_time,
                        and_(
                            Workflow.created_at == cursor_time, Workflow.id > cursor_id
                        ),
                    )
                )
            batch = list((await db.execute(query)).scalars())
        if not batch:
            break
        for workflow_id in batch:
            async with get_db_context() as db:
                workflow = await db.get(Workflow, workflow_id)
                if workflow is None:
                    continue
                try:
                    source = await _source_for_workflow(workflow)
                except UnicodeDecodeError:
                    report = await _blocked_report_for_source(db, workflow)
                except (FileNotFoundError, ClientError) as exc:
                    if not _is_missing_source_error(exc):
                        raise
                    report = await _blocked_report_for_source(db, workflow)
                else:
                    report = await _report_for_workflow(
                        db, workflow, source=source, module_inventory=module_inventory,
                    )
                await _fenced_job(db, context)
                row = (
                    await db.execute(
                        select(ReportRow)
                        .where(
                            ReportRow.job_id == context.job_id,
                            ReportRow.workflow_id == workflow.id,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if row is None:
                    db.add(
                        ReportRow(
                            job_id=context.job_id,
                            workflow_id=workflow.id,
                            source_fingerprint=report["source_fingerprint"],
                            status=report["status"],
                            report=report,
                        )
                    )
                else:
                    row.source_fingerprint, row.status, row.report = (
                        report["source_fingerprint"],
                        report["status"],
                        report,
                    )
                state["completed"] += 1
                state["cursor_created_at"] = workflow.created_at.isoformat()
                state["cursor_id"] = str(workflow.id)
                await db.commit()
            await context.save_checkpoint(state, phase="Analyzing workflows")
            total = state["total"]
            await context.report(
                "Analyzing workflows",
                state["completed"],
                total,
                100 * state["completed"] / total if total else 100,
            )
    return {
        "completed": state["completed"],
        "platform_cutover": payload.platform_cutover,
        "snapshot_before": state.get("snapshot_before"),
    }


async def run_apply(context: PlatformJobContext, payload) -> dict:
    module_inventory = await read_runtime_modules()
    try:
        requester = UUID(context.requested_by_user_id)
    except ValueError as exc:
        raise PlatformJobFailure(
            "requester_invalid", "The requesting administrator is no longer available"
        ) from exc
    completed: list[str] = list((context.checkpoint or {}).get("completed", []))
    for selection in payload.selections:
        if str(selection.workflow_id) in completed:
            continue
        async with get_db_context() as db, AsyncExitStack() as solution_writes:
            try:
                caller = await _require_current_conversion_admin(
                    db, context, "workflows.conversions.update"
                )
            except PlatformJobFailure as exc:
                if exc.result is None:
                    exc.result = {"completed": completed}
                raise
            workflow = (
                await db.execute(
                    select(Workflow)
                    .where(Workflow.id == selection.workflow_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            report_row = (
                await db.execute(
                    select(ReportRow)
                    .where(
                        ReportRow.job_id == payload.analysis_job_id,
                        ReportRow.workflow_id == selection.workflow_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if workflow is None or report_row is None:
                raise PlatformJobFailure(
                    "report_missing",
                    "A selected workflow no longer has an analysis report",
                    result={"completed": completed},
                )
            if report_row.status == "blocked":
                raise PlatformJobFailure(
                    "blocked_report",
                    "The selected workflow source could not be analyzed; resolve it and analyze again",
                    result={"completed": completed},
                )
            if (report_row.review or {}).get(
                "applied_selection_fingerprint"
            ) == selection.source_fingerprint:
                completed.append(str(selection.workflow_id))
                continue
            if (
                report_row.status == "stale"
                or report_row.source_fingerprint != selection.source_fingerprint
            ):
                raise PlatformJobFailure(
                    "stale_report",
                    "The selected workflow report is stale; analyze it again",
                    result={"completed": completed},
                )
            if workflow.solution_id is not None:
                try:
                    await solution_writes.enter_async_context(
                        solution_write_lock(workflow.solution_id)
                    )
                except SolutionWriteLockHeld as exc:
                    raise PlatformJobFailure(
                        "solution_busy",
                        "A Solution deployment is in progress; wait for it to finish and review the proposal again",
                        result={"completed": completed},
                    ) from exc
            current = await _report_for_workflow(db, workflow, module_inventory=module_inventory)
            if current["source_fingerprint"] != selection.source_fingerprint:
                await _mark_stale(db, context, report_row)
                raise PlatformJobFailure(
                    "stale_report",
                    "Workflow source, dependencies, identity, grants, or Solution consent changed; analyze it again",
                    result={"completed": completed},
                )
            if current["unknowns"] and not selection.acknowledged_unknowns:
                raise PlatformJobFailure(
                    "unresolved_code",
                    "Unresolved source analysis must be explicitly acknowledged",
                    result={"completed": completed},
                )
            solution_request = None
            if workflow.solution_id is not None:
                solution_request = (
                    await db.execute(
                        select(SolutionWorkflowPermissionRequest)
                        .where(
                            SolutionWorkflowPermissionRequest.solution_id
                            == workflow.solution_id,
                            SolutionWorkflowPermissionRequest.workflow_id
                            == workflow.id,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if not payload.acknowledged_solution_requests:
                    raise PlatformJobFailure(
                        "solution_consent_required",
                        "Applying a Solution workflow requires acknowledgement of its reviewed permission declaration",
                        result={"completed": completed},
                    )
            await _fenced_job(db, context)
            if workflow.solution_id is None:
                workflow.permission_mode = "restricted"
                await db.execute(
                    WorkflowPermissionGrant.__table__.delete().where(
                        WorkflowPermissionGrant.workflow_id == workflow.id
                    )
                )
                for permission in current["proposed_permissions"]:
                    db.add(
                        WorkflowPermissionGrant(
                            workflow_id=workflow.id,
                            permission=permission,
                            created_by=context.requested_by_email,
                        )
                    )
            else:
                # Only this reauthorized, fenced administrator decision owns
                # these instance settings. Keep the ordinary Solution ORM
                # write guard intact for every other mutation surface.
                await db.execute(
                    update(Workflow)
                    .where(
                        Workflow.id == workflow.id,
                        Workflow.solution_id == workflow.solution_id,
                    )
                    .values(
                        permission_mode="restricted",
                    )
                )
                await db.refresh(workflow)
                # The fresh request and approval are one transaction under the
                # job lease. This is an explicit administrator decision over
                # the exact reviewed proposal, never a reuse of stale consent.
                requested = RequestedWorkflowPermissions(
                    mode=WorkflowPermissionMode.RESTRICTED,
                    grants=[
                        WorkflowPermissionGrantSpec(permission=value)
                        for value in current["proposed_permissions"]
                    ],
                )
                digest = request_digest(requested)
                grants_json = [
                    {"permission": grant.permission} for grant in requested.grants
                ]
                if solution_request is None:
                    solution_request = SolutionWorkflowPermissionRequest(
                        solution_id=workflow.solution_id,
                        workflow_id=workflow.id,
                        requested_mode=requested.mode.value,
                        requested_grants=grants_json,
                        request_digest=digest,
                        status=RequestStatus.APPROVED.value,
                        approved_digest=digest,
                        decided_by=context.requested_by_email,
                        decided_at=datetime.now(timezone.utc),
                    )
                    db.add(solution_request)
                else:
                    solution_request.requested_mode = requested.mode.value
                    solution_request.requested_grants = grants_json
                    solution_request.request_digest = digest
                    solution_request.status = RequestStatus.APPROVED.value
                    solution_request.approved_digest = digest
                    solution_request.decided_by = context.requested_by_email
                    solution_request.decided_at = datetime.now(timezone.utc)
                await emit_audit(
                    db,
                    "workflow.permission_conversion.solution_approved",
                    resource_type="workflow",
                    resource_id=workflow.id,
                    details={
                        "solution_id": str(workflow.solution_id),
                        "request_digest": digest,
                        "permissions": current["proposed_permissions"],
                    },
                    actor_override=_conversion_actor(context, caller, requester),
                )
            await emit_audit(
                db,
                "workflow.permission_conversion.apply",
                resource_type="workflow",
                resource_id=workflow.id,
                details={
                    "analysis_job_id": str(payload.analysis_job_id),
                    "selection_fingerprint": selection.source_fingerprint,
                    "proposed_permission_count": len(current["proposed_permissions"]),
                    "acknowledged_unknowns": selection.acknowledged_unknowns,
                },
                actor_override=_conversion_actor(context, caller, requester),
            )
            # Queries in the authority refresh must observe the identity,
            # grants, and consent just staged in this transaction.
            await db.flush()
            updated = await _refresh_applied_report(
                db, report_row, workflow, solution_request
            )
            updated["status"] = "already_declared"
            report_row.source_fingerprint = updated["source_fingerprint"]
            report_row.status = "already_declared"
            report_row.report = updated
            report_row.review = {
                "applied": True,
                "applied_selection_fingerprint": selection.source_fingerprint,
                "acknowledged_unknowns": selection.acknowledged_unknowns,
            }
            await db.commit()
        completed.append(str(selection.workflow_id))
        await context.save_checkpoint(
            {"completed": completed}, phase="Applying reviewed workflows"
        )
        await context.report(
            "Applying reviewed workflows",
            len(completed),
            len(payload.selections),
            100 * len(completed) / len(payload.selections),
        )
    return {"completed": completed}


async def run_cutover(context: PlatformJobContext, payload) -> dict:
    module_inventory = await read_runtime_modules()
    async with get_db_context() as db:
        caller = await _require_current_conversion_admin(
            db, context, "workflows.permission_default.update"
        )
        analysis = await db.get(PlatformJob, payload.analysis_job_id)
        if (
            analysis is None
            or analysis.job_type != ANALYZE_JOB_TYPE
            or not bool(analysis.payload.get("platform_cutover"))
        ):
            raise PlatformJobFailure(
                "cutover_not_requested",
                "Cutover requires a Settings-origin conversion analysis",
            )
        if analysis.status != "succeeded":
            raise PlatformJobFailure(
                "analysis_incomplete",
                "Cutover requires a completed conversion analysis",
            )
        if not payload.acknowledged_remaining_exceptions:
            raise PlatformJobFailure(
                "cutover_not_acknowledged",
                "Platform cutover requires explicit acknowledgement",
            )
        active_ids = set(
            (
                await db.execute(
                    select(Workflow.id).where(Workflow.is_active.is_(True))
                )
            ).scalars()
        )
        reported_ids = set(
            (
                await db.execute(
                    select(ReportRow.workflow_id).where(ReportRow.job_id == analysis.id)
                )
            ).scalars()
        )
        if active_ids != reported_ids:
            raise PlatformJobFailure(
                "cutover_inventory_changed",
                "The active workflow inventory changed after analysis; run a new platform cutover analysis",
                result={"unreviewed_workflows": len(active_ids - reported_ids)},
            )
        unresolved = 0
        full_or_inherited = 0
        checked_revisions = {}
        # Re-read source/dependency/identity/consent fingerprints in bounded
        # pages before changing the inherited default. A report is a proposal,
        # never authority to cut over after its inputs changed.
        ordered_ids = sorted(active_ids, key=str)
        for start in range(0, len(ordered_ids), 100):
            batch_ids = ordered_ids[start : start + 100]
            workflows = list(
                (
                    await db.execute(select(Workflow).where(Workflow.id.in_(batch_ids)))
                ).scalars()
            )
            reports = {
                row.workflow_id: row
                for row in (
                    await db.execute(
                        select(ReportRow).where(
                            ReportRow.job_id == analysis.id,
                            ReportRow.workflow_id.in_(batch_ids),
                        )
                    )
                ).scalars()
            }
            for workflow in workflows:
                checked_revisions[workflow.id] = _workflow_review_state(workflow)
                row = reports[workflow.id]
                current = await _report_for_workflow(db, workflow, module_inventory=module_inventory)
                if current["source_fingerprint"] != row.source_fingerprint:
                    await _mark_stale(db, context, row)
                    raise PlatformJobFailure(
                        "stale_report",
                        "Workflow source, dependency, identity, or consent changed; run a new platform cutover analysis",
                        result={"workflow_id": str(workflow.id)},
                    )
                review = row.review or {}
                if current["unknowns"] and not review.get("acknowledged_unknowns"):
                    unresolved += 1
                if (
                    await effective_workflow_permission_mode(db, workflow)
                ).value == "full":
                    full_or_inherited += 1
        if unresolved:
            raise PlatformJobFailure(
                "cutover_unresolved",
                "The analysis still has unresolved workflows",
                result={"unresolved": unresolved},
            )
        if full_or_inherited:
            raise PlatformJobFailure(
                "cutover_blocked",
                "Active workflows still effectively use Full Access",
                result={"remaining": full_or_inherited},
            )
        # Source analysis can take minutes. Freeze registry writes only for
        # this short final check + default commit, so a newly added inherited
        # workflow cannot slip into an already reviewed platform inventory.
        await db.execute(text("LOCK TABLE workflows IN SHARE MODE"))
        final_revisions = {
            row[0]: tuple(row)
            for row in (
                await db.execute(
                    select(
                        *(getattr(Workflow, field) for field in _WORKFLOW_REVIEW_FIELDS)
                    ).where(Workflow.is_active.is_(True))
                )
            ).all()
        }
        if final_revisions != checked_revisions:
            raise PlatformJobFailure(
                "cutover_inventory_changed",
                "The active workflow inventory changed during verification; run a new platform cutover analysis",
            )
        await _fenced_job(db, context)
        setting = (
            await db.execute(
                select(SystemConfig)
                .where(
                    SystemConfig.category == WORKFLOW_PERMISSIONS_CONFIG_CATEGORY,
                    SystemConfig.key == DEFAULT_MODE_CONFIG_KEY,
                    SystemConfig.organization_id.is_(None),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if setting is None:
            setting = SystemConfig(
                category=WORKFLOW_PERMISSIONS_CONFIG_CATEGORY,
                key=DEFAULT_MODE_CONFIG_KEY,
                organization_id=None,
                value_json={"mode": "restricted"},
                created_by=context.requested_by_email,
                updated_by=context.requested_by_email,
            )
            db.add(setting)
        else:
            setting.value_json = {"mode": "restricted"}
            setting.updated_by = context.requested_by_email
        await emit_audit(
            db,
            "workflow.permission_conversion.cutover",
            resource_type="system_config",
            details={"analysis_job_id": str(analysis.id), "default_mode": "restricted"},
            actor_override=_conversion_actor(
                context, caller, UUID(context.requested_by_user_id)
            ),
        )
        await db.commit()
    return {"default_mode": "restricted"}


async def enqueue_analysis(
    db: AsyncSession,
    user: UserPrincipal,
    request: WorkflowConversionAnalyzeRequest,
    definition,
):
    if request.workflow_ids is not None:
        rows = list(
            (
                await db.execute(
                    select(Workflow.id).where(
                        Workflow.id.in_(request.workflow_ids),
                        Workflow.is_active.is_(True),
                    )
                )
            ).scalars()
        )
        if set(rows) != set(request.workflow_ids):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Every selected workflow must exist and be active",
            )
    elif request.scope not in (None, "all", "global"):
        try:
            scope_id = UUID(request.scope)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="scope must be all, global, or an organization UUID",
            ) from exc
        # Validate the scope early; an empty organization scope is still a
        # deliberate valid inventory selection.
        request = request.model_copy(update={"scope": str(scope_id)})
    job, reused = await enqueue_platform_job(
        db,
        definition,
        request.model_dump(mode="json"),
        dedupe_key=hashlib.sha256(request.model_dump_json().encode()).hexdigest(),
        resource_lock_key="workflow-permission-conversion",
        priority=300,
        organization_id=None,
        requested_by_user_id=user.user_id,
        requested_by_email=user.email,
        requested_by_name=user.name or user.email or "Unknown",
        resource_type="workflow_conversion",
        resource_id="analysis",
        title="Analyze workflow permissions",
        action_url="/workflows/convert?job={job_id}",
    )
    job.action_url = f"/workflows/convert?job={job.id}"
    if job.notification_id is None:
        await ensure_platform_job_notification(db, job)
    await db.commit()
    await db.refresh(job)
    await publish_platform_job_update(job)
    return job, reused


def _followup_dedupe_key(analysis_job_id: UUID, job_type: str, request) -> str:
    canonical = {
        "analysis_job_id": str(analysis_job_id),
        "job_type": job_type,
        "request": request.model_dump(mode="json"),
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def enqueue_followup(
    db: AsyncSession,
    user: UserPrincipal,
    analysis_job: PlatformJob,
    request,
    definition,
    *,
    title: str,
):
    try:
        caller = await load_human_caller(db, user.user_id)
        require_platform_job_access(caller, analysis_job)
    except (CallerUnavailable, PlatformJobAccessDenied) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversion analysis not found",
        ) from exc
    followup_id = uuid4()
    job, reused = await enqueue_platform_job(
        db,
        definition,
        {"analysis_job_id": str(analysis_job.id), **request.model_dump(mode="json")},
        dedupe_key=_followup_dedupe_key(analysis_job.id, definition.job_type, request),
        resource_lock_key="workflow-permission-conversion",
        priority=400,
        organization_id=None,
        requested_by_user_id=user.user_id,
        requested_by_email=user.email,
        requested_by_name=user.name or user.email or "Unknown",
        resource_type="workflow_conversion",
        resource_id=str(analysis_job.id),
        title=title,
        action_url=f"/workflows/convert?job={analysis_job.id}&operation={followup_id}",
        job_id=followup_id,
    )
    try:
        require_platform_job_access(caller, job)
    except PlatformJobAccessDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversion operation not found",
        ) from exc
    if job.notification_id is None:
        await ensure_platform_job_notification(db, job)
    await db.commit()
    await db.refresh(job)
    await publish_platform_job_update(job)
    return job, reused


async def get_report(
    db: AsyncSession, caller: Caller, job_id: UUID, *, offset: int, limit: int
) -> WorkflowConversionReport:
    job = await db.get(PlatformJob, job_id)
    if job is None or job.job_type != ANALYZE_JOB_TYPE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversion report not found"
        )
    try:
        require_platform_job_access(caller, job)
    except PlatformJobAccessDenied:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversion report not found"
        ) from None
    total = (
        await db.execute(
            select(func.count())
            .select_from(ReportRow)
            .where(ReportRow.job_id == job_id)
        )
    ).scalar_one()
    rows = list(
        (
            await db.execute(
                select(ReportRow)
                .where(ReportRow.job_id == job_id)
                .order_by(ReportRow.created_at, ReportRow.id)
                .offset(offset)
                .limit(limit)
            )
        ).scalars()
    )
    items = [
        WorkflowConversionReportItem.model_validate(
            {**row.report, "review": row.review}
        )
        for row in rows
    ]
    # Count from all rows, not only this page.
    all_counts = dict(
        (
            await db.execute(
                select(ReportRow.status, func.count())
                .where(ReportRow.job_id == job_id)
                .group_by(ReportRow.status)
            )
        ).all()
    )
    remaining_full = (
        await db.execute(
            select(func.count())
            .select_from(ReportRow)
            .where(
                ReportRow.job_id == job_id,
                ReportRow.report["current_permission_mode"].astext == "full",
            )
        )
    ).scalar_one()
    unresolved = sum(
        int(all_counts.get(name, 0)) for name in {"needs_review", "blocked", "stale"}
    )
    active_total = (
        await db.execute(
            select(func.count())
            .select_from(Workflow)
            .where(Workflow.is_active.is_(True))
        )
    ).scalar_one()
    reported_active_total = (
        await db.execute(
            select(func.count())
            .select_from(ReportRow)
            .join(Workflow, Workflow.id == ReportRow.workflow_id)
            .where(ReportRow.job_id == job_id, Workflow.is_active.is_(True))
        )
    ).scalar_one()
    inventory_complete = (
        job.status == "succeeded"
        and total == active_total
        and reported_active_total == active_total
    )
    default_mode = await get_default_workflow_permission_mode(db)
    return WorkflowConversionReport(
        job_id=job_id,
        total=total,
        offset=offset,
        limit=limit,
        items=items,
        summary=WorkflowConversionReportSummary(
            platform_cutover=bool(job.payload.get("platform_cutover")),
            analyzer_version=_analysis_version(),
            total=total,
            default_permission_mode=default_mode.value,
            remaining_full=remaining_full,
            cutover_ready=bool(job.payload.get("platform_cutover"))
            and inventory_complete
            and total > 0
            and not remaining_full
            and not unresolved,
            **{
                key: int(value)
                for key, value in all_counts.items()
                if key
                in {"ready", "needs_review", "blocked", "already_declared", "stale"}
            },
        ),
    )
