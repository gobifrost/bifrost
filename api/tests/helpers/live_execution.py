"""Test helper: back an engine token's execution_id with a real, live row.

Engine-token liveness is read from the execution row's status in Postgres
(``src/core/auth.py`` via ``shared.execution_liveness.is_execution_live``).
Tests that mint an engine token directly (bypassing the process pool) must
create a matching execution row in a Running status so they exercise a
still-running child the way production does, instead of hitting a false 401.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution


async def create_live_execution(async_session_factory, execution_id: str) -> None:
    """Insert a Running execution row for `execution_id` (committed)."""
    async with async_session_factory() as session:
        session.add(
            Execution(
                id=UUID(execution_id),
                workflow_name="test-engine-token-liveness",
                status=ExecutionStatus.RUNNING,
                executed_by_name="test-engine-token-liveness",
            )
        )
        await session.commit()


async def delete_live_execution(async_session_factory, execution_id: str) -> None:
    """Delete the execution row created by `create_live_execution`."""
    async with async_session_factory() as session:
        await session.execute(delete(Execution).where(Execution.id == UUID(execution_id)))
        await session.commit()
