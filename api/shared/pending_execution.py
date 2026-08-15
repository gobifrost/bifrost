"""Read-through representation for executions awaiting worker persistence."""

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from src.core.log_safety import log_safe
from src.core.principal import UserPrincipal
from src.core.redis_client import get_redis_client
from src.models import WorkflowExecution
from src.models.enums import ExecutionStatus
from src.models.orm.workflows import Workflow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def get_pending_execution_fallback(
    execution_id: UUID,
    user: UserPrincipal,
    db: "AsyncSession",
) -> tuple[WorkflowExecution | None, str | None]:
    """Return an owned Redis-pending execution after PostgreSQL misses."""
    try:
        pending = await get_redis_client().get_pending_execution(str(execution_id))
    except Exception:
        logger.warning(
            "Could not check Redis for pending execution %s",
            log_safe(execution_id),
            exc_info=True,
        )
        return None, "NotFound"

    if pending is None:
        return None, "NotFound"

    pending_user_id = pending.get("user_id")
    if not user.is_superuser and pending_user_id != str(user.user_id):
        return None, "Forbidden"

    workflow_id = pending.get("workflow_id")
    workflow_name = pending.get("script_name")
    if not workflow_name and workflow_id:
        workflow_result = await db.execute(
            select(Workflow.name).where(Workflow.id == UUID(workflow_id))
        )
        workflow_name = workflow_result.scalar_one_or_none()

    org_id = pending.get("org_id")
    if isinstance(org_id, str) and org_id.startswith("ORG:"):
        org_id = org_id.removeprefix("ORG:")

    return (
        WorkflowExecution(
            execution_id=str(execution_id),
            workflow_name=workflow_name or "Pending execution",
            workflow_id=workflow_id,
            org_id=org_id,
            org_name="Global" if org_id is None else None,
            form_id=pending.get("form_id"),
            executed_by=pending_user_id,
            executed_by_name=pending.get("user_name") or pending_user_id,
            executed_by_email=pending.get("user_email"),
            status=(
                ExecutionStatus.CANCELLED
                if pending.get("cancelled")
                else ExecutionStatus.PENDING
            ),
            input_data=pending.get("parameters") or {},
            result=None,
            logs=[],
            started_at=None,
            completed_at=None,
        ),
        None,
    )
