"""Unit tests for the boundary-aware role assignment service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.models.orm.role_assignments import RoleAssignmentBoundary
from src.models.orm.users import Role, User
from src.services.authorization import AuthorizationBoundaryKind
from src.services.role_assignments import (
    RoleAssignmentBoundarySpec,
    delete_role_assignment,
    replace_role_assignment,
)


def _principal() -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="admin@example.com",
        organization_id=uuid4(),
        is_active=True,
        is_superuser=False,
    )


def _role() -> Role:
    return Role(
        id=uuid4(),
        name="Platform Builder",
        created_by="system@example.com",
    )


def _user(org_id):
    return User(
        id=uuid4(),
        email="user@example.com",
        organization_id=org_id,
    )


def _db_mock(*, role=None, user=None, org_ids=(), group_ids=(), assignment=None):
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[role, user])
    db.scalar = AsyncMock(return_value=assignment)
    db.flush = AsyncMock()
    db.add = MagicMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = list(org_ids)
    group_result = MagicMock()
    group_result.scalars.return_value.all.return_value = list(group_ids)
    db.execute = AsyncMock(side_effect=[execute_result, group_result])
    return db


@pytest.mark.asyncio
async def test_replace_role_assignment_creates_exact_organization_boundary(monkeypatch):
    requester = _principal()
    org_id = uuid4()
    role = _role()
    user = _user(org_id)
    db = _db_mock(role=role, user=user, org_ids=[org_id])
    authorize = AsyncMock()
    invalidate = AsyncMock()

    monkeypatch.setattr("src.services.role_assignments.authorize_capability", authorize)
    monkeypatch.setattr("src.services.role_assignments.invalidate_user", invalidate)

    assignment = await replace_role_assignment(
        db,
        requester=requester,
        user_id=user.id,
        role_id=role.id,
        boundaries=[RoleAssignmentBoundarySpec(kind="organization", organization_id=org_id)],
    )

    assert assignment.user_id == user.id
    assert assignment.role_id == role.id
    assert [boundary.boundary_kind for boundary in assignment.boundaries] == [
        "organization"
    ]
    assert assignment.boundaries[0].organization_id == org_id
    assert assignment.boundaries[0].organization_group_id is None
    assert authorize.await_count == 1
    assert authorize.await_args.kwargs["selected_boundary"].kind is AuthorizationBoundaryKind.ORGANIZATION
    assert db.add.call_count == 1
    assert db.flush.await_count == 1
    assert invalidate.await_args.args == (user.id,)


@pytest.mark.asyncio
async def test_replace_role_assignment_authorizes_each_group_member_organization(monkeypatch):
    requester = _principal()
    group_id = uuid4()
    member_org_id = uuid4()
    role = _role()
    user = _user(uuid4())
    db = _db_mock(role=role, user=user, org_ids=[])
    group_exists_result = MagicMock()
    group_exists_result.scalars.return_value.all.return_value = [group_id]
    group_members_result = MagicMock()
    group_members_result.scalars.return_value.all.return_value = [member_org_id]
    db.execute = AsyncMock(side_effect=[group_exists_result, group_members_result])
    authorize = AsyncMock()
    invalidate = AsyncMock()

    monkeypatch.setattr("src.services.role_assignments.authorize_capability", authorize)
    monkeypatch.setattr("src.services.role_assignments.invalidate_user", invalidate)

    await replace_role_assignment(
        db,
        requester=requester,
        user_id=user.id,
        role_id=role.id,
        boundaries=[
            RoleAssignmentBoundarySpec(
                kind="organization_group",
                organization_group_id=group_id,
            )
        ],
    )

    assert authorize.await_count == 1
    selected = authorize.await_args.kwargs["selected_boundary"]
    assert selected.kind is AuthorizationBoundaryKind.ORGANIZATION
    assert selected.organization_id == member_org_id
    assert db.add.call_count == 1
    persisted = db.add.call_args.args[0]
    assert isinstance(persisted.boundaries[0], RoleAssignmentBoundary)
    assert persisted.boundaries[0].boundary_kind == "organization_group"
    assert persisted.boundaries[0].organization_group_id == group_id


@pytest.mark.asyncio
async def test_replace_role_assignment_rejects_duplicate_exact_boundaries(monkeypatch):
    requester = _principal()
    org_id = uuid4()
    role = _role()
    user = _user(org_id)
    db = _db_mock(role=role, user=user, org_ids=[org_id])
    monkeypatch.setattr(
        "src.services.role_assignments.authorize_capability", AsyncMock()
    )
    monkeypatch.setattr("src.services.role_assignments.invalidate_user", AsyncMock())

    from fastapi import HTTPException

    with pytest.raises(HTTPException, match="Duplicate boundary selections are not allowed"):
        await replace_role_assignment(
            db,
            requester=requester,
            user_id=user.id,
            role_id=role.id,
            boundaries=[
                RoleAssignmentBoundarySpec(kind="organization", organization_id=org_id),
                RoleAssignmentBoundarySpec(kind="organization", organization_id=org_id),
            ],
        )


@pytest.mark.asyncio
async def test_replace_role_assignment_authorizes_removed_boundaries(monkeypatch):
    requester = _principal()
    retained_org_id = uuid4()
    removed_org_id = uuid4()
    role = _role()
    user = _user(retained_org_id)
    assignment = MagicMock()
    assignment.boundaries = [
        RoleAssignmentBoundary(
            boundary_kind="organization",
            organization_id=retained_org_id,
        ),
        RoleAssignmentBoundary(
            boundary_kind="organization",
            organization_id=removed_org_id,
        ),
    ]
    db = _db_mock(
        role=role,
        user=user,
        org_ids=[retained_org_id],
        assignment=assignment,
    )
    authorize = AsyncMock()
    monkeypatch.setattr("src.services.role_assignments.authorize_capability", authorize)
    monkeypatch.setattr("src.services.role_assignments.invalidate_user", AsyncMock())

    await replace_role_assignment(
        db,
        requester=requester,
        user_id=user.id,
        role_id=role.id,
        boundaries=[
            RoleAssignmentBoundarySpec(
                kind="organization",
                organization_id=retained_org_id,
            )
        ],
    )

    authorized_orgs = {
        call.kwargs["selected_boundary"].organization_id
        for call in authorize.await_args_list
    }
    assert authorized_orgs == {retained_org_id, removed_org_id}


@pytest.mark.asyncio
async def test_delete_role_assignment_authorizes_every_existing_boundary(monkeypatch):
    requester = _principal()
    first_org_id = uuid4()
    second_org_id = uuid4()
    assignment = MagicMock()
    assignment.id = uuid4()
    assignment.boundaries = [
        RoleAssignmentBoundary(
            boundary_kind="organization",
            organization_id=first_org_id,
        ),
        RoleAssignmentBoundary(
            boundary_kind="organization",
            organization_id=second_org_id,
        ),
    ]
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=assignment)
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    authorize = AsyncMock()
    invalidate = AsyncMock()
    monkeypatch.setattr("src.services.role_assignments.authorize_capability", authorize)
    monkeypatch.setattr("src.services.role_assignments.invalidate_user", invalidate)

    removed = await delete_role_assignment(
        db,
        requester=requester,
        user_id=uuid4(),
        role_id=uuid4(),
    )

    assert removed is True
    authorized_orgs = {
        call.kwargs["selected_boundary"].organization_id
        for call in authorize.await_args_list
    }
    assert authorized_orgs == {first_org_id, second_org_id}
    assert db.execute.await_count == 1
    assert db.flush.await_count == 1
    assert invalidate.await_count == 1


@pytest.mark.asyncio
async def test_delete_role_assignment_returns_false_when_missing(monkeypatch):
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    authorize = AsyncMock()
    invalidate = AsyncMock()
    monkeypatch.setattr("src.services.role_assignments.authorize_capability", authorize)
    monkeypatch.setattr("src.services.role_assignments.invalidate_user", invalidate)

    removed = await delete_role_assignment(
        db,
        requester=_principal(),
        user_id=uuid4(),
        role_id=uuid4(),
    )

    assert removed is False
    authorize.assert_not_awaited()
    invalidate.assert_not_awaited()
