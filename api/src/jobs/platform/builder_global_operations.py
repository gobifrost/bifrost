"""Reusable Global Builder operation-change apply/rollback helpers."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession

from src.jobs.platform.base import PlatformJobFailure
from src.models.orm.solution_builder import SolutionGlobalOperationChange
from src.services.builder.global_operation_changes import (
    GlobalOperationChangeError,
    apply_staged_global_operation_changes,
    recover_interrupted_global_operation_changes,
    rollback_applied_global_operation_changes,
)
from src.services.builder.runtime_authorization import AuthorizedBuilderProject
from src.services.mcp_server.server import MCPContext
from src.services.operation_catalog import get_operation


def _capabilities_for_operation_ids(operation_ids: set[str]) -> tuple[str, ...]:
    capabilities = {"builder.execute"}
    for operation_id in operation_ids:
        capabilities.update(get_operation(operation_id).action_scopes)
    return tuple(sorted(capabilities))


async def required_capabilities_for_changes(
    db: AsyncSession,
    *,
    solution_id: UUID,
    approved_changes: dict[UUID, str],
) -> tuple[str, ...]:
    if not approved_changes:
        return ("builder.execute",)
    rows = (
        await db.scalars(
            select(SolutionGlobalOperationChange).where(
                SolutionGlobalOperationChange.solution_id == solution_id,
                SolutionGlobalOperationChange.id.in_(approved_changes),
            )
        )
    ).all()
    operation_ids = {row.operation_id for row in rows}
    capabilities = set(_capabilities_for_operation_ids(operation_ids))
    if any(row.payload.get("role_ids") for row in rows if isinstance(row.payload, dict)):
        capabilities.add("roles.readwrite")
    return tuple(sorted(capabilities))


def global_operations_mcp_context(
    authorized: AuthorizedBuilderProject,
    *,
    solution_id: UUID,
) -> MCPContext:
    return MCPContext(
        user_id=authorized.principal.user_id,
        org_id=authorized.principal.organization_id,
        is_platform_admin=authorized.authorization.has_capability("platform.superuser"),
        is_external=authorized.principal.is_external,
        user_email=authorized.principal.email,
        user_name=authorized.principal.name,
        agent_solution_id=solution_id,
        authorization_boundary="platform",
        resource_gate_bypass=authorized.authorization.has_capability(
            "platform.superuser"
        ),
    )


async def apply_global_operations_for_release(
    db: AsyncSession,
    *,
    solution_id: UUID,
    context: MCPContext,
    requested_by: UUID,
    apply_job_id: UUID,
    approved_changes: dict[UUID, str],
) -> dict[str, object]:
    """Apply reviewed Global loose-resource changes inside one release job."""

    recovered = await recover_interrupted_global_operation_changes(
        db,
        solution_id=solution_id,
    )
    try:
        applied = await apply_staged_global_operation_changes(
            db,
            solution_id=solution_id,
            context=context,
            requested_by=requested_by,
            apply_job_id=apply_job_id,
            approved_changes=approved_changes,
        )
    except GlobalOperationChangeError as exc:
        raise PlatformJobFailure(
            "global_operation_apply_failed",
            str(exc),
            retryable=False,
        ) from exc
    return {
        "solution_id": str(solution_id),
        "applied_count": len(applied),
        "recovered_count": recovered,
        "changes": [
            {
                "change_id": str(item.id),
                "operation_id": item.operation_id,
                "resource_id": item.resource_id,
                "state": item.state,
            }
            for item in applied
        ],
    }


async def rollback_global_operations_for_release(
    db: AsyncSession,
    *,
    solution_id: UUID,
    context: MCPContext,
    requested_by: UUID,
    rollback_job_id: UUID,
    approved_changes: dict[UUID, str],
) -> dict[str, object]:
    """Roll back reviewed Global loose-resource changes inside one release job."""

    try:
        rolled_back = await rollback_applied_global_operation_changes(
            db,
            solution_id=solution_id,
            context=context,
            requested_by=requested_by,
            rollback_job_id=rollback_job_id,
            approved_changes=approved_changes,
        )
    except GlobalOperationChangeError as exc:
        raise PlatformJobFailure(
            "global_operation_rollback_failed",
            str(exc),
            retryable=False,
        ) from exc
    return {
        "solution_id": str(solution_id),
        "rolled_back_count": len(rolled_back),
        "changes": [
            {
                "change_id": str(item.id),
                "operation_id": item.operation_id,
                "resource_id": item.resource_id,
                "state": item.state,
            }
            for item in rolled_back
        ],
    }
