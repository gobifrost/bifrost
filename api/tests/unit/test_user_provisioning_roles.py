"""Compatibility role synchronization for provider staff."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.constants import PLATFORM_OPERATOR_ROLE_ID, PROVIDER_ORG_ID
from src.models import UserRole
from src.services.user_provisioning import sync_platform_operator_role


def _db_with_assignment(assignment: UserRole | None) -> MagicMock:
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = assignment
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_provider_member_receives_platform_operator_shadow_role():
    user_id = uuid4()
    db = _db_with_assignment(None)

    changed = await sync_platform_operator_role(
        db,
        user_id=user_id,
        organization_id=PROVIDER_ORG_ID,
        is_platform_admin=False,
        assigned_by="admin@example.com",
    )

    assert changed is True
    assignment = db.add.call_args.args[0]
    assert assignment.user_id == user_id
    assert assignment.role_id == PLATFORM_OPERATOR_ROLE_ID
    assert assignment.assigned_by == "admin@example.com"


@pytest.mark.asyncio
async def test_platform_admin_does_not_keep_redundant_operator_role():
    assignment = UserRole(
        user_id=uuid4(),
        role_id=PLATFORM_OPERATOR_ROLE_ID,
        assigned_by="legacy@example.com",
    )
    db = _db_with_assignment(assignment)

    changed = await sync_platform_operator_role(
        db,
        user_id=assignment.user_id,
        organization_id=PROVIDER_ORG_ID,
        is_platform_admin=True,
        assigned_by="admin@example.com",
    )

    assert changed is True
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_customer_member_does_not_receive_operator_role():
    db = _db_with_assignment(None)

    changed = await sync_platform_operator_role(
        db,
        user_id=uuid4(),
        organization_id=uuid4(),
        is_platform_admin=False,
        assigned_by="admin@example.com",
    )

    assert changed is False
    db.add.assert_not_called()
