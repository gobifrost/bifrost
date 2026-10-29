"""Unit tests for CLI SDK scope resolution through the current Caller."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.routers.cli import _resolve_sdk_org_id
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import Caller
from tests.helpers.authorization import admin_caller


def _caller(*, org_id: UUID | None, grants: tuple[RoleGrant, ...] = ()) -> Caller:
    principal = UserPrincipal(
        user_id=uuid4(), email="scope@example.test", organization_id=org_id
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=principal.user_id,
            home_organization_id=org_id,
            base_role_id=uuid4(),
            is_external=False,
            base_permissions=frozenset(),
            role_grants=grants,
        ),
    )


async def _resolve(caller: Caller, scope: str | None) -> str | None:
    with patch("src.services.authorization.enforce.load_caller", new=AsyncMock(return_value=caller)):
        return await _resolve_sdk_org_id(caller.principal, scope, AsyncMock())


@pytest.mark.asyncio
async def test_platform_admin_global_returns_none():
    assert await _resolve(admin_caller(), "global") is None


@pytest.mark.asyncio
async def test_platform_admin_can_target_any_org():
    target = str(uuid4())
    assert await _resolve(admin_caller(), target) == target


@pytest.mark.asyncio
async def test_org_grant_can_target_its_granted_organization():
    target = uuid4()
    caller = _caller(
        org_id=uuid4(),
        grants=(
            RoleGrant(
                uuid4(),
                frozenset(),
                (Boundary(BoundaryKind.ORGANIZATION, target),),
            ),
        ),
    )
    assert await _resolve(caller, str(target)) == str(target)


@pytest.mark.asyncio
async def test_non_admin_cannot_target_other_org():
    caller = _caller(org_id=uuid4())
    with pytest.raises(HTTPException) as exc:
        await _resolve(caller, str(uuid4()))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_can_target_own_org():
    own_org = uuid4()
    assert await _resolve(_caller(org_id=own_org), str(own_org)) == str(own_org)


@pytest.mark.asyncio
async def test_unset_scope_uses_current_authorization_context_org():
    own_org = uuid4()
    assert await _resolve(_caller(org_id=own_org), None) == str(own_org)


@pytest.mark.asyncio
async def test_unset_scope_with_no_org_returns_none():
    assert await _resolve(_caller(org_id=None), None) is None


@pytest.mark.asyncio
async def test_empty_string_treated_as_unset():
    own_org = uuid4()
    assert await _resolve(_caller(org_id=own_org), "") == str(own_org)


@pytest.mark.asyncio
async def test_explicit_global_is_in_reach_for_current_callers():
    assert await _resolve(_caller(org_id=uuid4()), "global") is None


@pytest.mark.asyncio
async def test_garbage_scope_raises_422():
    with pytest.raises(HTTPException) as exc:
        await _resolve(_caller(org_id=uuid4()), "not-a-uuid")
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_uppercase_uuid_accepted_for_admin():
    upper = str(uuid4()).upper()
    assert UUID(await _resolve(admin_caller(), upper)) == UUID(upper)
