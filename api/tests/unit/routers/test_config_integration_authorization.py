"""Admission regressions for config and integration management routes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.services.authorization.enforce import Caller
from src.services.authorization.reach import OrgReach


@pytest.mark.asyncio
async def test_config_omitted_scope_filters_at_permission_reach_before_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.routers.config as config_router

    org_id = uuid4()
    user = UserPrincipal(
        user_id=uuid4(), email="role@example.test", organization_id=org_id
    )
    caller = Caller(user, None)
    reach = OrgReach(organization_ids=frozenset({org_id}))
    list_in_reach = AsyncMock(return_value=[])

    monkeypatch.setattr(config_router, "load_caller", AsyncMock(return_value=caller))
    monkeypatch.setattr(config_router, "operation_reach", lambda *_args: reach)
    monkeypatch.setattr(
        config_router,
        "ConfigRepository",
        lambda *_args, **_kwargs: SimpleNamespace(list_configs_in_reach=list_in_reach),
    )

    result = await config_router.get_config(
        ctx=SimpleNamespace(db=object(), org_id=org_id),
        user=user,
        scope=None,
    )

    assert result == []
    list_in_reach.assert_awaited_once_with(reach, include_global_fallback=True)


@pytest.mark.asyncio
async def test_mapping_list_applies_permission_reach_before_mapping_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.routers.integrations as integrations_router

    allowed_org = uuid4()
    user = UserPrincipal(
        user_id=uuid4(), email="role@example.test", organization_id=allowed_org
    )
    caller = Caller(user, None)
    reach = OrgReach(organization_ids=frozenset({allowed_org}))
    list_mappings = AsyncMock(return_value=[])

    monkeypatch.setattr(integrations_router, "load_caller", AsyncMock(return_value=caller))
    monkeypatch.setattr(integrations_router, "operation_reach", lambda *_args: reach)
    monkeypatch.setattr(
        integrations_router,
        "IntegrationsRepository",
        lambda *_args: SimpleNamespace(
            get_integration_by_id=AsyncMock(return_value=object()),
            list_mappings_for_integration=list_mappings,
        ),
    )

    integration_id = uuid4()
    response = await integrations_router.list_mappings(
        integration_id=integration_id,
        ctx=SimpleNamespace(db=object()),
        user=user,
    )

    assert response.items == []
    assert response.total == 0
    list_mappings.assert_awaited_once_with(integration_id, reach)


@pytest.mark.asyncio
async def test_mapping_operation_uses_persisted_mapping_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.routers.integrations as integrations_router

    target_org = uuid4()
    user = UserPrincipal(
        user_id=uuid4(), email="role@example.test", organization_id=target_org
    )
    mapping = SimpleNamespace(organization_id=target_org)
    repo = SimpleNamespace(get_mapping_by_id=AsyncMock(return_value=mapping))
    caller = Caller(user, None)
    require = MagicMock()

    monkeypatch.setattr(integrations_router, "load_caller", AsyncMock(return_value=caller))
    monkeypatch.setattr(integrations_router, "require_operation", require)

    result = await integrations_router._mapping_for_operation(
        repo,
        SimpleNamespace(db=object()),
        user,
        uuid4(),
        uuid4(),
        "integrations.mappings.get",
    )

    assert result is mapping
    require.assert_called_once_with(
        caller,
        "integrations.mappings.get",
        integrations_router.org_target(target_org),
    )
