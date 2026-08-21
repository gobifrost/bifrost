"""Default organization-member Role synchronization."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.core.constants import (
    ORGANIZATION_MEMBER_ROLE_ID,
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_ROLE_ID,
)
from src.models.orm.role_assignments import RoleAssignment, RoleAssignmentBoundary
from src.services.user_provisioning import (
    ensure_user_provisioned,
    ensure_platform_operator_role,
    sync_platform_admin_role,
    sync_organization_member_role,
)


def _db_with_assignment(assignment) -> MagicMock:
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = assignment
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_human_user_receives_organization_member_role():
    user_id = uuid4()
    organization_id = uuid4()
    db = _db_with_assignment(None)

    changed = await sync_organization_member_role(
        db,
        user_id=user_id,
        organization_id=organization_id,
        is_system=False,
    )

    assert changed is True
    assignment = db.add.call_args.args[0]
    assert assignment.role_id == ORGANIZATION_MEMBER_ROLE_ID
    assert assignment.boundaries[0].organization_id == organization_id


@pytest.mark.asyncio
async def test_organization_move_replaces_member_boundary():
    old_organization_id = uuid4()
    new_organization_id = uuid4()
    assignment = RoleAssignment(
        id=uuid4(),
        user_id=uuid4(),
        role_id=ORGANIZATION_MEMBER_ROLE_ID,
        boundaries=[
            RoleAssignmentBoundary(
                boundary_kind="organization",
                organization_id=old_organization_id,
            )
        ],
    )
    db = _db_with_assignment(assignment)

    changed = await sync_organization_member_role(
        db,
        user_id=assignment.user_id,
        organization_id=new_organization_id,
        is_system=False,
    )

    assert changed is True
    assert len(assignment.boundaries) == 1
    assert assignment.boundaries[0].organization_id == new_organization_id


@pytest.mark.asyncio
async def test_provider_member_receives_sticky_platform_operator_role():
    db = _db_with_assignment(None)
    db.scalar = AsyncMock(return_value=True)

    changed = await ensure_platform_operator_role(
        db,
        user_id=uuid4(),
        organization_id=uuid4(),
        is_superuser=False,
        is_system=False,
    )

    assert changed is True
    assignment = db.add.call_args.args[0]
    assert assignment.role_id == PLATFORM_OPERATOR_ROLE_ID
    assert len(assignment.boundaries) == 1
    assert assignment.boundaries[0].boundary_kind == "managed_organizations"


@pytest.mark.asyncio
async def test_legacy_superuser_receives_platform_admin_role():
    db = _db_with_assignment(None)

    changed = await sync_platform_admin_role(
        db,
        user_id=uuid4(),
        enabled=True,
    )

    assert changed is True
    assignment = db.add.call_args.args[0]
    assert assignment.role_id == PLATFORM_ADMIN_ROLE_ID
    assert len(assignment.boundaries) == 1
    assert assignment.boundaries[0].boundary_kind == "platform"


@pytest.mark.asyncio
async def test_customer_member_does_not_receive_platform_operator_role():
    db = _db_with_assignment(None)
    db.scalar = AsyncMock(return_value=False)

    changed = await ensure_platform_operator_role(
        db,
        user_id=uuid4(),
        organization_id=uuid4(),
        is_superuser=False,
        is_system=False,
    )

    assert changed is False
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_existing_platform_operator_assignment_is_not_removed_after_move():
    assignment = RoleAssignment(
        id=uuid4(),
        user_id=uuid4(),
        role_id=PLATFORM_OPERATOR_ROLE_ID,
        boundaries=[RoleAssignmentBoundary(boundary_kind="managed_organizations")],
    )
    db = _db_with_assignment(assignment)
    db.scalar = AsyncMock(side_effect=AssertionError("provider lookup not expected"))

    changed = await ensure_platform_operator_role(
        db,
        user_id=assignment.user_id,
        organization_id=uuid4(),
        is_superuser=False,
        is_system=False,
    )

    assert changed is False
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_existing_user_provisioning_syncs_legacy_superuser_role():
    user = SimpleNamespace(
        id=uuid4(),
        email="admin@example.com",
        organization_id=uuid4(),
        is_superuser=True,
        is_system=False,
    )
    db = MagicMock()
    db.commit = AsyncMock()

    with (
        patch("src.services.user_provisioning.UserRepository") as user_repo_cls,
        patch("src.services.user_provisioning.OrganizationRepository"),
        patch(
            "src.services.user_provisioning.sync_platform_admin_role",
            AsyncMock(return_value=True),
        ) as sync_admin,
        patch(
            "src.services.user_provisioning.sync_organization_member_role",
            AsyncMock(return_value=False),
        ) as sync_member,
        patch(
            "src.services.user_provisioning.ensure_platform_operator_role",
            AsyncMock(return_value=False),
        ) as sync_operator,
    ):
        user_repo_cls.return_value.get_by_email = AsyncMock(return_value=user)
        result = await ensure_user_provisioned(db, "ADMIN@example.com")

    assert result.user is user
    assert result.is_platform_admin is True
    sync_admin.assert_awaited_once_with(
        db,
        user_id=user.id,
        enabled=True,
    )
    sync_member.assert_awaited_once()
    sync_operator.assert_awaited_once()
    db.commit.assert_awaited_once()
