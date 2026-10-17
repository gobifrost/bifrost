"""Reach checks for direct-ID table resolution."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared.builtin_roles import USER_ROLE_ID
from src.core.principal import UserPrincipal
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import Caller
from shared.table_resolution import assert_resolved_table_reachable


def _caller(*, home, original_user, reached=()):
    grants = tuple(
        RoleGrant(uuid4(), frozenset(), (Boundary(BoundaryKind.ORGANIZATION, org_id),))
        for org_id in reached
    )
    principal = UserPrincipal(
        user_id=uuid4(), email="worker@fabrikam.example", organization_id=home
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=original_user,
            home_organization_id=home,
            base_role_id=USER_ROLE_ID,
            is_external=False,
            base_permissions=frozenset(),
            role_grants=grants,
        ),
    )


@pytest.mark.asyncio
async def test_foreign_uuid_table_is_hidden_without_original_user_reach(monkeypatch):
    from src.services.authorization import enforce

    home, foreign = uuid4(), uuid4()
    caller = _caller(home=home, original_user=uuid4())
    monkeypatch.setattr(enforce, "load_caller", AsyncMock(return_value=caller))
    ctx = SimpleNamespace(db=object(), user=caller.principal)
    table = SimpleNamespace(name="inventory", organization_id=foreign)

    with pytest.raises(HTTPException, match="not found") as exc_info:
        await assert_resolved_table_reachable(
            ctx, table, target_org_id=home, scope=None
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_reached_foreign_uuid_table_is_allowed(monkeypatch):
    from src.services.authorization import enforce

    home, reached = uuid4(), uuid4()
    caller = _caller(home=home, original_user=uuid4(), reached=(reached,))
    monkeypatch.setattr(enforce, "load_caller", AsyncMock(return_value=caller))
    ctx = SimpleNamespace(db=object(), user=caller.principal)
    table = SimpleNamespace(name="inventory", organization_id=reached)

    await assert_resolved_table_reachable(ctx, table, target_org_id=home, scope=None)


@pytest.mark.asyncio
async def test_explicit_scope_cannot_select_another_reached_table(monkeypatch):
    from src.services.authorization import enforce

    home, explicit, other = uuid4(), uuid4(), uuid4()
    caller = _caller(home=home, original_user=uuid4(), reached=(explicit, other))
    monkeypatch.setattr(enforce, "load_caller", AsyncMock(return_value=caller))
    ctx = SimpleNamespace(db=object(), user=caller.principal)
    table = SimpleNamespace(name="inventory", organization_id=other)

    with pytest.raises(HTTPException) as exc_info:
        await assert_resolved_table_reachable(
            ctx, table, target_org_id=explicit, scope=str(explicit)
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_global_fallback_remains_reachable_from_home_scope(monkeypatch):
    from src.services.authorization import enforce

    home = uuid4()
    caller = _caller(home=home, original_user=uuid4())
    monkeypatch.setattr(enforce, "load_caller", AsyncMock(return_value=caller))
    ctx = SimpleNamespace(db=object(), user=caller.principal)
    table = SimpleNamespace(name="shared_inventory", organization_id=None)

    await assert_resolved_table_reachable(ctx, table, target_org_id=home, scope=None)
