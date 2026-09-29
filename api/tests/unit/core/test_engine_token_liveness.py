"""An engine token stops working once its execution is no longer running.

mint_engine_token mints a token scoped to one execution. Historically it
kept authenticating for its full signed lifetime (up to 24h) even after the
execution it was minted for had finished - a completed execution's SDK
credential should not still be usable. Liveness is read from the execution
row's status in Postgres (shared.execution_liveness.is_execution_live), the
authoritative source, rather than the best-effort Redis active-execution
lease.

A supervised-service token (mint_service_token) also carries
engine_execution_id but is never superuser, so it must be unaffected by this
check.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials

from src.core.auth import get_current_user_optional
from src.core.security import mint_engine_token, mint_service_token
from src.models.enums import ExecutionStatus
from shared.execution_liveness import is_execution_live


@pytest.fixture
def mock_request():
    request = MagicMock(spec=Request)
    request.cookies = {}
    return request


@pytest.fixture
def mock_db():
    return MagicMock()


def _credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _db_with_status(status: ExecutionStatus | None) -> AsyncMock:
    """An AsyncSession stand-in whose SELECT returns a single status column."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=status)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _patched_liveness(live: bool):
    return patch("shared.execution_liveness.is_execution_live", AsyncMock(return_value=live))


# =============================================================================
# is_execution_live: the primary-key status check
# =============================================================================


@pytest.mark.parametrize(
    "status",
    [ExecutionStatus.PENDING, ExecutionStatus.RUNNING, ExecutionStatus.CANCELLING],
)
@pytest.mark.asyncio
async def test_is_execution_live_accepts_live_statuses(status):
    db = _db_with_status(status)
    assert await is_execution_live(db, str(uuid4())) is True


@pytest.mark.parametrize(
    "status",
    [
        ExecutionStatus.SCHEDULED,
        ExecutionStatus.SUCCESS,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMEOUT,
        ExecutionStatus.STUCK,
        ExecutionStatus.COMPLETED_WITH_ERRORS,
        ExecutionStatus.CANCELLED,
    ],
)
@pytest.mark.asyncio
async def test_is_execution_live_rejects_non_live_statuses(status):
    db = _db_with_status(status)
    assert await is_execution_live(db, str(uuid4())) is False


@pytest.mark.asyncio
async def test_is_execution_live_rejects_missing_row():
    db = _db_with_status(None)
    assert await is_execution_live(db, str(uuid4())) is False


@pytest.mark.asyncio
async def test_is_execution_live_rejects_malformed_id():
    db = _db_with_status(ExecutionStatus.RUNNING)
    assert await is_execution_live(db, "not-a-uuid") is False
    db.execute.assert_not_awaited()


# =============================================================================
# get_current_user_optional: the engine-token liveness gate
# =============================================================================


@pytest.mark.asyncio
async def test_engine_token_authenticates_while_execution_is_running(
    mock_request, mock_db
):
    execution_id = str(uuid4())
    token, _ = mint_engine_token(
        execution_id=execution_id,
        solution_id=None,
        global_repo_access=False,
        timeout_seconds=300,
    )

    with _patched_liveness(True):
        user = await get_current_user_optional(mock_request, _credentials(token), mock_db)

    assert user is not None
    assert user.engine_execution_id == execution_id
    assert user.is_superuser is True


@pytest.mark.asyncio
async def test_engine_token_rejected_once_its_execution_has_ended(
    mock_request, mock_db
):
    execution_id = str(uuid4())
    token, _ = mint_engine_token(
        execution_id=execution_id,
        solution_id=None,
        global_repo_access=False,
        timeout_seconds=300,
    )

    with _patched_liveness(False):
        user = await get_current_user_optional(mock_request, _credentials(token), mock_db)

    assert user is None


@pytest.mark.asyncio
async def test_engine_token_accepted_with_no_lease_when_row_is_running(
    mock_request, mock_db
):
    """Regression: a lost Redis lease must not fail a still-Running execution.

    The merge queue caught this against
    tests/e2e/api/test_executions.py::TestAsyncExecution::
    test_running_execution_completes_after_redis_tracking_loss, which
    deletes the lease mid-run and expects the workflow to keep going.
    """
    execution_id = str(uuid4())
    token, _ = mint_engine_token(
        execution_id=execution_id,
        solution_id=None,
        global_repo_access=False,
        timeout_seconds=300,
    )

    db = _db_with_status(ExecutionStatus.RUNNING)
    user = await get_current_user_optional(mock_request, _credentials(token), db)

    assert user is not None
    assert user.engine_execution_id == execution_id


@pytest.mark.asyncio
async def test_engine_token_liveness_check_fails_closed_on_db_error(
    mock_request, mock_db
):
    execution_id = str(uuid4())
    token, _ = mint_engine_token(
        execution_id=execution_id,
        solution_id=None,
        global_repo_access=False,
        timeout_seconds=300,
    )

    with patch(
        "shared.execution_liveness.is_execution_live",
        AsyncMock(side_effect=ConnectionError("database down")),
    ):
        user = await get_current_user_optional(mock_request, _credentials(token), mock_db)

    assert user is None


@pytest.mark.asyncio
async def test_service_token_unaffected_by_execution_status(
    mock_request, mock_db
):
    """Service tokens (mint_service_token) also carry engine_execution_id
    but are never superuser, and must not be gated by this check."""
    token, _ = mint_service_token(
        service_id=str(uuid4()),
        attempt_id=str(uuid4()),
        organization_id=str(uuid4()),
        solution_id=None,
        global_repo_access=False,
    )

    with _patched_liveness(False):
        user = await get_current_user_optional(mock_request, _credentials(token), mock_db)

    assert user is not None
    assert user.service_id is not None
    assert user.is_superuser is False
