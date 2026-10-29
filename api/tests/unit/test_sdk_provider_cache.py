"""Security partitions for the authenticated data-provider cache transport."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared.sdk_provider_cache import _runtime_scope, provider_cache_authority_fingerprint
from src.models.contracts.workflow_permissions import WorkflowGrant, WorkflowPermissionMode
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.explain import Powers, RunUser


def _run_user(*, permissions: frozenset[str] = frozenset({"secrets.read"})) -> RunUser:
    role_id = uuid4()
    org_id = uuid4()
    context = AuthorizationContext(
        user_id=uuid4(),
        home_organization_id=org_id,
        base_role_id=uuid4(),
        is_external=False,
        base_permissions=frozenset({"workflows.execute"}),
        role_grants=(RoleGrant(role_id, permissions, (Boundary(BoundaryKind.ORGANIZATION, org_id),)),),
    )
    return RunUser(context.user_id, context, None, is_active=True)


def test_provider_cache_fingerprint_changes_when_authority_changes():
    """Role/capability/actor changes cannot reuse a stronger cached result."""
    run_user = _run_user()
    powers = Powers(WorkflowPermissionMode.RESTRICTED, (WorkflowGrant(permission="secrets.read"),))
    organization_id = uuid4()
    actor_id = uuid4()

    baseline = provider_cache_authority_fingerprint(run_user, actor_id, powers, organization_id)
    changed_actor = provider_cache_authority_fingerprint(run_user, uuid4(), powers, organization_id)
    revoked_context = replace(
        run_user.ctx,
        role_grants=(
            replace(run_user.ctx.role_grants[0], permissions=frozenset()),
        ),
    )
    revoked_role = provider_cache_authority_fingerprint(
        replace(run_user, ctx=revoked_context), actor_id, powers, organization_id,
    )
    revoked_power = provider_cache_authority_fingerprint(
        run_user, actor_id, Powers(WorkflowPermissionMode.RESTRICTED, ()), organization_id,
    )

    assert baseline != changed_actor
    assert baseline != revoked_role
    assert baseline != revoked_power


@pytest.mark.asyncio
async def test_provider_cache_refuses_non_provider_runtime():
    """Agent and non-provider workflow credentials cannot select this cache."""
    agent_user = SimpleNamespace(runtime_authority=SimpleNamespace(kind="agent_run", workflow_id=None))
    with pytest.raises(HTTPException, match="data-provider workflow credential"):
        await _runtime_scope(SimpleNamespace(), agent_user)

    workflow_user = SimpleNamespace(
        runtime_authority=SimpleNamespace(
            kind="workflow_execution",
            workflow_id=uuid4(),
            organization_id=uuid4(),
            run_user=SimpleNamespace(user_id=uuid4()),
            acting_user_id=uuid4(),
        )
    )
    db = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(id=workflow_user.runtime_authority.workflow_id, type="workflow"))
    )
    with pytest.raises(HTTPException, match="Only data-provider workflows"):
        await _runtime_scope(db, workflow_user)
