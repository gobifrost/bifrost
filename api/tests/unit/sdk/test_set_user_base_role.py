"""Unit tests for the two single writers in `shared.sdk_users`:
`set_user_base_role` (User or a custom role, never a builtin beyond User) and
`set_platform_admin` (the Platform Admin assignment, with `is_superuser` kept
in lockstep)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from shared.builtin_roles import (
    DECRYPTION_ROLE_ID,
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_ROLE_ID,
    USER_ROLE_ID,
)
from shared.sdk_users import set_platform_admin, set_user_base_role


async def _seed_org(db_session):
    from src.models.orm.organizations import Organization

    org = Organization(name=f"org-{uuid4().hex[:8]}", is_active=True, created_by="test")
    db_session.add(org)
    await db_session.flush()
    return org


async def _seed_user(db_session, *, organization_id):
    from src.models import User as UserORM

    user = UserORM(email=f"u-{uuid4().hex[:8]}@test.local", organization_id=organization_id)
    db_session.add(user)
    await db_session.flush()
    return user


async def _admin_boundaries(db_session, user_id):
    from src.models import UserRoleBoundary

    rows = await db_session.execute(
        select(UserRoleBoundary.kind, UserRoleBoundary.organization_id).where(
            UserRoleBoundary.user_id == user_id,
            UserRoleBoundary.role_id == PLATFORM_ADMIN_ROLE_ID,
        )
    )
    return {tuple(row) for row in rows}


@pytest.mark.asyncio
async def test_user_role_id_is_a_valid_base(db_session):
    org = await _seed_org(db_session)
    user = await _seed_user(db_session, organization_id=org.id)

    await set_user_base_role(db_session, user, USER_ROLE_ID)

    assert user.base_role_id == USER_ROLE_ID
    assert user.is_superuser is False


@pytest.mark.asyncio
async def test_rejects_non_base_role_id(db_session):
    """Platform Admin, Platform Operator and Secrets Reader are builtin but
    never base roles — a user's base_role_id is User or a custom role."""
    org = await _seed_org(db_session)
    user = await _seed_user(db_session, organization_id=org.id)

    for role_id in (PLATFORM_ADMIN_ROLE_ID, PLATFORM_OPERATOR_ROLE_ID, DECRYPTION_ROLE_ID):
        with pytest.raises(ValueError):
            await set_user_base_role(db_session, user, role_id)
    assert user.base_role_id == USER_ROLE_ID


@pytest.mark.asyncio
async def test_rejects_arbitrary_role_id(db_session):
    org = await _seed_org(db_session)
    user = await _seed_user(db_session, organization_id=org.id)

    with pytest.raises(ValueError):
        await set_user_base_role(db_session, user, uuid4())


@pytest.mark.asyncio
async def test_set_platform_admin_adds_the_assignment_at_the_platform_boundary(db_session):
    org = await _seed_org(db_session)
    user = await _seed_user(db_session, organization_id=org.id)

    await set_platform_admin(db_session, user, True, assigned_by="test")

    assert user.is_superuser is True
    assert user.base_role_id == USER_ROLE_ID
    assert await _admin_boundaries(db_session, user.id) == {("platform", None)}


@pytest.mark.asyncio
async def test_set_platform_admin_removes_the_assignment_and_its_boundary(db_session):
    org = await _seed_org(db_session)
    user = await _seed_user(db_session, organization_id=org.id)
    await set_platform_admin(db_session, user, True, assigned_by="test")

    await set_platform_admin(db_session, user, False, assigned_by="test")

    assert user.is_superuser is False
    assert await _admin_boundaries(db_session, user.id) == set()


@pytest.mark.asyncio
async def test_set_platform_admin_is_idempotent(db_session):
    org = await _seed_org(db_session)
    user = await _seed_user(db_session, organization_id=org.id)

    await set_platform_admin(db_session, user, True, assigned_by="test")
    await set_platform_admin(db_session, user, True, assigned_by="test")
    assert await _admin_boundaries(db_session, user.id) == {("platform", None)}

    await set_platform_admin(db_session, user, False, assigned_by="test")
    await set_platform_admin(db_session, user, False, assigned_by="test")
    assert user.is_superuser is False
