"""Global platform-setting routes use evaluator authorization before work."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.models.contracts.access_list import AccessClass, AccessEntry, CurrentGate
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import Caller, decide_for
from src.services.authorization.evaluator import GLOBAL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module_name", "helper_name", "operation_name"),
    [
        ("src.routers.ai_pricing", "_authorize_platform_setting", "AI_PRICING_LIST_OPERATION"),
        ("src.routers.ai_models", "_authorize_platform_setting", "AI_CONNECTIONS_CREATE_OPERATION"),
        ("src.routers.roi_settings", "_authorize_platform_setting", "ROI_SETTINGS_GET_OPERATION"),
        ("src.routers.kubernetes", "_authorize_platform_setting", "KUBERNETES_EXECUTION_UPDATE_OPERATION"),
        ("src.routers.kubernetes", "_authorize_platform_setting", "KUBERNETES_POLICY_UPDATE_OPERATION"),
        ("src.routers.platform.workers", "_authorize_platform_operation", "WORKERS_LIST_OPERATION"),
        ("src.routers.platform.workers", "_authorize_platform_operation", "PLATFORM_QUEUE_OPERATION"),
    ],
)
async def test_platform_setting_dependencies_authorize_the_exact_global_route(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    helper_name: str,
    operation_name: str,
) -> None:
    module = __import__(module_name, fromlist=[helper_name])
    authorize = AsyncMock()
    monkeypatch.setattr(module, "authorize_operation", authorize)
    db = object()
    user = UserPrincipal(
        user_id=uuid4(), email="role-holder@example.test", organization_id=None
    )
    operation = getattr(module, operation_name)
    if helper_name == "_authorize_platform_operation":
        request = SimpleNamespace(
            method=operation.split(" ", 1)[0],
            scope={"route": SimpleNamespace(path=operation.split(" ", 1)[1])},
        )
        await getattr(module, helper_name)(request, db, user)
    else:
        await getattr(module, helper_name)(db, user, operation)

    authorize.assert_awaited_once_with(db, user, operation, GLOBAL)


def _settings_entry() -> AccessEntry:
    return AccessEntry(
        method="GET",
        path="/api/settings/example",
        access_class=AccessClass.PERMISSION,
        current_gate=CurrentGate.EVALUATOR,
        permission="settings.read",
        boundary="platform",
        reason="Global platform setting.",
    )


def _caller(*, permission: bool, identity_admin: bool) -> Caller:
    user_id = uuid4()
    grants = ()
    if permission:
        grants = (
            RoleGrant(
                uuid4(),
                frozenset({"settings.read"}),
                (Boundary(BoundaryKind.PLATFORM),),
            ),
        )
    context = AuthorizationContext(
        user_id=user_id,
        home_organization_id=None,
        base_role_id=uuid4(),
        is_external=False,
        base_permissions=frozenset(),
        role_grants=grants,
    )
    principal = UserPrincipal(
        user_id=user_id,
        email="role-holder@example.test",
        organization_id=None,
        is_superuser=identity_admin,
    )
    return Caller(principal, context)


def test_explicit_global_role_permission_succeeds_without_platform_admin_flag() -> None:
    caller = _caller(permission=True, identity_admin=False)

    assert decide_for(caller, _settings_entry(), GLOBAL).allowed is True


def test_identity_superuser_flag_cannot_grant_a_global_setting_permission() -> None:
    caller = _caller(permission=False, identity_admin=True)

    assert decide_for(caller, _settings_entry(), GLOBAL).allowed is False
