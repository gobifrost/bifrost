from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from shared.resource_access import ResourceAccessResolver, ResourceKind, can_write_resource
from src.core.principal import UserPrincipal
from src.models.orm import AppRole, Application, Organization, Role, User, UserRole
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller


def _caller(
    *permissions: str,
    home=None,
    external: bool = False,
    original_user_id=None,
    actor_id=None,
) -> Caller:
    home = home or uuid4()
    original_user_id = original_user_id or uuid4()
    return Caller(
        UserPrincipal(
            user_id=actor_id or original_user_id,
            email="resource-access@example.test",
            organization_id=home,
        ),
        AuthorizationContext(
            user_id=original_user_id,
            home_organization_id=home,
            base_role_id=uuid4(),
            is_external=external,
            base_permissions=frozenset(permissions),
        ),
    )


def _resource(*, organization_id, access_level="everyone", owner_user_id=None):
    return SimpleNamespace(
        id=uuid4(),
        organization_id=organization_id,
        access_level=access_level,
        owner_user_id=owner_user_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(ResourceKind))
async def test_everyone_requires_original_reach_and_basic_permission(kind):
    home, foreign = uuid4(), uuid4()
    caller = _caller(f"{kind.value}s.readbasic", home=home)
    resolver = ResourceAccessResolver(None, caller)

    assert await resolver.can_read(kind, _resource(organization_id=home))
    assert not await resolver.can_read(kind, _resource(organization_id=foreign))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(ResourceKind))
async def test_everyone_does_not_bypass_missing_basic_permission(kind):
    home = uuid4()
    caller = _caller(home=home)

    assert not await ResourceAccessResolver(None, caller).can_read(
        kind, _resource(organization_id=home)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(ResourceKind))
async def test_authenticated_sharing_excludes_external_users(kind):
    home = uuid4()
    caller = _caller(f"{kind.value}s.readbasic", home=home, external=True)
    resolver = ResourceAccessResolver(None, caller)

    assert not await resolver.can_read(
        kind, _resource(organization_id=home, access_level="authenticated")
    )
    assert await resolver.can_read(
        kind, _resource(organization_id=home, access_level="everyone")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(ResourceKind))
async def test_unknown_access_level_never_grants_ordinary_sharing(kind):
    home = uuid4()
    caller = _caller(f"{kind.value}s.readbasic", home=home)

    assert not await ResourceAccessResolver(None, caller).can_read(
        kind, _resource(organization_id=home, access_level="unknown")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(ResourceKind))
async def test_global_read_uses_home_fallback_unless_global_is_explicit(kind):
    home = uuid4()
    caller = _caller(f"{kind.value}s.readbasic", home=home)
    resolver = ResourceAccessResolver(None, caller)
    resource = _resource(organization_id=None, access_level="everyone")

    assert await resolver.can_read(kind, resource)
    assert not await resolver.can_read(
        kind, resource, selected_organization_id=None
    )


@pytest.mark.asyncio
async def test_app_read_needs_basic_even_when_management_read_exists():
    home = uuid4()
    caller = _caller("apps.read", home=home)
    resolver = ResourceAccessResolver(None, caller)
    app = _resource(organization_id=home)

    assert not await resolver.can_read(ResourceKind.APP, app)
    assert resolver.can_inspect(ResourceKind.APP, app)


@pytest.mark.parametrize("kind", list(ResourceKind))
def test_writes_use_the_actual_organization_not_global_fallback(kind):
    home = uuid4()
    caller = _caller(f"{kind.value}s.readwrite", home=home)
    global_resource = _resource(organization_id=None)
    home_resource = _resource(organization_id=home)

    assert not can_write_resource(kind, caller, global_resource)
    assert can_write_resource(kind, caller, home_resource)


def test_management_read_uses_the_actual_organization_not_global_fallback():
    home = uuid4()
    caller = _caller("apps.read", home=home)
    global_app = _resource(organization_id=None)

    assert not ResourceAccessResolver(None, caller).can_inspect(ResourceKind.APP, global_app)


@pytest.mark.asyncio
async def test_private_agent_owner_and_all_permissions_follow_the_private_boundary():
    home, owner, other = uuid4(), uuid4(), uuid4()
    private = _resource(organization_id=home, access_level="private", owner_user_id=owner)

    owner_caller = _caller(home=home, original_user_id=owner)
    readwrite_all = _caller("agents.readwrite.all", home=home, original_user_id=other)
    read_all = _caller("agents.read.all", home=home, original_user_id=other)

    assert await ResourceAccessResolver(None, owner_caller).can_read(ResourceKind.AGENT, private)
    assert can_write_resource(ResourceKind.AGENT, owner_caller, private)
    assert not await ResourceAccessResolver(None, readwrite_all).can_read(
        ResourceKind.AGENT, private
    )
    assert can_write_resource(ResourceKind.AGENT, readwrite_all, private)
    assert await ResourceAccessResolver(None, read_all).can_read(ResourceKind.AGENT, private)

    unowned = _resource(organization_id=home, access_level="private")
    assert not ResourceAccessResolver(None, owner_caller).can_inspect(ResourceKind.AGENT, unowned)
    assert not can_write_resource(ResourceKind.AGENT, owner_caller, unowned)


@pytest.mark.asyncio
async def test_role_sharing_matches_the_original_user_not_the_actor(db_session):
    organization = Organization(name="Home", created_by="test")
    role = Role(name="Shared app", created_by="test")
    original = User(email="original-resource-access@example.test", organization=organization)
    actor = User(email="actor-resource-access@example.test", organization=organization)
    app = Application(
        name="Shared app",
        slug="shared-resource-access",
        organization=organization,
        access_level="role_based",
    )
    db_session.add_all([organization, role, original, actor, app])
    await db_session.flush()
    db_session.add_all([
        UserRole(user_id=actor.id, role_id=role.id, assigned_by="test"),
        AppRole(app_id=app.id, role_id=role.id, assigned_by="test"),
    ])
    await db_session.flush()

    caller = _caller(
        "apps.readbasic",
        home=organization.id,
        original_user_id=original.id,
        actor_id=actor.id,
    )
    resolver = ResourceAccessResolver(db_session, caller)

    assert not await resolver.can_read(ResourceKind.APP, app)

    db_session.add(UserRole(user_id=original.id, role_id=role.id, assigned_by="test"))
    await db_session.flush()

    assert await ResourceAccessResolver(db_session, caller).can_read(ResourceKind.APP, app)
