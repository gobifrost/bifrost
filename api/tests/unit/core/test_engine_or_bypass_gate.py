"""Unit tests for the execution-credential-or-bypass dependency."""

from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.auth import get_current_engine_or_bypass_user
from src.core.constants import SYSTEM_USER_UUID
from src.core.principal import UserPrincipal


def _principal(**overrides) -> UserPrincipal:
    values = {"user_id": uuid4(), "email": "user@example.com", "organization_id": uuid4()}
    values.update(overrides)
    return UserPrincipal(**values)


@pytest.mark.asyncio
async def test_engine_token_is_admitted():
    engine = _principal(
        user_id=SYSTEM_USER_UUID,
        organization_id=None,
        is_superuser=True,
        engine_execution_id=str(uuid4()),
    )
    assert await get_current_engine_or_bypass_user(engine) is engine


@pytest.mark.asyncio
async def test_service_token_is_admitted():
    service = _principal(
        user_id=SYSTEM_USER_UUID,
        engine_execution_id=str(uuid4()),
        service_id=str(uuid4()),
    )
    assert await get_current_engine_or_bypass_user(service) is service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flags",
    [{"is_superuser": True}, {"is_provider_org": True}],
    ids=["platform-admin", "provider-org-member"],
)
async def test_bypass_principals_are_admitted(flags):
    principal = _principal(**flags)
    assert await get_current_engine_or_bypass_user(principal) is principal


@pytest.mark.asyncio
async def test_regular_user_token_is_refused():
    with pytest.raises(HTTPException) as exc:
        await get_current_engine_or_bypass_user(_principal())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_embed_token_is_refused_even_with_execution_claim():
    embed = _principal(
        user_id=SYSTEM_USER_UUID,
        embed=True,
        engine_execution_id=str(uuid4()),
    )
    with pytest.raises(HTTPException) as exc:
        await get_current_engine_or_bypass_user(embed)
    assert exc.value.status_code == 403
