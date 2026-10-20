"""Redis-pending cancellation must authorize before its state changes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.routers.executions import cancel_execution


@pytest.mark.asyncio
async def test_pending_cancel_refuses_another_users_receipt_before_mutation():
    execution_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    caller_id = uuid4()
    owner_id = uuid4()
    principal = UserPrincipal(
        user_id=caller_id,
        email="caller@example.com",
        organization_id=None,
        is_verified=True,
    )
    ctx = SimpleNamespace(db=AsyncMock(), user=principal)
    redis = AsyncMock()
    redis.get_pending_execution.return_value = {
        "org_id": None,
        "user_id": str(owner_id),
        "lineage": {
            "started_by_user_id": str(owner_id),
            "root_execution_id": str(execution_id),
        },
    }
    caller = SimpleNamespace(principal=principal, ctx=None)

    with (
        patch("src.routers.executions.get_redis_client", return_value=redis),
        patch(
            "src.routers.executions.ExecutionReadAdapter.cancel_execution",
            new=AsyncMock(return_value=(None, "NotFound")),
        ),
        patch("src.routers.executions.load_caller", new=AsyncMock(return_value=caller)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await cancel_execution(execution_id, ctx)

    assert exc_info.value.status_code == 403
    redis.set_pending_cancelled.assert_not_awaited()
