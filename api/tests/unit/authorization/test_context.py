"""``build_authorization_context`` against the real tables."""

from __future__ import annotations

from uuid import uuid4

import pytest

from shared.sdk_users import set_platform_admin
from shared.builtin_roles import (
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_PERMISSIONS,
    PLATFORM_OPERATOR_ROLE_ID,
    USER_BASE_PERMISSIONS,
    USER_ROLE_ID,
)
from src.core.constants import PROVIDER_ORG_ID
from src.models.orm.organizations import Organization
from src.models.orm.users import User, UserRole, UserRoleBoundary
from src.services.authorization.context import (
    Boundary,
    BoundaryKind,
    build_authorization_context,
)


async def _user(db_session, *, superuser: bool = False, external: bool = False, org_id=None) -> User:
    user = User(
        id=uuid4(),
        email=f"{uuid4().hex}@example.com",
        name="ctx",
        is_external=external,
        organization_id=org_id,
        is_active=True,
        is_registered=True,
    )
    db_session.add(user)
    await db_session.flush()
    await set_platform_admin(db_session, user, superuser, assigned_by="test")
    return user


@pytest.mark.asyncio
async def test_user_context_carries_base_permissions_and_no_grants(db_session) -> None:
    user = await _user(db_session, org_id=PROVIDER_ORG_ID)
    ctx = await build_authorization_context(db_session, user.id)
    assert ctx.base_role_id == USER_ROLE_ID
    assert ctx.home_organization_id == PROVIDER_ORG_ID
    assert ctx.base_permissions == USER_BASE_PERMISSIONS
    assert ctx.role_grants == ()
    assert not ctx.is_platform_admin


@pytest.mark.asyncio
async def test_platform_admin_context_is_recognised_by_the_assignment(db_session) -> None:
    user = await _user(db_session, superuser=True, external=True, org_id=PROVIDER_ORG_ID)
    ctx = await build_authorization_context(db_session, user.id)
    assert ctx.base_role_id == USER_ROLE_ID
    assert ctx.is_platform_admin
    assert [grant.role_id for grant in ctx.role_grants] == [PLATFORM_ADMIN_ROLE_ID]
    assert ctx.role_grants[0].boundaries == (Boundary(BoundaryKind.PLATFORM),)
    assert not ctx.is_external


@pytest.mark.asyncio
async def test_additional_role_is_loaded_with_permissions_and_boundaries(db_session) -> None:
    org = Organization(id=uuid4(), name=f"ctx-{uuid4().hex[:8]}", is_active=True, created_by="test")
    db_session.add(org)
    await db_session.flush()
    user = await _user(db_session, external=True, org_id=org.id)
    db_session.add(UserRole(user_id=user.id, role_id=PLATFORM_OPERATOR_ROLE_ID, assigned_by="test"))
    await db_session.flush()
    db_session.add(
        UserRoleBoundary(user_id=user.id, role_id=PLATFORM_OPERATOR_ROLE_ID, kind="managed_organizations")
    )
    db_session.add(
        UserRoleBoundary(user_id=user.id, role_id=PLATFORM_OPERATOR_ROLE_ID, kind="organization", organization_id=org.id)
    )
    await db_session.flush()

    ctx = await build_authorization_context(db_session, user.id)

    assert ctx.is_external
    (grant,) = ctx.role_grants
    assert grant.role_id == PLATFORM_OPERATOR_ROLE_ID
    assert grant.permissions == PLATFORM_OPERATOR_PERMISSIONS
    assert set(grant.boundaries) == {
        Boundary(BoundaryKind.MANAGED_ORGANIZATIONS),
        Boundary(BoundaryKind.ORGANIZATION, org.id),
    }
