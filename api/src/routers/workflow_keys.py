"""
Workflow Keys Router

API key management for workflow execution.
Allows platform admins to create and revoke API keys for workflows.

API keys are now stored directly on the workflows table (api_key_* columns).
Each workflow can have ONE API key. No global keys - each key is workflow-specific.
"""

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, or_

from src.core.auth import Context, CurrentSuperuser
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.models import Workflow
from src.models import WorkflowKeyCreateRequest, WorkflowKeyResponse
from src.services.workflow_keys import generate_workflow_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workflow-keys", tags=["Workflow Keys"])


# =============================================================================
# Request/Response Models
# =============================================================================

# WorkflowKeyResponse with raw_key for creation
class WorkflowKeyCreatedResponse(WorkflowKeyResponse):
    """Response when creating a key - includes the raw key (shown only once)."""
    raw_key: str = ""  # Override parent's optional field with required but empty default


# =============================================================================
# Helper Functions
# =============================================================================


def mask_key(hashed_key: str) -> str:
    """Create a masked display version of the key hash."""
    if not hashed_key:
        return ""
    return f"{hashed_key[:4]}...{hashed_key[-4:]}"


# =============================================================================
# HTTP Endpoints
# =============================================================================


@router.get(
    "",
    response_model=list[WorkflowKeyResponse],
    summary="List workflow API keys",
    description="List all workflows with API keys enabled (Platform admin only)",
)
async def list_keys(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
    workflow_id: str | None = None,
) -> list[WorkflowKeyResponse]:
    """List all workflows with API keys enabled."""
    query = select(Workflow).where(
        Workflow.api_key_hash.isnot(None),  # Has an API key
        Workflow.api_key_enabled == True,  # noqa: E712
        Workflow.is_active == True,  # noqa: E712
    ).order_by(Workflow.api_key_created_at.desc())

    if workflow_id:
        try:
            query = query.where(Workflow.id == UUID(workflow_id))
        except ValueError:
            return []

    result = await db.execute(query)
    workflows = result.scalars().all()

    return [
        WorkflowKeyResponse(
            id=str(wf.id),
            workflow_id=str(wf.id),
            workflow_name=wf.name,
            raw_key=None,  # Never return raw key in list
            masked_key=mask_key(wf.api_key_hash or ""),
            description=wf.api_key_description,
            created_by=wf.api_key_created_by or "",
            created_at=wf.api_key_created_at or wf.created_at,
            last_used_at=wf.api_key_last_used_at,
            expires_at=wf.api_key_expires_at,
            revoked=not wf.api_key_enabled,
        )
        for wf in workflows
    ]


@router.post(
    "",
    response_model=WorkflowKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new API key",
    description="Create a new workflow API key (Platform admin only, requires workflow_name)",
)
async def create_key(
    request: WorkflowKeyCreateRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> WorkflowKeyCreatedResponse:
    """Create a new workflow API key. Each workflow can have one API key."""

    # Workflow ID is now required
    if not request.workflow_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="workflow_id is required (global keys are no longer supported)"
        )

    # Look up the workflow by ID
    try:
        wf_uuid = UUID(request.workflow_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workflow_id format (expected UUID)"
        )

    result = await db.execute(
        select(Workflow).where(
            Workflow.id == wf_uuid,
            Workflow.is_active == True,  # noqa: E712
        )
    )
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{request.workflow_id}' not found"
        )

    # Check if workflow already has a key
    if workflow.api_key_hash and workflow.api_key_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Workflow '{workflow.name}' already has an active API key. Revoke it first."
        )

    # Generate new key
    raw_key, hashed_key = generate_workflow_key()

    expires_at = None
    if request.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=request.expires_in_days)

    # Update workflow with API key info
    workflow.api_key_hash = hashed_key
    workflow.api_key_description = request.description
    workflow.api_key_enabled = True
    workflow.api_key_created_by = user.email
    workflow.api_key_created_at = datetime.now(timezone.utc)
    workflow.api_key_expires_at = expires_at

    await db.flush()
    await db.refresh(workflow)

    logger.info(f"Created API key for workflow '{workflow.name}' (ID: {workflow.id}) by {user.email}")

    return WorkflowKeyCreatedResponse(
        id=str(workflow.id),
        workflow_id=str(workflow.id),
        workflow_name=workflow.name,
        masked_key=mask_key(hashed_key),
        raw_key=raw_key,
        description=workflow.api_key_description,
        created_by=workflow.api_key_created_by or user.email,
        created_at=workflow.api_key_created_at or workflow.created_at,
        last_used_at=workflow.api_key_last_used_at,
        expires_at=workflow.api_key_expires_at,
        revoked=not workflow.api_key_enabled,
    )


@router.delete(
    "/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key",
    description="Revoke a workflow API key by workflow ID (Platform admin only)",
)
async def revoke_key(
    workflow_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Revoke (disable) a workflow API key."""
    result = await db.execute(
        select(Workflow).where(Workflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    if not workflow.api_key_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workflow does not have an API key",
        )

    if not workflow.api_key_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API key already revoked",
        )

    # Disable the API key
    workflow.api_key_enabled = False

    await db.flush()
    logger.info(f"Revoked API key for workflow '{log_safe(workflow.name)}' (ID: {log_safe(workflow_id)}) by {user.email}")


# =============================================================================
# Key Validation (for use by auth middleware)
# =============================================================================


async def validate_workflow_key(
    db: DbSession,
    api_key: str,
    workflow_id: str | None = None,
) -> tuple[bool, UUID | None]:
    """
    Validate an API key for workflow execution.

    Args:
        db: Database session
        api_key: Raw API key to validate
        workflow_id: Workflow UUID string to validate against

    Returns:
        Tuple of (is_valid, workflow_id)
    """
    hashed_key = hashlib.sha256(api_key.encode()).hexdigest()
    now = datetime.now(timezone.utc)

    # Build query for workflow with matching API key
    query = select(Workflow).where(
        Workflow.api_key_hash == hashed_key,
        Workflow.api_key_enabled == True,  # noqa: E712
        Workflow.is_active == True,  # noqa: E712
        or_(
            Workflow.api_key_expires_at.is_(None),
            Workflow.api_key_expires_at > now,
        ),
    )

    # If workflow_id provided, filter by ID
    if workflow_id:
        try:
            query = query.where(Workflow.id == UUID(workflow_id))
        except ValueError:
            return (False, None)

    result = await db.execute(query)
    workflow = result.scalar_one_or_none()

    if not workflow:
        return (False, None)

    # Update last used timestamp
    workflow.api_key_last_used_at = now
    await db.flush()

    return (True, workflow.id)
