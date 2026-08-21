"""Boundary and capability gates for Workflow API-key administration."""

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.orm.workflows import Workflow
from src.routers.workflow_keys import (
    _require_workflow_key_mutation,
)
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    *,
    organization_id: UUID,
    capabilities: set[str],
    boundary: AuthorizationBoundary | None = None,
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="builder@example.com",
        organization_id=organization_id,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary
        or AuthorizationBoundary.organization(organization_id),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


def _workflow(organization_id: UUID | None) -> Workflow:
    return Workflow(
        id=uuid4(),
        name="Sync customer data",
        function_name="sync_customer_data",
        path="workflows/customer.py",
        organization_id=organization_id,
    )


def test_key_mutation_requires_workflow_readwrite() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        organization_id=organization_id,
        capabilities={"workflows.read"},
    )

    with pytest.raises(HTTPException) as exc:
        _require_workflow_key_mutation(authorization, _workflow(organization_id))

    assert exc.value.status_code == 403


def test_key_mutation_rejects_cross_boundary_workflow() -> None:
    authorization = _authorization(
        organization_id=uuid4(),
        capabilities={"workflows.readwrite"},
    )

    with pytest.raises(HTTPException) as exc:
        _require_workflow_key_mutation(authorization, _workflow(uuid4()))

    assert exc.value.status_code == 409


def test_global_workflow_key_requires_platform_boundary() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        organization_id=organization_id,
        capabilities={"workflows.readwrite"},
        boundary=AuthorizationBoundary.platform(),
    )

    _require_workflow_key_mutation(authorization, _workflow(None))
