"""Base-role defaults resolve safely and provision only new accounts."""

from __future__ import annotations

from uuid import uuid4

import pytest

from shared.base_role_defaults import (
    BaseRoleDefaultError,
    default_base_role_reference_count,
    get_default_base_role,
    refuse_referenced_default_base_role_deletion,
    resolve_base_role,
    set_default_base_role,
)
from shared.builtin_roles import USER_ROLE_ID
from tests.helpers.authorization import admin_caller


pytestmark = pytest.mark.asyncio


async def _organization(db_session):
    from src.models import Organization

    organization = Organization(
        name=f"base-role-defaults-{uuid4().hex[:8]}",
        is_active=True,
        created_by="test",
    )
    db_session.add(organization)
    await db_session.flush()
    return organization


async def _role(db_session, *, privileged: bool = False):
    from src.models import Role, RolePermission

    role = Role(name=f"Provisioned {uuid4().hex[:8]}", created_by="test")
    db_session.add(role)
    await db_session.flush()
    if privileged:
        db_session.add(RolePermission(role_id=role.id, permission="configs.readwrite"))
        await db_session.flush()
    return role


async def test_global_falls_back_to_user_and_org_inherits_it(db_session):
    organization = await _organization(db_session)

    global_configured, global_effective = await get_default_base_role(
        db_session, organization_id=None
    )
    org_configured, org_effective = await get_default_base_role(
        db_session, organization_id=organization.id
    )

    assert global_configured == USER_ROLE_ID
    assert (global_effective.id, global_effective.source) == (USER_ROLE_ID, "global")
    assert org_configured is None
    assert (org_effective.id, org_effective.source) == (USER_ROLE_ID, "global")


async def test_org_override_wins_and_null_restores_global_inheritance(db_session):
    organization = await _organization(db_session)
    global_role = await _role(db_session)
    organization_role = await _role(db_session)

    await set_default_base_role(
        db_session,
        organization_id=None,
        default_base_role_id=global_role.id,
        updated_by="test",
    )
    configured, effective = await set_default_base_role(
        db_session,
        organization_id=organization.id,
        default_base_role_id=organization_role.id,
        updated_by="test",
    )
    assert configured == organization_role.id
    assert (effective.id, effective.source) == (organization_role.id, "organization")

    configured, effective = await set_default_base_role(
        db_session,
        organization_id=organization.id,
        default_base_role_id=None,
        updated_by="test",
    )
    assert configured is None
    assert (effective.id, effective.source) == (global_role.id, "global")


async def test_privileged_role_cannot_be_configured_as_a_default(db_session):
    privileged_role = await _role(db_session, privileged=True)

    with pytest.raises(BaseRoleDefaultError) as refused:
        await set_default_base_role(
            db_session,
            organization_id=None,
            default_base_role_id=privileged_role.id,
            updated_by="test",
        )

    assert refused.value.status_code == 422
    assert "cannot grant privileged" in refused.value.detail


async def test_setting_org_default_preserves_latest_other_org_settings(db_session):
    from sqlalchemy import update
    from src.models import Organization

    organization = await _organization(db_session)
    role = await _role(db_session)
    # Model an organization cached before another settings writer committed.
    await db_session.execute(
        update(Organization)
        .where(Organization.id == organization.id)
        .values(settings={"another_setting": "latest"})
        .execution_options(synchronize_session=False)
    )

    await set_default_base_role(
        db_session, organization_id=organization.id,
        default_base_role_id=role.id, updated_by="test",
    )

    assert organization.settings == {
        "another_setting": "latest", "default_base_role_id": str(role.id),
    }


async def test_referenced_role_is_refused_by_the_deletion_guard(db_session):
    role = await _role(db_session)
    await set_default_base_role(
        db_session,
        organization_id=None,
        default_base_role_id=role.id,
        updated_by="test",
    )

    assert await default_base_role_reference_count(db_session, role_id=role.id) == 1
    with pytest.raises(BaseRoleDefaultError) as refused:
        await refuse_referenced_default_base_role_deletion(db_session, role_id=role.id)

    assert refused.value.status_code == 409


async def test_referenced_default_role_cannot_be_made_privileged(db_session):
    from src.services.role_permissions import RolePermissionError, set_role_permissions

    role = await _role(db_session)
    await set_default_base_role(
        db_session,
        organization_id=None,
        default_base_role_id=role.id,
        updated_by="test",
    )

    with pytest.raises(RolePermissionError) as refused:
        await set_role_permissions(
            db_session,
            role_id=role.id,
            permissions=frozenset({"configs.readwrite"}),
        )

    assert refused.value.status_code == 409


async def test_new_users_and_custom_identities_use_the_resolved_default(db_session):
    from shared.sdk_users import create_user
    from src.models.contracts.identities import IdentityCreate
    from src.services.identities import create_identity

    organization = await _organization(db_session)
    role = await _role(db_session)
    await set_default_base_role(
        db_session,
        organization_id=organization.id,
        default_base_role_id=role.id,
        updated_by="test",
    )

    created = await create_user(
        db_session,
        admin_caller(),
        email=f"person-{uuid4().hex[:8]}@example.com",
        name="Person",
        organization_id=organization.id,
    )
    identity = await create_identity(
        db_session,
        admin_caller(),
        IdentityCreate(name="Provisioner", organization_id=organization.id),
    )

    from src.models import User

    created_user = await db_session.get(User, created.id)
    assert created_user is not None
    assert created_user.base_role_id == role.id
    assert identity.base_role.id == role.id


async def test_explicit_base_role_has_precedence_over_configured_default(db_session):
    organization = await _organization(db_session)
    configured_role = await _role(db_session)
    explicit_role = await _role(db_session)
    await set_default_base_role(
        db_session,
        organization_id=organization.id,
        default_base_role_id=configured_role.id,
        updated_by="test",
    )

    resolved = await resolve_base_role(
        db_session,
        organization_id=organization.id,
        explicit_base_role_id=explicit_role.id,
    )

    assert resolved.id == explicit_role.id
