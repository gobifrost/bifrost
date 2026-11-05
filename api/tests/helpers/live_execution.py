"""Test helper: back an engine token's execution_id with a real, live row.

Engine-token liveness is read from the execution row's status in Postgres
(``src/core/auth.py`` via ``shared.execution_liveness.is_execution_live``).
Tests that mint an engine token directly (bypassing the process pool) must
create a matching execution row in a Running status so they exercise a
still-running child the way production does, instead of hitting a false 401.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution


@dataclass(frozen=True)
class LiveExecution:
    """Durable identifiers created to back an engine-token test credential."""

    execution_id: UUID
    actor_id: UUID
    workflow_id: UUID
    organization_id: UUID | None


async def create_live_execution(
    async_session_factory, execution_id: str, *, organization_id: UUID | None = None,
    platform_admin: bool = True,
) -> LiveExecution:
    """Insert a Running execution with durable actor, caller, and Full workflow."""
    from shared.sdk_users import set_platform_admin
    from src.core.constants import PROVIDER_ORG_ID
    from src.models.orm.users import User
    from src.models.orm.workflows import Workflow

    execution_uuid = UUID(execution_id)
    async with async_session_factory() as session:
        actor = User(
            email=f"live-execution-{execution_uuid.hex}@example.com",
            name="Live Execution Test Actor",
            organization_id=organization_id or PROVIDER_ORG_ID,
        )
        session.add(actor)
        await session.flush()
        # Socket transport tests intentionally cross organization boundaries;
        # represent that with a real Platform Admin assignment, not a sentinel.
        if platform_admin:
            await set_platform_admin(session, actor, True, assigned_by="live-execution-test")
        workflow = Workflow(
            name=f"live_execution_{execution_uuid.hex}",
            function_name="live_execution",
            path=f"workflows/live_execution_{execution_uuid.hex}.py",
            permission_mode="full",
            organization_id=organization_id,
        )
        session.add(workflow)
        await session.flush()
        session.add(
            Execution(
                id=execution_uuid,
                workflow_name="test-engine-token-liveness",
                status=ExecutionStatus.RUNNING,
                executed_by_name="test-engine-token-liveness",
                workflow_id=workflow.id,
                run_user_id=actor.id,
                executed_by=actor.id,
                started_by_user_id=actor.id,
                root_execution_id=execution_uuid,
                organization_id=organization_id,
            )
        )
        await session.commit()
    return LiveExecution(
        execution_id=execution_uuid,
        actor_id=actor.id,
        workflow_id=workflow.id,
        organization_id=organization_id,
    )


async def delete_live_execution(async_session_factory, execution_id: str) -> None:
    """Delete the durable test rows created by `create_live_execution`, and the
    audit and access-check rows the engine token wrote under its execution."""
    from src.models.orm.audit import AuditLog
    from src.models.orm.users import User
    from src.models.orm.workflows import Workflow

    async with async_session_factory() as session:
        # Keyed by execution: user_id is set to NULL when the actor is deleted,
        # and organization_id is NULL for global targets.
        await session.execute(delete(AuditLog).where(AuditLog.execution_id == UUID(execution_id)))
        execution = await session.get(Execution, UUID(execution_id))
        if execution is None:
            await session.commit()
            return
        workflow_id = execution.workflow_id
        actor_id = execution.run_user_id
        await session.execute(delete(Execution).where(Execution.id == execution.id))
        if workflow_id is not None:
            await session.execute(delete(Workflow).where(Workflow.id == workflow_id))
        if actor_id is not None:
            await session.execute(delete(User).where(User.id == actor_id))
        await session.commit()
