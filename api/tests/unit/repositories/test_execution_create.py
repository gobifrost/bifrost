"""Tests for the immediate-execution insert path."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.models.enums import ExecutionStatus
from src.repositories.executions import ExecutionRepository


@pytest.mark.asyncio
async def test_known_new_execution_skips_lookup_and_refresh() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    repo = ExecutionRepository(session)

    execution = await repo.create_execution(
        execution_id=str(uuid4()),
        workflow_name="fast workflow",
        parameters={},
        org_id=None,
        user_id=str(uuid4()),
        user_name="Test User",
        status=ExecutionStatus.RUNNING,
        check_existing=False,
    )

    session.get.assert_not_awaited()
    session.flush.assert_awaited_once()
    session.refresh.assert_not_awaited()
    session.add.assert_called_once_with(execution)
