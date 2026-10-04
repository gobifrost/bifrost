"""Tests for the immediate-execution insert path."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
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
        lineage=None,
    )

    session.get.assert_not_awaited()
    session.flush.assert_awaited_once()
    session.refresh.assert_not_awaited()
    session.add.assert_called_once_with(execution)


def _bound() -> dict[str, str]:
    person, root = str(uuid4()), str(uuid4())
    return {"run_user_id": person, "started_by_user_id": person, "root_execution_id": root}


async def _create(session, execution_id: str, lineage):
    return await ExecutionRepository(session).create_execution(
        execution_id=execution_id,
        workflow_name="wf",
        parameters={},
        org_id=None,
        user_id=str(uuid4()),
        user_name="Test User",
        lineage=lineage,
    )


@pytest.mark.asyncio
async def test_new_execution_records_its_lineage() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    session.get.return_value = None
    lineage = _bound()

    execution = await _create(session, str(uuid4()), lineage)

    assert (str(execution.run_user_id), str(execution.started_by_user_id), str(execution.root_execution_id)) == (
        lineage["run_user_id"],
        lineage["started_by_user_id"],
        lineage["root_execution_id"],
    )


@pytest.mark.asyncio
async def test_promoted_row_takes_the_lineage_it_carries() -> None:
    execution_id = uuid4()
    existing = Execution(id=execution_id, workflow_name="wf", executed_by_name="x")
    session = AsyncMock()
    session.get.return_value = existing
    lineage = _bound()

    await _create(session, str(execution_id), lineage)

    assert str(existing.run_user_id) == lineage["run_user_id"]
    assert str(existing.root_execution_id) == lineage["root_execution_id"]


@pytest.mark.asyncio
async def test_pending_record_without_lineage_leaves_the_row_lineage() -> None:
    execution_id, person, root = uuid4(), uuid4(), uuid4()
    existing = Execution(
        id=execution_id,
        workflow_name="wf",
        executed_by_name="x",
        run_user_id=person,
        started_by_user_id=person,
        root_execution_id=root,
    )
    session = AsyncMock()
    session.get.return_value = existing

    await _create(session, str(execution_id), None)

    assert (existing.run_user_id, existing.started_by_user_id, existing.root_execution_id) == (person, person, root)
