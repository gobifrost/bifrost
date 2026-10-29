"""SDK bootstrap selects only current original-user organization reach."""

from uuid import uuid4

import pytest
from sqlalchemy import delete

from shared.builtin_roles import USER_ROLE_ID
from shared.sdk_context import SdkContextError, get_sdk_context
from src.core.principal import UserPrincipal
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, User, UserRole, UserRoleBoundary


async def _person(db, *, actor_flag=False):
    home, foreign = [Organization(name=name, created_by="test") for name in ("Contoso", "Fabrikam")]
    db.add_all([home, foreign])
    await db.flush()
    person = User(email=f"context-{uuid4().hex}@contoso.example", organization_id=home.id, base_role_id=USER_ROLE_ID)
    db.add(person)
    await db.flush()
    return UserPrincipal(user_id=person.id, email=person.email, organization_id=home.id, is_superuser=actor_flag, is_provider_org=True), home, foreign


async def test_bootstrap_actor_flags_cannot_grant_foreign_reach(db_session):
    principal, home, foreign = await _person(db_session, actor_flag=True)
    with pytest.raises(SdkContextError) as error:
        await get_sdk_context(db_session, principal, org_id=foreign.id)
    assert error.value.status_code == 403
    result = await get_sdk_context(db_session, principal)
    assert result["organization"]["id"] == str(home.id)


async def test_bootstrap_named_role_placement_is_current_and_can_be_revoked(db_session):
    principal, _home, foreign = await _person(db_session)
    role = Role(name=f"Fabrikam Placement {uuid4().hex}", created_by="test")
    db_session.add(role)
    await db_session.flush()
    db_session.add_all([
        UserRole(user_id=principal.user_id, role_id=role.id, assigned_by="test"),
        UserRoleBoundary(user_id=principal.user_id, role_id=role.id, kind="organization", organization_id=foreign.id),
    ])
    await db_session.flush()
    result = await get_sdk_context(db_session, principal, org_id=foreign.id)
    assert result["organization"]["id"] == str(foreign.id)
    await db_session.execute(delete(UserRole).where(UserRole.user_id == principal.user_id, UserRole.role_id == role.id))
    await db_session.flush()
    with pytest.raises(SdkContextError) as error:
        await get_sdk_context(db_session, principal, org_id=foreign.id)
    assert error.value.status_code == 403
