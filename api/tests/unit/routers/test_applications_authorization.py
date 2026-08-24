"""Boundary/capability gates for Application administration."""

from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.orm.applications import Application
from src.services.application_authorization import (
    authorized_application_repository,
    require_application_mutation,
    selected_application_organization_id,
)
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


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


def _application(organization_id: UUID | None) -> Application:
    return Application(
        id=uuid4(),
        name="Expense tracker",
        slug=f"expense-tracker-{uuid4().hex[:8]}",
        repo_path="apps/expense-tracker",
        organization_id=organization_id,
    )


def test_application_repository_is_bound_to_selected_organization() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        home_organization_id=organization_id,
        capabilities={"apps.read"},
    )

    repo = authorized_application_repository(MagicMock(), authorization)

    assert repo.org_id == organization_id
    assert repo.user_id == authorization.requester.user_id
    assert repo.bypass_resource_roles is False
    assert repo.bypass_resource_admission is False


def test_platform_boundary_targets_global_applications() -> None:
    authorization = _authorization(
        home_organization_id=uuid4(),
        capabilities={"apps.readwrite"},
        boundary=AuthorizationBoundary.platform(),
    )

    assert selected_application_organization_id(authorization) is None
    require_application_mutation(authorization, _application(None))


def test_application_mutation_requires_readwrite_capability() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        home_organization_id=organization_id,
        capabilities={"apps.read"},
    )

    with pytest.raises(HTTPException) as exc:
        require_application_mutation(
            authorization, _application(organization_id)
        )

    assert exc.value.status_code == 403


def test_application_mutation_rejects_cross_boundary_resource() -> None:
    authorization = _authorization(
        home_organization_id=uuid4(),
        capabilities={"apps.readwrite"},
    )

    with pytest.raises(HTTPException) as exc:
        require_application_mutation(authorization, _application(uuid4()))

    assert exc.value.status_code == 409


def test_managed_organizations_is_not_an_application_target() -> None:
    authorization = _authorization(
        home_organization_id=uuid4(),
        capabilities={"apps.readwrite"},
        boundary=AuthorizationBoundary.managed_organizations(),
    )

    with pytest.raises(HTTPException) as exc:
        selected_application_organization_id(authorization)

    assert exc.value.status_code == 409
