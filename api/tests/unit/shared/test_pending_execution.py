from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from shared.pending_execution import get_pending_execution_fallback
from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus


EXECUTION_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
OTHER_USER_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def _principal(*, user_id: UUID = USER_ID, is_superuser: bool = False) -> UserPrincipal:
    return UserPrincipal(
        user_id=user_id,
        email="user@example.com",
        organization_id=None,
        is_superuser=is_superuser,
    )


def _pending(**overrides):
    value = {
        "execution_id": str(EXECUTION_ID),
        "workflow_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "script_name": None,
        "parameters": {"domain": "example.com"},
        "org_id": None,
        "user_id": str(USER_ID),
        "user_name": "Example User",
        "user_email": "user@example.com",
        "form_id": None,
        "api_key_id": None,
        "startup": None,
        "form_inputs": {},
        "embed": {},
        "sync": False,
        "is_platform_admin": False,
        "event": None,
        "created_at": "2026-08-14T12:00:00+00:00",
        "cancelled": False,
    }
    value.update(overrides)
    return value


@pytest.mark.asyncio
async def test_pending_execution_is_returned_as_contract_valid_pending_state():
    redis = AsyncMock()
    redis.get_pending_execution.return_value = _pending()
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = "DMARC report ingestion"
    db.execute.return_value = result

    with patch(
        "shared.pending_execution.get_redis_client",
        return_value=redis,
    ):
        execution, error = await get_pending_execution_fallback(
            EXECUTION_ID,
            _principal(),
            db,
        )

    assert error is None
    assert execution is not None
    assert execution.execution_id == str(EXECUTION_ID)
    assert execution.workflow_name == "DMARC report ingestion"
    assert execution.status == ExecutionStatus.PENDING
    assert execution.started_at is None
    assert execution.input_data == {"domain": "example.com"}
    assert execution.executed_by == str(USER_ID)


@pytest.mark.asyncio
async def test_pending_execution_preserves_owner_authorization():
    redis = AsyncMock()
    redis.get_pending_execution.return_value = _pending(user_id=str(OTHER_USER_ID))

    with patch(
        "shared.pending_execution.get_redis_client",
        return_value=redis,
    ):
        execution, error = await get_pending_execution_fallback(
            EXECUTION_ID,
            _principal(),
            AsyncMock(),
        )

    assert execution is None
    assert error == "Forbidden"


@pytest.mark.asyncio
async def test_platform_admin_can_read_any_pending_execution():
    redis = AsyncMock()
    redis.get_pending_execution.return_value = _pending(
        user_id=str(OTHER_USER_ID),
        script_name="inline_script",
        workflow_id=None,
    )

    with patch(
        "shared.pending_execution.get_redis_client",
        return_value=redis,
    ):
        execution, error = await get_pending_execution_fallback(
            EXECUTION_ID,
            _principal(is_superuser=True),
            AsyncMock(),
        )

    assert error is None
    assert execution is not None
    assert execution.workflow_name == "inline_script"


@pytest.mark.asyncio
async def test_cancelled_pending_execution_is_not_reported_as_pending():
    redis = AsyncMock()
    redis.get_pending_execution.return_value = _pending(
        cancelled=True,
        script_name="pending script",
        workflow_id=None,
    )

    with patch(
        "shared.pending_execution.get_redis_client",
        return_value=redis,
    ):
        execution, error = await get_pending_execution_fallback(
            EXECUTION_ID,
            _principal(),
            AsyncMock(),
        )

    assert error is None
    assert execution is not None
    assert execution.status == ExecutionStatus.CANCELLED


@pytest.mark.asyncio
async def test_missing_or_unavailable_redis_preserves_not_found():
    for redis_result in (None, RuntimeError("redis unavailable")):
        redis = AsyncMock()
        if isinstance(redis_result, Exception):
            redis.get_pending_execution.side_effect = redis_result
        else:
            redis.get_pending_execution.return_value = redis_result

        with patch(
            "shared.pending_execution.get_redis_client",
            return_value=redis,
        ):
            execution, error = await get_pending_execution_fallback(
                EXECUTION_ID,
                _principal(),
                AsyncMock(),
            )

        assert execution is None
        assert error == "NotFound"
