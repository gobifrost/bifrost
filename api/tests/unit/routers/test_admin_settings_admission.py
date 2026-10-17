"""Focused admission seams for Global administrative settings."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import Caller


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("permission", "boundary", "write", "allowed"),
    [
        ("settings.read", BoundaryKind.ORGANIZATION, False, False),
        ("settings.read", BoundaryKind.PLATFORM, False, True),
        ("settings.read", BoundaryKind.PLATFORM, True, False),
    ],
)
async def test_oauth_settings_requires_named_permission_at_global(
    monkeypatch: pytest.MonkeyPatch,
    permission: str,
    boundary: BoundaryKind,
    write: bool,
    allowed: bool,
) -> None:
    import src.routers.oauth_config as router

    org_id = uuid4()
    user = UserPrincipal(user_id=uuid4(), email="role@example.test", organization_id=org_id)
    caller = Caller(
        user,
        AuthorizationContext(
            user_id=user.user_id,
            home_organization_id=org_id,
            base_role_id=uuid4(),
            is_external=False,
            base_permissions=frozenset(),
            role_grants=(
                RoleGrant(
                    uuid4(),
                    frozenset({permission}),
                    (Boundary(boundary, None if boundary == BoundaryKind.PLATFORM else org_id),),
                ),
            ),
        ),
    )
    monkeypatch.setattr(router, "load_caller", AsyncMock(return_value=caller))

    if allowed:
        await router._authorize_oauth_settings(SimpleNamespace(db=object()), user, write=write)
    else:
        with pytest.raises(HTTPException) as exc_info:
            await router._authorize_oauth_settings(SimpleNamespace(db=object()), user, write=write)
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_claim_target_authorizes_the_selected_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.routers.claims as router

    org_id = uuid4()
    user = UserPrincipal(user_id=uuid4(), email="role@example.test", organization_id=org_id)
    caller = object()
    require = MagicMock()
    monkeypatch.setattr(router, "load_caller", AsyncMock(return_value=caller))
    monkeypatch.setattr(router, "require_operation", require)

    target = await router._resolve_target_org(
        SimpleNamespace(db=object(), org_id=uuid4()), user, str(org_id), "claims.get"
    )

    assert target == org_id
    require.assert_called_once_with(caller, "claims.get", router.org_target(org_id))
