from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.routers.auth import list_authorization_targets
from src.services.authorization_targets import (
    AuthorizationOrganizationTarget,
    AuthorizationTargets,
)


@pytest.mark.asyncio
async def test_authorization_target_endpoint_labels_exact_managed_and_global(
    monkeypatch,
) -> None:
    organization_id = uuid4()
    discovered = AuthorizationTargets(
        organizations=(
            AuthorizationOrganizationTarget(
                id=organization_id,
                name="Northwind",
                is_provider=False,
                capabilities=frozenset({"agents.read", "agents.readwrite"}),
                role_ids=frozenset({uuid4()}),
            ),
        ),
        managed_capabilities=frozenset({"builder.read"}),
        platform_capabilities=frozenset({"solutions.publish.execute"}),
    )
    discover = AsyncMock(return_value=discovered)
    monkeypatch.setattr(
        "src.services.authorization_targets.discover_authorization_targets",
        discover,
    )
    requester = UserPrincipal(
        user_id=uuid4(),
        email="publisher@example.com",
        organization_id=None,
    )

    response = await list_authorization_targets(requester, SimpleNamespace())

    assert [target.boundary for target in response.targets] == [
        f"organization:{organization_id}",
        "managed_organizations",
        "platform",
    ]
    assert response.targets[0].label == "Northwind"
    assert response.targets[0].capabilities == ["agents.read", "agents.readwrite"]
    assert response.targets[2].label == "Global"
    discover.assert_awaited_once()


@pytest.mark.asyncio
async def test_authorization_target_endpoint_rejects_external_sessions() -> None:
    requester = UserPrincipal(
        user_id=uuid4(),
        email="guest@example.com",
        organization_id=None,
        is_external=True,
    )

    with pytest.raises(HTTPException) as exc:
        await list_authorization_targets(requester, SimpleNamespace())

    assert exc.value.status_code == 403
