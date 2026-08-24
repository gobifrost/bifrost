"""Boundary-aware access helpers for Workflow administration surfaces."""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Workflow
from src.repositories.workflows import WorkflowRepository
from src.services.authorization import (
    AuthorizationBoundaryKind,
    AuthorizationContext,
)


def selected_workflow_organization_id(
    authorization: AuthorizationContext,
) -> UUID | None:
    """Resolve one executable Workflow target from the selected boundary."""

    boundary = authorization.selected_boundary
    if boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select one organization before working with Workflows",
        )
    return (
        boundary.organization_id
        if boundary.kind is AuthorizationBoundaryKind.ORGANIZATION
        else None
    )


def authorized_workflow_repository(
    db: AsyncSession,
    authorization: AuthorizationContext,
    *,
    allow_embed_execution: bool = False,
) -> WorkflowRepository:
    """Build a Workflow repository bound to the selected exact target."""

    user = authorization.requester
    return WorkflowRepository(
        session=db,
        org_id=selected_workflow_organization_id(authorization),
        user_id=user.user_id,
        bypass_resource_roles=authorization.has_capability("platform.superuser"),
        is_external=user.is_external and not allow_embed_execution,
    )


async def authorized_workflow_by_id(
    db: AsyncSession,
    authorization: AuthorizationContext,
    workflow_id: UUID,
) -> Workflow:
    """Return one visible Workflow without leaking inaccessible IDs."""

    workflow = await authorized_workflow_repository(db, authorization).get(
        id=workflow_id
    )
    if workflow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow with ID '{workflow_id}' not found",
        )
    return workflow


def require_workflow_mutation(
    authorization: AuthorizationContext,
    workflow: Workflow,
) -> None:
    """Require Workflow authoring authority in the resource's boundary."""

    authorization.require("workflows.readwrite")
    authorization.require_resource_boundary(workflow.organization_id)
