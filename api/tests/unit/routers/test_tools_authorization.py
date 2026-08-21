"""Boundary-aware authorization for the tools listing route."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.org_filter import OrgFilterType
from src.core.principal import UserPrincipal
from src.routers import tools
from src.services.authorization import (
    AuthorizationBoundary,
    AuthorizationBoundaryKind,
    AuthorizationContext,
)


def _user(org_id=None) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="tool-user@example.com",
        organization_id=org_id,
    )


def _authorization(
    user: UserPrincipal,
    *,
    boundary: AuthorizationBoundary,
    capabilities: set[str] | None = None,
) -> AuthorizationContext:
    return AuthorizationContext(
        requester=user,
        effective_actor=user,
        selected_boundary=boundary,
        effective_capabilities=frozenset(capabilities or {"workflows.read"}),
        grant_sources=(),
    )


@pytest.mark.asyncio
async def test_workflow_tools_use_selected_organization_boundary(monkeypatch) -> None:
    org_id = uuid4()
    user = _user(org_id)
    calls = []

    class _Repo:
        def __init__(self, *args, **kwargs) -> None:
            calls.append(("init", args, kwargs))

        async def list_tools_for_filter(self, filter_type, filter_org_id, active_only):
            calls.append(("list", filter_type, filter_org_id, active_only))
            return []

    monkeypatch.setattr(tools, "WorkflowRepository", _Repo)

    response = await tools.list_tools(
        AsyncMock(),
        user,
        _authorization(user, boundary=AuthorizationBoundary.organization(org_id)),
        type="workflow",
        scope=None,
        include_inactive=False,
    )

    assert response.tools == []
    assert calls[-1] == ("list", OrgFilterType.ORG_PLUS_GLOBAL, org_id, True)


@pytest.mark.asyncio
async def test_workflow_tools_platform_boundary_is_global_only(monkeypatch) -> None:
    user = _user(uuid4())
    calls = []

    class _Repo:
        def __init__(self, *args, **kwargs) -> None:
            calls.append(("init", args, kwargs))

        async def list_tools_for_filter(self, filter_type, filter_org_id, active_only):
            calls.append(("list", filter_type, filter_org_id, active_only))
            return []

    monkeypatch.setattr(tools, "WorkflowRepository", _Repo)

    await tools.list_tools(
        AsyncMock(),
        user,
        _authorization(user, boundary=AuthorizationBoundary.platform()),
        type="workflow",
        scope="global",
        include_inactive=False,
    )

    assert calls[-1] == ("list", OrgFilterType.GLOBAL_ONLY, None, True)


@pytest.mark.asyncio
async def test_workflow_tools_deny_unselected_org_scope(monkeypatch) -> None:
    org_id = uuid4()
    other_org_id = uuid4()
    user = _user(org_id)
    list_tools_for_filter = AsyncMock(return_value=[])

    class _Repo:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def list_tools_for_filter(self, *args, **kwargs):
            return await list_tools_for_filter(*args, **kwargs)

    monkeypatch.setattr(tools, "WorkflowRepository", _Repo)

    response = await tools.list_tools(
        AsyncMock(),
        user,
        _authorization(user, boundary=AuthorizationBoundary.organization(org_id)),
        type="workflow",
        scope=str(other_org_id),
        include_inactive=False,
    )

    assert response.tools == []
    list_tools_for_filter.assert_not_awaited()


@pytest.mark.asyncio
async def test_workflow_tools_managed_boundary_uses_repository_visibility(
    monkeypatch,
) -> None:
    user = _user(uuid4())
    customer_org_id = uuid4()
    calls = []

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return [customer_org_id]

    class _Repo:
        def __init__(self, *args, **kwargs) -> None:
            calls.append(("init", args, kwargs))

        async def list_tools_for_filter(self, filter_type, filter_org_id, active_only):
            calls.append(("list", filter_type, filter_org_id, active_only))
            return []

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result())
    monkeypatch.setattr(tools, "WorkflowRepository", _Repo)

    response = await tools.list_tools(
        db,
        user,
        _authorization(
            user,
            boundary=AuthorizationBoundary(
                AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS
            ),
        ),
        type="workflow",
        scope=None,
        include_inactive=False,
    )

    assert response.tools == []
    db.execute.assert_awaited_once()
    assert calls[-1] == ("list", OrgFilterType.ORG_PLUS_GLOBAL, customer_org_id, True)
