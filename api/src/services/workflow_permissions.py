"""Workflow capabilities and versioned Solution permission requests.

Runtime authority loads the effective mode and permission-only capabilities
from the exact durable workflow. Solution approvals bind their canonical digest.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.workflow_permissions import (
    RequestedWorkflowPermissions,
    RequestStatus,
    WorkflowGrant,
    WorkflowPermissionMode,
)
from src.models.orm.config import SystemConfig
from src.models.orm.solution_workflow_permission_requests import (
    SolutionWorkflowPermissionRequest,
)
from src.models.orm.workflow_permissions import WorkflowPermissionGrant
from src.models.orm.workflows import Workflow

DEFAULT_WORKFLOW_PERMISSION_MODE = WorkflowPermissionMode.FULL

WORKFLOW_PERMISSIONS_CONFIG_CATEGORY = "workflow_permissions"
DEFAULT_MODE_CONFIG_KEY = "default_mode"


async def get_default_workflow_permission_mode(
    db: AsyncSession,
) -> WorkflowPermissionMode:
    """The platform's "Default workflow permissions" setting."""
    config = (
        (
            await db.execute(
                select(SystemConfig).where(
                    SystemConfig.category == WORKFLOW_PERMISSIONS_CONFIG_CATEGORY,
                    SystemConfig.key == DEFAULT_MODE_CONFIG_KEY,
                    SystemConfig.organization_id.is_(None),
                )
            )
        )
        .scalars()
        .first()
    )
    if config is None or not config.value_json:
        return DEFAULT_WORKFLOW_PERMISSION_MODE
    return WorkflowPermissionMode(config.value_json["mode"])


def resolve_workflow_permission_mode(
    workflow: Workflow,
    default: WorkflowPermissionMode,
    permission_request: SolutionWorkflowPermissionRequest | None = None,
) -> WorkflowPermissionMode:
    """An explicit Solution restriction never inherits the Full default."""
    if permission_request is not None and (
        workflow.solution_id is not None
        and permission_request.workflow_id == workflow.id
        and permission_request.solution_id == workflow.solution_id
    ):
        if permission_request.requested_mode == WorkflowPermissionMode.RESTRICTED.value:
            return WorkflowPermissionMode.RESTRICTED
        approved = _approved_permissions(permission_request)
        if approved is not None:
            return approved.mode
    if workflow.permission_mode is not None:
        return WorkflowPermissionMode(workflow.permission_mode)
    return default


def _approved_permissions(
    permission_request: SolutionWorkflowPermissionRequest,
) -> RequestedWorkflowPermissions | None:
    """Only current, canonically bound consent contributes capabilities."""
    if (
        permission_request.status != RequestStatus.APPROVED.value
        or permission_request.approved_digest != permission_request.request_digest
    ):
        return None
    try:
        requested = RequestedWorkflowPermissions.model_validate(
            {
                "mode": permission_request.requested_mode,
                "grants": permission_request.requested_grants,
            }
        )
    except ValidationError:
        return None
    return (
        requested
        if request_digest(requested) == permission_request.approved_digest
        else None
    )


async def workflow_permission_requests(
    db: AsyncSession,
    workflows: Sequence[Workflow],
) -> dict[UUID, SolutionWorkflowPermissionRequest]:
    """Load consent for each workflow's exact owning install in one query."""
    installed = {
        workflow.id: workflow.solution_id
        for workflow in workflows
        if workflow.solution_id is not None
    }
    if not installed:
        return {}
    rows = (
        (
            await db.execute(
                select(SolutionWorkflowPermissionRequest)
                .where(
                    SolutionWorkflowPermissionRequest.solution_id.in_(
                        set(installed.values())
                    ),
                    SolutionWorkflowPermissionRequest.workflow_id.in_(installed),
                )
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return {
        row.workflow_id: row
        for row in rows
        if row.solution_id == installed[row.workflow_id]
    }


async def effective_workflow_permission_mode(
    db: AsyncSession, workflow: Workflow
) -> WorkflowPermissionMode:
    requests = await workflow_permission_requests(db, [workflow])
    return resolve_workflow_permission_mode(
        workflow,
        await get_default_workflow_permission_mode(db),
        requests.get(workflow.id),
    )


async def load_workflow_permissions(
    db: AsyncSession,
    workflow: Workflow,
) -> tuple[WorkflowPermissionMode, list[WorkflowGrant]]:
    """Resolve mode and added powers from current durable consent together."""
    requests = await workflow_permission_requests(db, [workflow])
    permission_request = requests.get(workflow.id)
    mode = resolve_workflow_permission_mode(
        workflow, await get_default_workflow_permission_mode(db), permission_request
    )
    if mode is WorkflowPermissionMode.FULL:
        return mode, []
    if workflow.solution_id is not None:
        approved = (
            None
            if permission_request is None
            else _approved_permissions(permission_request)
        )
        grants = (
            []
            if approved is None
            else [
                WorkflowGrant(permission=grant.permission) for grant in approved.grants
            ]
        )
        return mode, grants
    return mode, await list_workflow_grants(db, workflow.id)


async def list_workflow_grants(
    db: AsyncSession, workflow_id: UUID
) -> list[WorkflowGrant]:
    rows = (
        (
            await db.execute(
                select(WorkflowPermissionGrant).where(
                    WorkflowPermissionGrant.workflow_id == workflow_id
                )
            )
        )
        .scalars()
        .all()
    )
    return [WorkflowGrant(permission=row.permission) for row in rows]


def _grants_json(requested: RequestedWorkflowPermissions) -> list[dict[str, str]]:
    """Grants as stored JSON, sorted so manifest order never matters."""
    return sorted(
        ({"permission": grant.permission} for grant in requested.grants),
        key=lambda grant: grant["permission"],
    )


def request_digest(requested: RequestedWorkflowPermissions) -> str:
    canonical = json.dumps(
        {"mode": requested.mode.value, "grants": _grants_json(requested)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def sync_solution_permission_requests(
    db: AsyncSession,
    *,
    solution_id: UUID,
    requests: dict[UUID, RequestedWorkflowPermissions],
) -> None:
    """Reconcile an install's permission requests with what its bundle declares.

    ``requests`` maps each deployed workflow that declares a request to that
    request. Non-destructive: an unchanged request keeps its approval; a changed
    one is pending again unless it matches the digest an admin already approved;
    a request no longer declared is deleted.
    """
    existing = {
        row.workflow_id: row
        for row in (
            await db.execute(
                select(SolutionWorkflowPermissionRequest)
                .where(SolutionWorkflowPermissionRequest.solution_id == solution_id)
                .order_by(SolutionWorkflowPermissionRequest.workflow_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }

    stale = existing.keys() - requests.keys()
    if stale:
        restricted = [
            wf_id for wf_id in stale if existing[wf_id].requested_mode == "restricted"
        ]
        if restricted:
            await db.execute(
                update(Workflow)
                .where(Workflow.id.in_(restricted), Workflow.solution_id == solution_id)
                .values(permission_mode="restricted")
            )
        await db.execute(
            delete(SolutionWorkflowPermissionRequest).where(
                SolutionWorkflowPermissionRequest.solution_id == solution_id,
                SolutionWorkflowPermissionRequest.workflow_id.in_(stale),
            )
        )

    for workflow_id, requested in requests.items():
        digest = request_digest(requested)
        row = existing.get(workflow_id)
        if row is None:
            db.add(
                SolutionWorkflowPermissionRequest(
                    solution_id=solution_id,
                    workflow_id=workflow_id,
                    requested_mode=requested.mode.value,
                    requested_grants=_grants_json(requested),
                    request_digest=digest,
                    status=RequestStatus.PENDING.value,
                )
            )
        elif row.request_digest != digest:
            if row.status == RequestStatus.DECLINED.value:
                # A revoked decision cannot approve a later bundle rollback.
                row.approved_digest = None
            row.requested_mode = requested.mode.value
            row.requested_grants = _grants_json(requested)
            row.request_digest = digest
            row.status = (
                RequestStatus.APPROVED.value
                if digest == row.approved_digest
                else RequestStatus.PENDING.value
            )
    await db.flush()
