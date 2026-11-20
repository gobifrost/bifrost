"""SCIM bearer authentication: bad tokens can't use up a connection's quota."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.routers import scim as scim_router
from src.services.identity_provisioning.scim import ScimError


def _request(token: str) -> SimpleNamespace:
    return SimpleNamespace(headers={"authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_a_bad_token_counts_against_failures_not_the_connections_quota():
    connection_id = str(uuid4())
    connection_quota, failure_quota = AsyncMock(), AsyncMock()
    with (
        patch.object(scim_router._RATE_LIMIT, "check", connection_quota),
        patch.object(scim_router._AUTH_FAILURE_LIMIT, "check", failure_quota),
        patch.object(scim_router.tokens, "authenticate", AsyncMock(return_value=None)),
    ):
        with pytest.raises(ScimError) as raised:
            await scim_router.scim_connection(connection_id, _request("bfidp_junk"), AsyncMock())

    assert raised.value.status == 401
    connection_quota.assert_not_awaited()
    failure_quota.assert_awaited_once_with("scim_auth_failure", connection_id)


@pytest.mark.asyncio
async def test_a_valid_token_counts_against_its_connection():
    connection = SimpleNamespace(id=uuid4(), source_type="scim")
    connection_quota, failure_quota = AsyncMock(), AsyncMock()
    with (
        patch.object(scim_router._RATE_LIMIT, "check", connection_quota),
        patch.object(scim_router._AUTH_FAILURE_LIMIT, "check", failure_quota),
        patch.object(scim_router.tokens, "authenticate", AsyncMock(return_value=connection)),
    ):
        result = await scim_router.scim_connection(
            str(connection.id), _request("bfidp_valid"), AsyncMock()
        )

    assert result is connection
    connection_quota.assert_awaited_once_with("scim", str(connection.id))
    failure_quota.assert_not_awaited()
