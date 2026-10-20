"""Authorization rules shared by the SDK knowledge endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from shared.sdk_knowledge import (
    KnowledgeAuthorizationError,
    require_direct_knowledge_access,
    resolve_knowledge_scope,
)
from src.core.principal import UserPrincipal
from src.models.contracts.access_list import AccessClass, AccessEntry, CurrentGate
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import Caller, decide_for
from src.services.authorization.evaluator import cross_org


def _caller(
    *,
    organization_id=None,
    grants: tuple[RoleGrant, ...] = (),
    external: bool = False,
    runtime_powers=...,
) -> Caller:
    user_id = uuid4()
    context = AuthorizationContext(
        user_id=user_id,
        home_organization_id=organization_id,
        base_role_id=uuid4(),
        base_permissions=frozenset(),
        role_grants=grants,
        is_external=external,
    )
    runtime = None if runtime_powers is ... else SimpleNamespace(powers=runtime_powers)
    return Caller(
        UserPrincipal(
            user_id=user_id,
            email="knowledge@example.test",
            organization_id=organization_id,
            is_external=external,
            runtime_authority=runtime,
        ),
        context,
    )


def _read_entry() -> AccessEntry:
    return AccessEntry(
        method="GET",
        path="/api/sdk/knowledge/search",
        access_class=AccessClass.PERMISSION,
        current_gate=CurrentGate.EVALUATOR,
        permission="knowledge.read",
        boundary="organization",
        reason="Knowledge search.",
    )


def test_explicit_knowledge_role_grant_succeeds_without_platform_admin() -> None:
    organization_id = uuid4()
    caller = _caller(
        organization_id=organization_id,
        grants=(
            RoleGrant(
                uuid4(),
                frozenset({"knowledge.read"}),
                (Boundary(BoundaryKind.ORGANIZATION, organization_id),),
            ),
        ),
    )

    assert decide_for(caller, _read_entry(), cross_org(organization_id)).allowed


def test_knowledge_scope_rejects_unreachable_organization() -> None:
    caller = _caller(organization_id=uuid4())

    with pytest.raises(KnowledgeAuthorizationError, match="outside") as exc:
        resolve_knowledge_scope(str(uuid4()), caller=caller)

    assert exc.value.status_code == 403


def test_direct_external_knowledge_is_denied_but_explicit_runtime_powers_are_not() -> None:
    direct_external = _caller(organization_id=uuid4(), external=True)
    with pytest.raises(KnowledgeAuthorizationError) as exc:
        require_direct_knowledge_access(direct_external)
    assert exc.value.status_code == 403

    workflow_external = _caller(
        organization_id=uuid4(), external=True, runtime_powers=object()
    )
    require_direct_knowledge_access(workflow_external)
