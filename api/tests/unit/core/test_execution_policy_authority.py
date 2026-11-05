"""Execution policy authority comes from persisted workflow powers only."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from shared.builtin_roles import USER_ROLE_ID
from shared.policies.evaluate import resolve_policy_authority, resolve_user_field
from src.core.auth import get_execution_context
from src.core.principal import UserPrincipal
from src.models.contracts.workflow_permissions import WorkflowPermissionMode
from src.services.access_check_policies import load_policy_principal
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller, permitted_organizations
from src.services.authorization.explain import Powers, RunUser
from src.services.authorization.runtime import RuntimeAuthority


class _Request:
    query_params: dict[str, str] = {}
    headers: dict[str, str] = {}


def _run_user(*, organization_id=None) -> RunUser:
    user_id = uuid4()
    return RunUser(
        user_id=user_id,
        ctx=AuthorizationContext(
            user_id=user_id,
            home_organization_id=organization_id,
            base_role_id=USER_ROLE_ID,
            is_external=False,
            base_permissions=frozenset(),
        ),
        identity_kind=None,
        is_active=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("persisted_mode", "expected_authority"),
    [
        (WorkflowPermissionMode.FULL, True),
        (WorkflowPermissionMode.RESTRICTED, False),
    ],
)
async def test_execution_policy_authority_uses_persisted_workflow_without_changing_role_facts(
    persisted_mode: WorkflowPermissionMode,
    expected_authority: bool,
) -> None:
    execution_id = uuid4()
    persisted_workflow_id = uuid4()
    execution = SimpleNamespace(workflow_id=persisted_workflow_id)
    db = AsyncMock()
    db.get.return_value = execution
    run_user = _run_user()
    db.execute.return_value = SimpleNamespace(
        one=lambda: ("runner@example.test", False, False)
    )

    with (
        patch(
            "src.services.access_check_policies.load_run_user",
            AsyncMock(return_value=run_user),
        ),
        patch(
            "src.services.access_check_policies.load_powers",
            AsyncMock(return_value=Powers(persisted_mode, ())),
        ),
        patch(
            "src.services.access_check_policies.get_user_roles",
            AsyncMock(return_value=([], [])),
        ),
    ):
        user = await load_policy_principal(db, run_user.user_id, execution_id)

    assert user is not None
    assert user.user_id == run_user.user_id
    assert ("platform_admin" in user.policy_authorities) is expected_authority
    # A Full workflow holds the platform-admin policy authority, which the
    # legacy ``user.is_platform_admin`` term also honors; it adds no role.
    assert user.role_ids == []
    assert resolve_user_field(user, "is_platform_admin") is expected_authority
    assert resolve_policy_authority(user, "platform_admin") is expected_authority
    db.get.assert_awaited_once()
    assert db.get.await_args.args[1] == execution_id


@pytest.mark.asyncio
async def test_inline_policy_principal_has_no_workflow_power_even_when_default_is_full() -> None:
    db = AsyncMock()
    run_user = _run_user()
    db.execute.return_value = SimpleNamespace(
        one=lambda: ("runner@example.test", False, False)
    )

    with (
        patch(
            "src.services.access_check_policies.load_run_user",
            AsyncMock(return_value=run_user),
        ),
        patch(
            "src.services.access_check_policies.get_user_roles",
            AsyncMock(return_value=([], [])),
        ),
    ):
        user = await load_policy_principal(db, run_user.user_id, None)

    assert user is not None
    assert user.policy_authorities == frozenset()
    assert resolve_user_field(user, "is_platform_admin") is False
    assert resolve_policy_authority(user, "platform_admin") is False
    db.get.assert_not_awaited()


def test_full_runtime_power_is_limited_to_the_original_users_reach() -> None:
    home_org = uuid4()
    outside_org = uuid4()
    run_user = _run_user(organization_id=home_org)
    principal = UserPrincipal(
        user_id=uuid4(),
        email="actor@example.test",
        organization_id=home_org,
        # This is an acting-user fact. It does not widen the original run
        # user's reach used by the runtime authority.
        is_superuser=True,
    )
    principal.runtime_authority = RuntimeAuthority(
        kind="workflow_execution",
        credential_id=uuid4(),
        run_user=run_user,
        powers=Powers(WorkflowPermissionMode.FULL, ()),
        acting_user_id=principal.user_id,
        organization_id=home_org,
        workflow_id=uuid4(),
        started_by_user_id=run_user.user_id,
        root_execution_id=uuid4(),
    )

    reach = permitted_organizations(
        Caller(principal, run_user.ctx), "tables.readwrite"
    )

    assert reach.covers(home_org)
    assert reach.covers(None)
    assert not reach.covers(outside_org)


@pytest.mark.asyncio
async def test_direct_superuser_token_never_receives_execution_authority() -> None:
    user = UserPrincipal(user_id=uuid4(), email="admin@example.test", organization_id=None, is_superuser=True)
    db = AsyncMock()

    with patch("src.core.auth.get_user_roles", AsyncMock(return_value=([], []))):
        await get_execution_context(_Request(), user, db)

    assert user.policy_authorities == frozenset()
    db.get.assert_not_awaited()
