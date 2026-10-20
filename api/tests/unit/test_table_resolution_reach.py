"""Reach checks for direct-ID table resolution."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
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
from shared.table_resolution import assert_resolved_table_reachable, get_table_or_404


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


@pytest.mark.asyncio
async def test_solution_name_lookup_keeps_documented_global_fallback(monkeypatch):
    """An own Solution name lookup may cascade from its org to Global."""
    from shared import table_resolution

    home = uuid4()
    caller = _caller(home=home, original_user=uuid4())
    table = SimpleNamespace(
        name="shared_inventory", organization_id=None, solution_id=None
    )
    ctx = SimpleNamespace(
        db=object(),
        user=caller.principal,
        app_id="app-install",
        solution_id=None,
        caller_solution_id=None,
    )
    reachable = AsyncMock()
    monkeypatch.setattr(
        table_resolution, "resolve_target_org_safe", AsyncMock(return_value=home)
    )
    monkeypatch.setattr(
        table_resolution, "resolve_effective_solution_id", AsyncMock(return_value=uuid4())
    )
    monkeypatch.setattr(
        table_resolution,
        "resolve_solution_table_by_name",
        AsyncMock(return_value=table),
    )
    monkeypatch.setattr(table_resolution, "assert_resolved_table_reachable", reachable)
    monkeypatch.setattr(table_resolution, "TableRepository", Mock())

    resolved = await get_table_or_404(ctx, "shared_inventory", scope=str(home))

    assert resolved is table
    reachable.assert_awaited_once_with(
        ctx, table, target_org_id=home, scope=None
    )


@pytest.mark.asyncio
async def test_ordinary_name_lookup_uses_the_cascade_under_an_explicit_scope(monkeypatch):
    """A workspace workflow names a Global table from an org scope; the
    canonical org-then-Global cascade decides, not an exact-scope check."""
    from shared import table_resolution

    home = uuid4()
    caller = _caller(home=home, original_user=uuid4())
    table = SimpleNamespace(
        name="onboarding_runs", organization_id=None, solution_id=None
    )
    ctx = SimpleNamespace(
        db=object(),
        user=caller.principal,
        app_id=None,
        solution_id=None,
        caller_solution_id=None,
    )
    reachable = AsyncMock()
    repo = Mock()
    repo.get_by_name = AsyncMock(return_value=table)
    monkeypatch.setattr(
        table_resolution, "resolve_target_org_safe", AsyncMock(return_value=home)
    )
    monkeypatch.setattr(
        table_resolution, "resolve_effective_solution_id", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        table_resolution, "resolve_solution_table_by_name", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(table_resolution, "assert_resolved_table_reachable", reachable)
    monkeypatch.setattr(table_resolution, "TableRepository", Mock(return_value=repo))

    resolved = await get_table_or_404(ctx, "onboarding_runs", scope=str(home))

    assert resolved is table
    repo.get_by_name.assert_awaited_once_with("onboarding_runs")
    reachable.assert_awaited_once_with(ctx, table, target_org_id=home, scope=None)


@pytest.mark.asyncio
async def test_uuid_lookup_keeps_the_explicit_scope(monkeypatch):
    from shared import table_resolution

    home = uuid4()
    caller = _caller(home=home, original_user=uuid4())
    table_id = uuid4()
    table = SimpleNamespace(
        id=table_id, name="onboarding_runs", organization_id=None, solution_id=None
    )
    db = SimpleNamespace(get=AsyncMock(return_value=table))
    ctx = SimpleNamespace(
        db=db, user=caller.principal, app_id=None, solution_id=None, caller_solution_id=None
    )
    reachable = AsyncMock()
    monkeypatch.setattr(
        table_resolution, "resolve_target_org_safe", AsyncMock(return_value=home)
    )
    monkeypatch.setattr(
        table_resolution, "resolve_effective_solution_id", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(table_resolution, "assert_resolved_table_reachable", reachable)
    monkeypatch.setattr(table_resolution, "TableRepository", Mock())

    await get_table_or_404(ctx, str(table_id), scope=str(home))

    assert reachable.await_args_list[-1].kwargs["scope"] == str(home)
