"""Authorization gates for platform runtime package management."""

from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.routers.packages import _require_package_access
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    *,
    capabilities: set[str],
    boundary: AuthorizationBoundary,
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="builder@example.com",
        organization_id=uuid4(),
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary,
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


def test_package_listing_requires_repository_read() -> None:
    authorization = _authorization(
        capabilities=set(),
        boundary=AuthorizationBoundary.platform(),
    )

    with pytest.raises(HTTPException) as exc:
        _require_package_access(authorization, write=False)

    assert exc.value.status_code == 403


def test_package_mutation_requires_repository_readwrite() -> None:
    authorization = _authorization(
        capabilities={"repository.read"},
        boundary=AuthorizationBoundary.platform(),
    )

    with pytest.raises(HTTPException) as exc:
        _require_package_access(authorization, write=True)

    assert exc.value.status_code == 403


def test_package_access_requires_platform_boundary() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        capabilities={"repository.readwrite"},
        boundary=AuthorizationBoundary.organization(organization_id),
    )

    with pytest.raises(HTTPException) as exc:
        _require_package_access(authorization, write=True)

    assert exc.value.status_code == 409


def test_platform_builder_can_manage_runtime_packages() -> None:
    authorization = _authorization(
        capabilities={"repository.readwrite"},
        boundary=AuthorizationBoundary.platform(),
    )

    _require_package_access(authorization, write=True)
