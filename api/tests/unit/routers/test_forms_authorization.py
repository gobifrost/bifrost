"""Boundary/capability gates for Form administration routes."""

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.enums import FormAccessLevel
from src.models.orm.forms import Form
from src.routers.forms import (
    _check_form_access,
    _form_repository,
    _require_form_mutation,
    _require_form_read,
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


def _form(organization_id: UUID | None) -> Form:
    return Form(
        id=uuid4(),
        name="Customer intake",
        access_level=FormAccessLevel.AUTHENTICATED,
        organization_id=organization_id,
        is_active=True,
    )


def test_form_repository_is_bound_to_selected_organization() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        organization_id=organization_id,
        capabilities={"forms.read"},
    )

    repo = _form_repository(object(), authorization)  # type: ignore[arg-type]

    assert repo.org_id == organization_id
    assert repo.user_id == authorization.requester.user_id
    assert repo.bypass_resource_roles is False


def test_platform_superuser_form_repository_uses_global_with_bypass() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        organization_id=organization_id,
        capabilities={"platform.superuser"},
        boundary=AuthorizationBoundary.platform(),
    )

    repo = _form_repository(object(), authorization)  # type: ignore[arg-type]

    assert repo.org_id is None
    assert repo.user_id == authorization.requester.user_id
    assert repo.bypass_resource_roles is True


@pytest.mark.asyncio
async def test_form_access_uses_canonical_capability_not_legacy_bit(monkeypatch) -> None:
    organization_id = uuid4()
    form = _form(organization_id)
    authorization = _authorization(
        organization_id=organization_id,
        capabilities={"platform.superuser"},
        boundary=AuthorizationBoundary.platform(),
    )
    authorization.requester.is_superuser = False
    captured: dict[str, object] = {}

    class FakeFormRepository:
        def __init__(
            self,
            db,
            *,
            org_id,
            user_id,
            bypass_resource_roles,
            is_external,
        ) -> None:
            captured["org_id"] = org_id
            captured["user_id"] = user_id
            captured["bypass_resource_roles"] = bypass_resource_roles
            captured["is_external"] = is_external

        async def get(self, *, id):
            captured["id"] = id
            return form

    monkeypatch.setattr("src.routers.forms.FormRepository", FakeFormRepository)

    assert await _check_form_access(
        object(),  # type: ignore[arg-type]
        form,
        authorization.requester.user_id,
        None,
        authorization,
    )
    assert captured["bypass_resource_roles"] is True


@pytest.mark.asyncio
async def test_form_access_rejects_stale_legacy_bit_without_capability(monkeypatch) -> None:
    organization_id = uuid4()
    form = _form(organization_id)
    authorization = _authorization(
        organization_id=organization_id,
        capabilities={"forms.read"},
    )
    authorization.requester.is_superuser = True
    captured: dict[str, object] = {}

    class FakeFormRepository:
        def __init__(
            self,
            db,
            *,
            org_id,
            user_id,
            bypass_resource_roles,
            is_external,
        ) -> None:
            captured["bypass_resource_roles"] = bypass_resource_roles

        async def get(self, *, id):
            return None

    monkeypatch.setattr("src.routers.forms.FormRepository", FakeFormRepository)

    assert not await _check_form_access(
        object(),  # type: ignore[arg-type]
        form,
        authorization.requester.user_id,
        organization_id,
        authorization,
    )
    assert captured["bypass_resource_roles"] is False


def test_form_mutation_requires_readwrite_capability() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        organization_id=organization_id,
        capabilities={"forms.read"},
    )

    with pytest.raises(HTTPException) as exc:
        _require_form_mutation(authorization, _form(organization_id))

    assert exc.value.status_code == 403


def test_form_publication_read_requires_read_capability() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        organization_id=organization_id,
        capabilities=set(),
    )

    with pytest.raises(HTTPException) as exc:
        _require_form_read(authorization, _form(organization_id))

    assert exc.value.status_code == 403


def test_form_publication_read_rejects_cross_boundary_resource() -> None:
    authorization = _authorization(
        organization_id=uuid4(),
        capabilities={"forms.read"},
    )

    with pytest.raises(HTTPException) as exc:
        _require_form_read(authorization, _form(uuid4()))

    assert exc.value.status_code == 409


def test_form_mutation_rejects_cross_boundary_resource() -> None:
    authorization = _authorization(
        organization_id=uuid4(),
        capabilities={"forms.readwrite"},
    )

    with pytest.raises(HTTPException) as exc:
        _require_form_mutation(authorization, _form(uuid4()))

    assert exc.value.status_code == 409


def test_global_form_mutation_requires_platform_boundary() -> None:
    home_organization_id = uuid4()
    authorization = _authorization(
        organization_id=home_organization_id,
        capabilities={"forms.readwrite"},
        boundary=AuthorizationBoundary.platform(),
    )

    _require_form_mutation(authorization, _form(None))
