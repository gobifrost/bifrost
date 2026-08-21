"""Boundary/capability gates for Workflow administration."""

from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models import Workflow
from src.services.authorization import AuthorizationBoundary, AuthorizationContext
from src.services.workflow_authorization import (
    authorized_workflow_repository,
    require_workflow_mutation,
    selected_workflow_organization_id,
)


def _authorization(
    *,
    home_organization_id: UUID,
    capabilities: set[str],
    boundary: AuthorizationBoundary | None = None,
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="builder@example.com",
        organization_id=home_organization_id,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary
        or AuthorizationBoundary.organization(home_organization_id),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


def _workflow(organization_id: UUID | None) -> Workflow:
    return Workflow(
        id=uuid4(),
        name="sync_expenses",
        function_name="sync_expenses",
        path="workflows/expenses.py",
        organization_id=organization_id,
    )


def test_workflow_repository_is_bound_to_selected_organization() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        home_organization_id=organization_id,
        capabilities={"workflows.read"},
    )

    repo = authorized_workflow_repository(MagicMock(), authorization)

    assert repo.org_id == organization_id
    assert repo.user_id == authorization.requester.user_id
    assert repo.bypass_resource_roles is False
    assert repo.bypass_resource_admission is False


def test_platform_boundary_targets_global_workflows() -> None:
    authorization = _authorization(
        home_organization_id=uuid4(),
        capabilities={"workflows.readwrite"},
        boundary=AuthorizationBoundary.platform(),
    )

    assert selected_workflow_organization_id(authorization) is None
    require_workflow_mutation(authorization, _workflow(None))


def test_workflow_mutation_requires_readwrite_capability() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        home_organization_id=organization_id,
        capabilities={"workflows.read"},
    )

    with pytest.raises(HTTPException) as exc:
        require_workflow_mutation(
            authorization, _workflow(organization_id)
        )

    assert exc.value.status_code == 403


def test_workflow_mutation_rejects_cross_boundary_resource() -> None:
    authorization = _authorization(
        home_organization_id=uuid4(),
        capabilities={"workflows.readwrite"},
    )

    with pytest.raises(HTTPException) as exc:
        require_workflow_mutation(authorization, _workflow(uuid4()))

    assert exc.value.status_code == 409


def test_managed_organizations_is_not_a_workflow_target() -> None:
    authorization = _authorization(
        home_organization_id=uuid4(),
        capabilities={"workflows.readwrite"},
        boundary=AuthorizationBoundary.managed_organizations(),
    )

    with pytest.raises(HTTPException) as exc:
        selected_workflow_organization_id(authorization)

    assert exc.value.status_code == 409
