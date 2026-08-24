"""Boundary-isolation tests for the central human authorization evaluator."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models import Role, RoleAssignment, RoleAssignmentBoundary
from src.services.authorization import (
    AuthorizationBoundary,
    AuthorizationBoundaryKind,
    AuthorizationContext,
    AuthorizationGrantSource,
    authorize_capability,
    authorize_operation,
    parse_authorization_boundary,
    policy_principal_for_authorization,
    resolve_authorization_context,
)


def _principal() -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="builder@example.com",
        organization_id=uuid4(),
    )


def _row(
    principal: UserPrincipal,
    *,
    capabilities: list[str],
    boundary_kind: str,
    organization_id=None,
    organization_group_id=None,
):
    role = Role(
        id=uuid4(),
        name="Test role",
        capabilities=capabilities,
        created_by="test",
    )
    assignment = RoleAssignment(
        id=uuid4(),
        user_id=principal.user_id,
        role_id=role.id,
    )
    boundary = RoleAssignmentBoundary(
        id=uuid4(),
        role_assignment_id=assignment.id,
        boundary_kind=boundary_kind,
        organization_id=organization_id,
        organization_group_id=organization_group_id,
    )
    return assignment, role, boundary


def _result(rows):
    result = MagicMock()
    result.all.return_value = rows
    return result


def _scalar_result(values):
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


def test_boundary_header_defaults_to_home_organization_and_requires_global_explicitly():
    home_organization_id = uuid4()

    home = parse_authorization_boundary(
        None,
        home_organization_id=home_organization_id,
    )
    platform = parse_authorization_boundary(
        "platform",
        home_organization_id=home_organization_id,
    )

    assert home == AuthorizationBoundary.organization(home_organization_id)
    assert platform.kind is AuthorizationBoundaryKind.PLATFORM


def test_boundary_header_rejects_invalid_context():
    with pytest.raises(HTTPException) as exc:
        parse_authorization_boundary("all", home_organization_id=uuid4())

    assert exc.value.status_code == 400


def test_concrete_resource_requires_exact_selected_boundary():
    principal = _principal()
    organization_id = uuid4()
    context = AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=AuthorizationBoundary.organization(organization_id),
        effective_capabilities=frozenset({"agents.read"}),
        grant_sources=(),
    )

    context.require_resource_boundary(organization_id)

    with pytest.raises(HTTPException) as exc:
        context.require_resource_boundary(uuid4())
    assert exc.value.status_code == 409


def test_managed_selector_is_not_a_concrete_resource_identity():
    principal = _principal()
    context = AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=AuthorizationBoundary.managed_organizations(),
        effective_capabilities=frozenset({"agents.readwrite"}),
        grant_sources=(),
    )

    with pytest.raises(HTTPException) as exc:
        context.require_resource_boundary(uuid4())
    assert exc.value.status_code == 409


def test_platform_admin_wildcard_satisfies_resource_boundary() -> None:
    principal = _principal()
    context = AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=AuthorizationBoundary.organization(
            principal.organization_id
        ),
        effective_capabilities=frozenset({"platform.superuser"}),
        grant_sources=(),
    )

    context.require_resource_boundary(uuid4())
    context.require_resource_boundary(None)


@pytest.mark.asyncio
async def test_platform_and_organization_capabilities_are_not_unioned():
    principal = _principal()
    organization_id = uuid4()
    rows = [
        _row(
            principal,
            capabilities=["agents.readwrite"],
            boundary_kind="organization",
            organization_id=organization_id,
        ),
        _row(
            principal,
            capabilities=["repository.readwrite"],
            boundary_kind="platform",
        ),
    ]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(rows), _scalar_result([])])
    db.scalar = AsyncMock(return_value=False)

    context = await resolve_authorization_context(
        db,
        requester=principal,
        selected_boundary=AuthorizationBoundary.organization(organization_id),
    )

    assert context.has_capability("agents.readwrite")
    assert context.has_capability("agents.read")
    assert not context.has_capability("repository.readwrite")


@pytest.mark.asyncio
async def test_group_assignment_covers_only_current_member_organization():
    principal = _principal()
    organization_id = uuid4()
    group_id = uuid4()
    rows = [
        _row(
            principal,
            capabilities=["builder.execute"],
            boundary_kind="organization_group",
            organization_group_id=group_id,
        )
    ]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(rows), _scalar_result([group_id])])
    db.scalar = AsyncMock(return_value=False)

    context = await resolve_authorization_context(
        db,
        requester=principal,
        selected_boundary=AuthorizationBoundary.organization(organization_id),
    )

    assert context.has_capability("builder.execute")
    assert context.grant_sources[0].covering_boundary_id == group_id


@pytest.mark.asyncio
async def test_empty_group_assignment_grants_no_customer_authority():
    principal = _principal()
    organization_id = uuid4()
    group_id = uuid4()
    rows = [
        _row(
            principal,
            capabilities=["builder.execute"],
            boundary_kind="organization_group",
            organization_group_id=group_id,
        )
    ]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(rows), _scalar_result([])])
    db.scalar = AsyncMock(return_value=False)

    context = await resolve_authorization_context(
        db,
        requester=principal,
        selected_boundary=AuthorizationBoundary.organization(organization_id),
    )

    assert not context.has_capability("builder.execute")
    assert context.grant_sources == ()


@pytest.mark.asyncio
async def test_managed_selector_never_covers_provider_organization():
    principal = _principal()
    provider_organization_id = uuid4()
    rows = [
        _row(
            principal,
            capabilities=["organizations.readwrite"],
            boundary_kind="managed_organizations",
        )
    ]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(rows), _scalar_result([])])
    db.scalar = AsyncMock(return_value=True)

    context = await resolve_authorization_context(
        db,
        requester=principal,
        selected_boundary=AuthorizationBoundary.organization(
            provider_organization_id
        ),
    )

    assert not context.has_capability("organizations.readwrite")


@pytest.mark.asyncio
async def test_platform_admin_wildcard_crosses_boundary_from_platform_assignment():
    principal = _principal()
    organization_id = uuid4()
    rows = [
        _row(
            principal,
            capabilities=["platform.superuser"],
            boundary_kind="platform",
        )
    ]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(rows), _scalar_result([])])
    db.scalar = AsyncMock(return_value=False)

    context = await authorize_capability(
        db,
        requester=principal,
        selected_boundary=AuthorizationBoundary.organization(organization_id),
        capability="solutions.publish.execute",
    )

    assert context.role_assignment_ids == (rows[0][0].id,)


@pytest.mark.asyncio
async def test_missing_capability_is_forbidden():
    principal = _principal()
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result([]))

    with pytest.raises(HTTPException) as exc:
        await authorize_capability(
            db,
            requester=principal,
            selected_boundary=AuthorizationBoundary.platform(),
            capability="repository.readwrite",
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_operation_authorization_uses_catalogued_capability():
    principal = _principal()
    organization_id = uuid4()
    rows = [
        _row(
            principal,
            capabilities=["agents.read"],
            boundary_kind="organization",
            organization_id=organization_id,
        )
    ]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(rows), _scalar_result([])])
    db.scalar = AsyncMock(return_value=False)

    context = await authorize_operation(
        db,
        requester=principal,
        selected_boundary=AuthorizationBoundary.organization(organization_id),
        operation_id="agents.get",
    )

    assert context.has_capability("agents.read")


def test_policy_principal_uses_only_selected_boundary_roles() -> None:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="user@example.com",
        organization_id=uuid4(),
        role_ids=[uuid4()],
        role_names=["Other Org Role"],
    )
    selected_role_id = uuid4()
    context = AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=AuthorizationBoundary.organization(principal.organization_id),
        effective_capabilities=frozenset({"tabledocuments.read"}),
        grant_sources=(
            AuthorizationGrantSource(
                assignment_id=uuid4(),
                role_id=selected_role_id,
                role_name="Selected Org Role",
                capabilities=frozenset({"tabledocuments.read"}),
                covering_boundary_kind="organization",
                covering_boundary_id=principal.organization_id,
            ),
        ),
    )

    policy_user = policy_principal_for_authorization(principal, context)

    assert policy_user is not principal
    assert policy_user.role_ids == [selected_role_id]
    assert policy_user.role_names == ["Selected Org Role"]
    assert principal.role_names == ["Other Org Role"]


def test_policy_principal_preserves_runtime_principal_without_authorization() -> None:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="runtime@example.com",
        organization_id=uuid4(),
        role_names=["Runtime Role"],
    )

    assert policy_principal_for_authorization(principal, None) is principal
