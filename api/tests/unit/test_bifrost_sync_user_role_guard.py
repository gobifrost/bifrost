"""``bifrost._sync._apply_user_role_change`` refuses the system account.

This path applies buffered ``user_role`` changes queued during workflow
execution (see ``bifrost/_write_buffer.py``). It builds ``UserRole`` rows
directly, so it needs the same system-account guard as the REST role
assignment service (``shared.sdk_roles.assign_users_to_role``).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from bifrost._sync import SyncError, _apply_user_role_change
from src.core.constants import SYSTEM_USER_ID
from src.models import UserRole


async def test_apply_user_role_change_refuses_system_account(db_session):
    role_id = str(uuid4())
    change = {
        "entity_type": "user_role",
        "operation": "assign",
        "entity_id": role_id,
        "data": {"user_ids": [SYSTEM_USER_ID]},
        "user_id": "workflow@test.local",
    }

    with pytest.raises(SyncError, match="system account"):
        await _apply_user_role_change(db_session, change)

    result = await db_session.execute(
        select(UserRole).where(UserRole.role_id == role_id)
    )
    assert result.scalars().first() is None


async def test_apply_user_role_change_assigns_normal_user(db_session):
    from src.models.orm.organizations import Organization as OrganizationModel
    from src.models import User as UserORM
    from src.models import Role

    org = OrganizationModel(
        name=f"sync-user-role-org-{uuid4().hex[:8]}", is_active=True, created_by="test"
    )
    db_session.add(org)
    await db_session.flush()

    user = UserORM(
        email=f"sync-user-role-{uuid4().hex[:8]}@test.local",
        name="Sync Test User",
        organization_id=org.id,
    )
    db_session.add(user)

    role = Role(name=f"sync-user-role-{uuid4().hex[:8]}", created_by="test")
    db_session.add(role)
    await db_session.flush()

    role_id = str(role.id)
    change = {
        "entity_type": "user_role",
        "operation": "assign",
        "entity_id": role_id,
        "data": {"user_ids": [str(user.id)]},
        "user_id": "workflow@test.local",
    }

    await _apply_user_role_change(db_session, change)

    result = await db_session.execute(
        select(UserRole).where(UserRole.role_id == role_id)
    )
    row = result.scalars().first()
    assert row is not None
    assert row.user_id == user.id
