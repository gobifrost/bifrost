"""Unit tests for `src.services.role_permissions`.

Covers vocabulary validation, get/set round-trip, builtin-role immutability,
and the Platform Admin wildcard.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID, USER_ROLE_ID, WILDCARD_PERMISSION
from src.services.role_permissions import (
    RolePermissionError,
    get_role_permissions,
    role_has_permission,
    set_role_permissions,
    validate_permission,
)


def test_validate_permission_accepts_known_domain_and_action():
    validate_permission("agents.read")
    validate_permission("workflows.readwrite")
    validate_permission("workflows.execute")


def test_validate_permission_rejects_unknown_domain():
    with pytest.raises(RolePermissionError) as exc_info:
        validate_permission("not_a_domain.read")
    assert exc_info.value.status_code == 422


def test_validate_permission_rejects_bad_action():
    with pytest.raises(RolePermissionError) as exc_info:
        validate_permission("agents.delete")
    assert exc_info.value.status_code == 422


def test_validate_permission_rejects_missing_dot():
    with pytest.raises(RolePermissionError):
        validate_permission("agentsread")


@pytest.mark.asyncio
async def test_platform_admin_returns_wildcard(db_session):
    perms = await get_role_permissions(db_session, role_id=PLATFORM_ADMIN_ROLE_ID)
    assert perms == frozenset({WILDCARD_PERMISSION})


@pytest.mark.asyncio
async def test_platform_admin_cannot_be_modified(db_session):
    with pytest.raises(RolePermissionError) as exc_info:
        await set_role_permissions(
            db_session, role_id=PLATFORM_ADMIN_ROLE_ID, permissions=frozenset({"agents.read"})
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_user_base_role_cannot_be_modified(db_session):
    with pytest.raises(RolePermissionError) as exc_info:
        await set_role_permissions(
            db_session, role_id=USER_ROLE_ID, permissions=frozenset({"agents.read"})
        )
    assert exc_info.value.status_code == 409


async def _seed_role(db_session):
    from src.models.orm.users import Role

    role = Role(name=f"custom-{uuid4().hex[:8]}", created_by="test")
    db_session.add(role)
    await db_session.flush()
    return role


@pytest.mark.asyncio
async def test_set_and_get_round_trip_for_custom_role(db_session):
    role = await _seed_role(db_session)
    await set_role_permissions(
        db_session, role_id=role.id, permissions=frozenset({"agents.read", "forms.readwrite"})
    )
    perms = await get_role_permissions(db_session, role_id=role.id)
    assert perms == frozenset({"agents.read", "forms.readwrite"})


@pytest.mark.asyncio
async def test_set_replaces_wholesale(db_session):
    role = await _seed_role(db_session)
    await set_role_permissions(db_session, role_id=role.id, permissions=frozenset({"agents.read"}))
    await set_role_permissions(db_session, role_id=role.id, permissions=frozenset({"forms.read"}))
    perms = await get_role_permissions(db_session, role_id=role.id)
    assert perms == frozenset({"forms.read"})


@pytest.mark.asyncio
async def test_set_validates_every_permission(db_session):
    role = await _seed_role(db_session)
    with pytest.raises(RolePermissionError):
        await set_role_permissions(
            db_session, role_id=role.id, permissions=frozenset({"bogus.read"})
        )


@pytest.mark.asyncio
async def test_role_has_permission_true_for_platform_admin_regardless_of_rows(db_session):
    assert await role_has_permission(
        db_session, role_ids=[PLATFORM_ADMIN_ROLE_ID], permission="anything.readwrite"
    )


@pytest.mark.asyncio
async def test_role_has_permission_checks_across_multiple_roles(db_session):
    role = await _seed_role(db_session)
    other = await _seed_role(db_session)
    await set_role_permissions(db_session, role_id=role.id, permissions=frozenset({"agents.readwrite"}))

    assert await role_has_permission(
        db_session, role_ids=[other.id, role.id], permission="agents.readwrite"
    )
    assert not await role_has_permission(
        db_session, role_ids=[other.id], permission="agents.readwrite"
    )


@pytest.mark.asyncio
async def test_role_has_permission_false_for_empty_role_list(db_session):
    assert not await role_has_permission(db_session, role_ids=[], permission="agents.read")
