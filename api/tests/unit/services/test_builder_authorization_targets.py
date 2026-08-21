"""Builder target discovery honors Role-assignment boundaries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.services.builder.authorization_targets import (
    discover_builder_authorization_targets,
    global_builder_tool_names,
)


def _result(*, rows=None, scalars=None):
    result = MagicMock()
    result.all.return_value = rows or []
    result.scalars.return_value.all.return_value = scalars or []
    return result


def _assignment_row(
    user_id,
    *,
    capabilities,
    kind,
    organization_id=None,
    organization_group_id=None,
):
    role = SimpleNamespace(
        id=uuid4(),
        capabilities=capabilities,
    )
    assignment = SimpleNamespace(id=uuid4(), user_id=user_id, role_id=role.id)
    boundary = SimpleNamespace(
        boundary_kind=kind,
        organization_id=organization_id,
        organization_group_id=organization_group_id,
    )
    return assignment, role, boundary


def test_global_builder_tools_exclude_live_write_operations() -> None:
    names = {
        name
        for name, _scopes in global_builder_tool_names(
            frozenset({"platform.superuser"})
        )
    }

    assert "bifrost_list_roles" in names
    assert "bifrost_create_role" not in names
    assert "bifrost_write_file" not in names


@pytest.mark.asyncio
async def test_discovers_direct_group_managed_and_platform_targets() -> None:
    requester = UserPrincipal(
        user_id=uuid4(),
        email="builder@example.com",
        organization_id=uuid4(),
    )
    provider_id = requester.organization_id
    direct_id = uuid4()
    group_id = uuid4()
    group_organization_id = uuid4()
    managed_id = uuid4()
    rows = [
        _assignment_row(
            requester.user_id,
            capabilities=["builder.execute", "agents.readwrite"],
            kind="organization",
            organization_id=direct_id,
        ),
        _assignment_row(
            requester.user_id,
            capabilities=["builder.read"],
            kind="organization_group",
            organization_group_id=group_id,
        ),
        _assignment_row(
            requester.user_id,
            capabilities=["builder.read", "builder.execute", "workflows.readwrite"],
            kind="managed_organizations",
        ),
        _assignment_row(
            requester.user_id,
            capabilities=["builder.execute", "repository.readwrite"],
            kind="platform",
        ),
    ]
    organizations = [
        SimpleNamespace(id=provider_id, name="Provider", is_provider=True),
        SimpleNamespace(id=direct_id, name="Direct", is_provider=False),
        SimpleNamespace(id=group_organization_id, name="Group", is_provider=False),
        SimpleNamespace(id=managed_id, name="Managed", is_provider=False),
    ]
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _result(rows=rows),
            _result(scalars=organizations),
            _result(rows=[(group_id, group_organization_id)]),
        ]
    )

    discovered = await discover_builder_authorization_targets(
        db,
        requester=requester,
    )

    targets = {target.id: target for target in discovered.organizations}
    assert provider_id not in targets
    assert targets[direct_id].can_execute
    assert targets[direct_id].can_build_resources
    assert targets[group_organization_id].can_read
    assert targets[group_organization_id].can_execute
    assert targets[managed_id].can_build_resources
    assert discovered.can_view_all
    assert discovered.can_open_global_workspace


@pytest.mark.asyncio
async def test_platform_admin_wildcard_discovers_every_active_boundary() -> None:
    requester = UserPrincipal(
        user_id=uuid4(),
        email="admin@example.com",
        organization_id=uuid4(),
    )
    rows = [
        _assignment_row(
            requester.user_id,
            capabilities=["platform.superuser"],
            kind="platform",
        )
    ]
    organizations = [
        SimpleNamespace(id=requester.organization_id, name="Provider", is_provider=True),
        SimpleNamespace(id=uuid4(), name="Customer", is_provider=False),
    ]
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _result(rows=rows),
            _result(scalars=organizations),
            _result(rows=[]),
        ]
    )

    discovered = await discover_builder_authorization_targets(
        db,
        requester=requester,
    )

    assert {target.id for target in discovered.organizations} == {
        organization.id for organization in organizations
    }
    assert all(target.can_build_resources for target in discovered.organizations)
    assert discovered.is_platform_admin
    assert discovered.can_view_all
    assert discovered.can_open_global_workspace
