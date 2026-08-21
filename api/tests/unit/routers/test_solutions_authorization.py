from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.orm.solutions import Solution
from src.routers import solutions
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    boundary: AuthorizationBoundary,
    *capabilities: str,
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="builder@example.com",
        organization_id=uuid4(),
        name="Builder",
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary,
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


def test_install_target_defaults_to_the_visible_organization() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        AuthorizationBoundary.organization(organization_id),
        "solutions.deploy.execute",
    )

    assert (
        solutions._selected_install_organization(
            authorization,
            None,
            was_explicit=False,
        )
        == organization_id
    )


def test_install_target_defaults_to_global_only_in_platform_context() -> None:
    authorization = _authorization(
        AuthorizationBoundary.platform(),
        "solutions.deploy.execute",
    )

    assert (
        solutions._selected_install_organization(
            authorization,
            None,
            was_explicit=False,
        )
        is None
    )


def test_install_target_rejects_managed_collection_context() -> None:
    authorization = _authorization(
        AuthorizationBoundary.managed_organizations(),
        "solutions.deploy.execute",
    )

    with pytest.raises(HTTPException) as exc_info:
        solutions._selected_install_organization(
            authorization,
            None,
            was_explicit=False,
        )

    assert exc_info.value.status_code == 409
    assert "specific organization or Global" in exc_info.value.detail


@pytest.mark.asyncio
async def test_solution_mutation_requires_capability_before_storage() -> None:
    authorization = _authorization(AuthorizationBoundary.platform(), "solutions.read")
    db = SimpleNamespace(get=pytest.fail)

    with pytest.raises(HTTPException) as exc_info:
        await solutions._authorized_solution(
            SimpleNamespace(db=db),
            authorization,
            uuid4(),
            write=True,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Missing required capability: solutions.readwrite"


@pytest.mark.asyncio
async def test_solution_resource_must_match_the_selected_boundary() -> None:
    selected_organization_id = uuid4()
    solution = Solution(
        id=uuid4(),
        slug="customer-app",
        name="Customer App",
        organization_id=uuid4(),
    )

    class _DB:
        async def get(self, model, row_id):  # noqa: ANN001, ANN201
            assert model is Solution
            assert row_id == solution.id
            return solution

    authorization = _authorization(
        AuthorizationBoundary.organization(selected_organization_id),
        "solutions.read",
    )

    with pytest.raises(HTTPException) as exc_info:
        await solutions._authorized_solution(
            SimpleNamespace(db=_DB()),
            authorization,
            solution.id,
        )

    assert exc_info.value.status_code == 409
    assert str(solution.organization_id) in exc_info.value.detail
