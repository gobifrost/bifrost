"""Workflow permission mode and Solution permission requests.

Data only: nothing reads the effective mode or the grants at run time until
delegated execution. See ``src.models.contracts.workflow_permissions``.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.workflow_permissions import (
    RequestedWorkflowPermissions,
    RequestStatus,
    WorkflowGrant,
    WorkflowPermissionMode,
)
from src.models.orm.config import SystemConfig
from src.models.orm.solution_workflow_permission_requests import SolutionWorkflowPermissionRequest
from src.models.orm.workflow_permissions import WorkflowPermissionGrant
from src.models.orm.workflows import Workflow

DEFAULT_WORKFLOW_PERMISSION_MODE = WorkflowPermissionMode.FULL

WORKFLOW_PERMISSIONS_CONFIG_CATEGORY = "workflow_permissions"
DEFAULT_MODE_CONFIG_KEY = "default_mode"


async def get_default_workflow_permission_mode(db: AsyncSession) -> WorkflowPermissionMode:
    """The platform's "Default workflow permissions" setting."""
    config = (
        await db.execute(
            select(SystemConfig).where(
                SystemConfig.category == WORKFLOW_PERMISSIONS_CONFIG_CATEGORY,
                SystemConfig.key == DEFAULT_MODE_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
    ).scalars().first()
    if config is None or not config.value_json:
        return DEFAULT_WORKFLOW_PERMISSION_MODE
    return WorkflowPermissionMode(config.value_json["mode"])


async def effective_workflow_permission_mode(
    db: AsyncSession, workflow: Workflow
) -> WorkflowPermissionMode:
    """The workflow's own mode, else the platform default."""
    if workflow.permission_mode is not None:
        return WorkflowPermissionMode(workflow.permission_mode)
    return await get_default_workflow_permission_mode(db)


async def list_workflow_grants(db: AsyncSession, workflow_id: UUID) -> list[WorkflowGrant]:
    rows = (
        await db.execute(
            select(WorkflowPermissionGrant).where(
                WorkflowPermissionGrant.workflow_id == workflow_id
            )
        )
    ).scalars().all()
    return [
        WorkflowGrant(
            permission=row.permission,
            boundary=row.boundary_kind,  # type: ignore[arg-type]
            organization_id=row.organization_id,
        )
        for row in rows
    ]


def _grants_json(requested: RequestedWorkflowPermissions) -> list[dict[str, str | None]]:
    """Grants as stored JSON, sorted so manifest order never matters."""
    return sorted(
        ({"permission": g.permission, "boundary": g.boundary} for g in requested.grants),
        key=lambda g: (g["permission"], g["boundary"] or ""),
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
                select(SolutionWorkflowPermissionRequest).where(
                    SolutionWorkflowPermissionRequest.solution_id == solution_id
                )
            )
        ).scalars()
    }

    stale = existing.keys() - requests.keys()
    if stale:
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
            row.requested_mode = requested.mode.value
            row.requested_grants = _grants_json(requested)
            row.request_digest = digest
            row.status = (
                RequestStatus.APPROVED.value
                if digest == row.approved_digest
                else RequestStatus.PENDING.value
            )
    await db.flush()
