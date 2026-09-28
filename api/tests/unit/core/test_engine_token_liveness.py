"""An engine token stops working once its execution is no longer running.

mint_engine_token mints a token scoped to one execution. Historically it
kept authenticating for its full signed lifetime (up to 24h) even after the
execution it was minted for had finished - a completed execution's SDK
credential should not still be usable. The active-execution Redis lease
(process_pool.py::_write_active_execution_lease / cleanup_execution_cache)
is the parent's own liveness signal for a running child, so authentication
reuses it instead of inventing a new one.

A supervised-service token (mint_service_token) also carries
engine_execution_id but is never superuser, so it must be unaffected by a
missing lease.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials

from src.core.auth import get_current_user_optional
from src.core.security import mint_engine_token, mint_service_token


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


def _patched_redis_client(active_execution):
    """A get_redis_client() stand-in whose get_active_execution() is canned."""
    client = MagicMock()
    client.get_active_execution = AsyncMock(return_value=active_execution)
    return patch("src.core.redis_client.get_redis_client", return_value=client)


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

    with _patched_redis_client({"execution_id": execution_id, "sync": False}):
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

    # cleanup_execution_cache removes the lease on every terminal path
    # (success, failure, timeout, cancellation, crash); a missing lease
    # means the execution is over.
    with _patched_redis_client(None):
        user = await get_current_user_optional(mock_request, _credentials(token), mock_db)

    assert user is None


@pytest.mark.asyncio
async def test_engine_token_liveness_check_fails_closed_on_redis_error(
    mock_request, mock_db
):
    execution_id = str(uuid4())
    token, _ = mint_engine_token(
        execution_id=execution_id,
        solution_id=None,
        global_repo_access=False,
        timeout_seconds=300,
    )

    client = MagicMock()
    client.get_active_execution = AsyncMock(side_effect=ConnectionError("redis down"))
    with patch("src.core.redis_client.get_redis_client", return_value=client):
        user = await get_current_user_optional(mock_request, _credentials(token), mock_db)

    assert user is None


@pytest.mark.asyncio
async def test_service_token_unaffected_by_missing_execution_lease(
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

    with _patched_redis_client(None):
        user = await get_current_user_optional(mock_request, _credentials(token), mock_db)

    assert user is not None
    assert user.service_id is not None
    assert user.is_superuser is False
