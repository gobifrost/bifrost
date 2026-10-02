"""Unit tests for `shared.sdk_users.set_user_base_role` — the single writer
that keeps `User.base_role_id` and `User.is_superuser` in lockstep."""

from __future__ import annotations

from uuid import uuid4

import pytest

from shared.builtin_roles import (
    DECRYPTION_ROLE_ID,
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_ROLE_ID,
    USER_ROLE_ID,
)
from shared.sdk_users import set_user_base_role


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


@pytest.mark.asyncio
async def test_platform_admin_role_id_sets_is_superuser_true(db_session):
    org = await _seed_org(db_session)
    user = await _seed_user(db_session, organization_id=org.id)

    await set_user_base_role(db_session, user, PLATFORM_ADMIN_ROLE_ID)

    assert user.base_role_id == PLATFORM_ADMIN_ROLE_ID
    assert user.is_superuser is True


@pytest.mark.asyncio
async def test_user_role_id_sets_is_superuser_false(db_session):
    org = await _seed_org(db_session)
    user = await _seed_user(db_session, organization_id=org.id)

    await set_user_base_role(db_session, user, USER_ROLE_ID)

    assert user.base_role_id == USER_ROLE_ID
    assert user.is_superuser is False


@pytest.mark.asyncio
async def test_toggling_back_and_forth_keeps_invariant(db_session):
    org = await _seed_org(db_session)
    user = await _seed_user(db_session, organization_id=org.id)

    await set_user_base_role(db_session, user, PLATFORM_ADMIN_ROLE_ID)
    assert user.is_superuser is True

    await set_user_base_role(db_session, user, USER_ROLE_ID)
    assert user.is_superuser is False

    await set_user_base_role(db_session, user, PLATFORM_ADMIN_ROLE_ID)
    assert user.is_superuser is True


@pytest.mark.asyncio
async def test_rejects_non_base_role_id(db_session):
    """Platform Operator and Secrets Reader are builtin but never base
    roles — a user's base_role_id is Platform Admin, User, or a custom role."""
    org = await _seed_org(db_session)
    user = await _seed_user(db_session, organization_id=org.id)

    with pytest.raises(ValueError):
        await set_user_base_role(db_session, user, PLATFORM_OPERATOR_ROLE_ID)
    with pytest.raises(ValueError):
        await set_user_base_role(db_session, user, DECRYPTION_ROLE_ID)


@pytest.mark.asyncio
async def test_rejects_arbitrary_role_id(db_session):
    org = await _seed_org(db_session)
    user = await _seed_user(db_session, organization_id=org.id)

    with pytest.raises(ValueError):
        await set_user_base_role(db_session, user, uuid4())
