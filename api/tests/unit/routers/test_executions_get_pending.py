"""Regression tests for the PostgreSQL-first pending execution read path."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.routers.executions import get_execution


@pytest.mark.asyncio
async def test_get_execution_falls_back_to_redis_only_after_database_miss():
    execution_id = uuid4()
    ctx = MagicMock()
    database_execution = MagicMock(name="database_execution")
    pending_execution = MagicMock(name="pending_execution")

    with (
        patch(
            "src.routers.executions.ExecutionRepository.get_execution",
            new=AsyncMock(return_value=(None, "NotFound")),
        ),
        patch(
            "src.routers.executions.get_pending_execution_fallback",
            new=AsyncMock(return_value=(pending_execution, None)),
        ) as fallback,
    ):
        result = await get_execution(execution_id, ctx)

    assert result is pending_execution
    fallback.assert_awaited_once_with(execution_id, ctx.user, ctx.db)

    with (
        patch(
            "src.routers.executions.ExecutionRepository.get_execution",
            new=AsyncMock(return_value=(database_execution, None)),
        ),
        patch(
            "src.routers.executions.get_pending_execution_fallback",
            new=AsyncMock(),
        ) as fallback,
    ):
        result = await get_execution(execution_id, ctx)

    assert result is database_execution
    fallback.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_execution_remains_not_found_when_database_and_redis_miss():
    execution_id = uuid4()
    ctx = MagicMock()

    with (
        patch(
            "src.routers.executions.ExecutionRepository.get_execution",
            new=AsyncMock(return_value=(None, "NotFound")),
        ),
        patch(
            "src.routers.executions.get_pending_execution_fallback",
            new=AsyncMock(return_value=(None, "NotFound")),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await get_execution(execution_id, ctx)

    assert exc_info.value.status_code == 404
