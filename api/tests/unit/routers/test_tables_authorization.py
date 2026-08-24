"""Boundary/capability gates for Table schema administration."""

from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.orm.tables import Table
from src.routers.tables import (
    _resolve_attribution,
    _require_table_mutation,
    _selected_table_organization_id,
    _table_repository,
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


def _table(organization_id: UUID | None) -> Table:
    return Table(
        id=uuid4(),
        name="expenses",
        organization_id=organization_id,
    )


def test_table_repository_is_bound_to_selected_organization() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        home_organization_id=organization_id,
        capabilities={"tables.read"},
    )
    ctx = MagicMock()

    repo = _table_repository(ctx, authorization)

    assert repo.org_id == organization_id
    assert repo.user_id == authorization.requester.user_id
    assert repo.bypass_resource_admission is False


def test_table_repository_platform_admin_uses_canonical_capability_not_legacy_bit() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        home_organization_id=organization_id,
        capabilities={"platform.superuser"},
        boundary=AuthorizationBoundary.platform(),
    )
    authorization.requester.is_superuser = False
    ctx = MagicMock()

    repo = _table_repository(ctx, authorization)

    assert repo.org_id is None
    assert repo.bypass_resource_admission is True


def test_table_attribution_override_uses_canonical_capability_not_legacy_bit() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        home_organization_id=organization_id,
        capabilities={"platform.superuser"},
        boundary=AuthorizationBoundary.platform(),
    )
    authorization.requester.is_superuser = False

    created_by, updated_by = _resolve_attribution(
        authorization.requester,
        str(uuid4()),
        None,
        authorization,
    )

    assert created_by == updated_by


def test_table_attribution_override_rejects_stale_legacy_bit_without_capability() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        home_organization_id=organization_id,
        capabilities={"tables.readwrite"},
    )
    authorization.requester.is_superuser = True

    with pytest.raises(HTTPException) as exc:
        _resolve_attribution(
            authorization.requester,
            str(uuid4()),
            None,
            authorization,
        )

    assert exc.value.status_code == 403


def test_platform_boundary_targets_global_tables() -> None:
    authorization = _authorization(
        home_organization_id=uuid4(),
        capabilities={"tables.readwrite"},
        boundary=AuthorizationBoundary.platform(),
    )

    assert _selected_table_organization_id(authorization) is None
    _require_table_mutation(authorization, _table(None))


def test_table_mutation_requires_readwrite_capability() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        home_organization_id=organization_id,
        capabilities={"tables.read"},
    )

    with pytest.raises(HTTPException) as exc:
        _require_table_mutation(authorization, _table(organization_id))

    assert exc.value.status_code == 403


def test_table_mutation_rejects_cross_boundary_resource() -> None:
    authorization = _authorization(
        home_organization_id=uuid4(),
        capabilities={"tables.readwrite"},
    )

    with pytest.raises(HTTPException) as exc:
        _require_table_mutation(authorization, _table(uuid4()))

    assert exc.value.status_code == 409
