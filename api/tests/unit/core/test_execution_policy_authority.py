"""Execution policy authority comes from persisted workflow powers only."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.core.auth import get_execution_context
from src.core.principal import UserPrincipal
from src.models.contracts.workflow_permissions import WorkflowPermissionMode


class _Request:
    query_params: dict[str, str] = {}
    headers: dict[str, str] = {}


def _engine_principal(execution_id: str, attributed_workflow_id) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="engine@example.test",
        organization_id=None,
        is_superuser=True,
        engine_execution_id=execution_id,
        workflow_id=attributed_workflow_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("persisted_mode", "attributed_mode", "expected_authority"),
    [
        (WorkflowPermissionMode.FULL, WorkflowPermissionMode.RESTRICTED, True),
        (WorkflowPermissionMode.RESTRICTED, WorkflowPermissionMode.FULL, False),
    ],
)
async def test_execution_authority_uses_persisted_workflow_not_attribution_claim(
    persisted_mode: WorkflowPermissionMode,
    attributed_mode: WorkflowPermissionMode,
    expected_authority: bool,
) -> None:
    execution_id = uuid4()
    persisted_workflow_id = uuid4()
    attributed_workflow_id = uuid4()
    execution = SimpleNamespace(workflow_id=persisted_workflow_id)
    persisted_workflow = SimpleNamespace(permission_mode=persisted_mode.value)
    db = AsyncMock()

    async def get(model, identifier):
        if model.__name__ == "Execution":
            assert identifier == execution_id
            return execution
        if model.__name__ == "Workflow":
            assert identifier == persisted_workflow_id
            return persisted_workflow
        raise AssertionError(f"unexpected lookup: {model}, {identifier}")

    db.get.side_effect = get
    user = _engine_principal(str(execution_id), attributed_workflow_id)
    attributed_workflow = SimpleNamespace(permission_mode=attributed_mode.value)

    with (
        patch("src.core.auth.get_user_roles", AsyncMock(return_value=([], []))),
        patch(
            "src.services.workflow_permissions.get_default_workflow_permission_mode",
            AsyncMock(return_value=attributed_mode),
        ),
    ):
        context = await get_execution_context(_Request(), user, db)

    assert context.user is user
    assert ("platform_admin" in user.policy_authorities) is expected_authority
    assert user.workflow_id == attributed_workflow_id
    assert attributed_workflow.permission_mode == attributed_mode.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("default_mode", "expected_authority"),
    [
        (WorkflowPermissionMode.FULL, True),
        (WorkflowPermissionMode.RESTRICTED, False),
    ],
)
async def test_inline_execution_authority_uses_the_persisted_default_mode(
    default_mode: WorkflowPermissionMode,
    expected_authority: bool,
) -> None:
    execution_id = uuid4()
    execution = SimpleNamespace(workflow_id=None)
    db = AsyncMock()
    db.get.return_value = execution
    user = _engine_principal(str(execution_id), uuid4())

    with (
        patch("src.core.auth.get_user_roles", AsyncMock(return_value=([], []))),
        patch(
            "src.services.workflow_permissions.get_default_workflow_permission_mode",
            AsyncMock(return_value=default_mode),
        ),
    ):
        await get_execution_context(_Request(), user, db)

    assert ("platform_admin" in user.policy_authorities) is expected_authority


@pytest.mark.asyncio
async def test_direct_superuser_token_never_receives_execution_authority() -> None:
    user = UserPrincipal(user_id=uuid4(), email="admin@example.test", organization_id=None, is_superuser=True)
    db = AsyncMock()

    with patch("src.core.auth.get_user_roles", AsyncMock(return_value=([], []))):
        await get_execution_context(_Request(), user, db)

    assert user.policy_authorities == frozenset()
    db.get.assert_not_awaited()
