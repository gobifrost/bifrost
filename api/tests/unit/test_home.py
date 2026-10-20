from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from shared import home
from shared.sdk_users import set_platform_admin
from src.core.principal import UserPrincipal
from src.models.enums import AgentAccessLevel, AppAccessLevel, FormAccessLevel
from src.models.contracts.home import HomeCollectionWrite, HomePreferenceWrite
from src.models.orm.agents import Agent
from src.models.orm.applications import Application
from src.models.orm.forms import Form
from src.models.orm.home import HomeCollection, HomeResourcePreference
from src.models.orm.organizations import Organization
from src.models.orm.users import User


pytestmark = pytest.mark.asyncio


def _principal(
    user: User,
    *,
    is_superuser: bool = False,
    is_external: bool = False,
    embed: bool = False,
) -> UserPrincipal:
    return UserPrincipal(
        user_id=user.id,
        email=user.email,
        organization_id=user.organization_id,
        name=user.name or "",
        is_superuser=is_superuser,
        is_external=is_external,
        embed=embed,
    )


async def _org(db, name: str) -> Organization:
    row = Organization(name=name, domain=f"{uuid4().hex}.example", created_by="test")
    db.add(row)
    await db.flush()
    return row


async def _user(
    db, org: Organization | None, name: str, *, superuser: bool = False, external: bool = False
) -> User:
    row = User(
        email=f"{uuid4().hex}@example.com",
        name=name,
        is_superuser=superuser,
        is_external=external,
        is_verified=True,
        organization_id=org.id if org else None,
    )
    db.add(row)
    await db.flush()
    if superuser:
        await set_platform_admin(db, row, True, assigned_by="test")
    return row


async def _launchable_app(db, name: str, org: Organization | None) -> Application:
    row = Application(
        name=name,
        slug=f"{name.lower().replace(' ', '-')}-{uuid4().hex[:8]}",
        organization_id=org.id if org else None,
        access_level=AppAccessLevel.EVERYONE.value,
        published_snapshot={},
        created_by="test",
    )
    db.add(row)
    await db.flush()
    return row


async def _launchable_form(db, name: str, org: Organization | None) -> Form:
    row = Form(
        name=name,
        organization_id=org.id if org else None,
        access_level=FormAccessLevel.EVERYONE,
        is_active=True,
        created_by="test",
    )
    db.add(row)
    await db.flush()
    return row


async def _launchable_agent(db, name: str, org: Organization | None) -> Agent:
    row = Agent(
        name=name,
        description="Chat agent",
        system_prompt="Help",
        organization_id=org.id if org else None,
        access_level=AgentAccessLevel.EVERYONE,
        channels=["chat"],
        is_active=True,
        created_by="test",
    )
    db.add(row)
    await db.flush()
    return row


def _collection_write(**overrides) -> HomeCollectionWrite:
    values = {
        "name": "Ops",
        "description": "",
        "icon": "folder",
        "shared": False,
        "organization_id": None,
        "resource_keys": [],
    }
    values.update(overrides)
    return HomeCollectionWrite(**values)


async def test_catalog_uses_launchable_resource_repositories_and_preferences(
    db_session,
):
    org = await _org(db_session, "Catalog Org")
    user = await _user(db_session, org, "Catalog User")
    app = await _launchable_app(db_session, "Launch App", org)
    form = await _launchable_form(db_session, "Launch Form", org)
    agent = await _launchable_agent(db_session, "Launch Agent", org)
    db_session.add(
        HomeResourcePreference(
            user_id=user.id,
            resource_key=f"app:{app.id}",
            pinned=True,
        )
    )
    await db_session.flush()

    response = await home.get_home(db_session, _principal(user))
    keys = {resource.key for resource in response.resources}

    assert {f"app:{app.id}", f"form:{form.id}", f"agent:{agent.id}"} <= keys
    assert (
        next(
            resource
            for resource in response.resources
            if resource.key == f"app:{app.id}"
        ).pinned
        is True
    )


async def test_catalog_includes_resource_logo_metadata(db_session):
    org = await _org(db_session, "Logo Org")
    user = await _user(db_session, org, "Logo User")
    app = await _launchable_app(db_session, "Logo App", org)
    form = await _launchable_form(db_session, "Logo Form", org)
    agent = await _launchable_agent(db_session, "Logo Agent", org)
    app.logo_content_type = "image/png"
    app.logo_thumbnail_version = "a" * 64
    form.logo_content_type = "image/png"
    form.logo_thumbnail_version = "b" * 64
    agent.logo_content_type = "image/png"
    agent.logo_thumbnail_version = "c" * 64
    await db_session.flush()

    response = await home.get_home(db_session, _principal(user))
    by_key = {resource.key: resource for resource in response.resources}

    assert by_key[f"app:{app.id}"].logo_url == (
        f"/api/applications/{app.id}/logo?v={'a' * 64}"
    )
    assert by_key[f"app:{app.id}"].logo_version == "a" * 64
    assert by_key[f"form:{form.id}"].logo_url == (
        f"/api/forms/{form.id}/logo?v={'b' * 64}"
    )
    assert by_key[f"form:{form.id}"].logo_version == "b" * 64
    assert by_key[f"agent:{agent.id}"].logo_url == (
        f"/api/agents/{agent.id}/logo?v={'c' * 64}"
    )
    assert by_key[f"agent:{agent.id}"].logo_version == "c" * 64


async def test_collection_membership_does_not_confer_resource_access(db_session):
    org1 = await _org(db_session, "Home Org 1")
    org2 = await _org(db_session, "Home Org 2")
    admin = await _user(db_session, None, "Admin", superuser=True)
    user = await _user(db_session, org1, "Org 1 User")
    visible_app = await _launchable_app(db_session, "Visible App", org1)
    hidden_app = await _launchable_app(db_session, "Hidden App", org2)
    db_session.add(
        HomeCollection(
            owner_id=admin.id,
            shared=True,
            organization_id=org1.id,
            name="Shared Ops",
            description="",
            icon="folder",
            resource_keys=[
                f"app:{visible_app.id}",
                f"app:{hidden_app.id}",
                f"app:{uuid4()}",
            ],
        )
    )
    await db_session.flush()

    response = await home.get_home(db_session, _principal(user))
    collection = next(
        item for item in response.collections if item.name == "Shared Ops"
    )

    assert collection.resource_keys == [f"app:{visible_app.id}"]
    assert f"app:{hidden_app.id}" not in {
        resource.key for resource in response.resources
    }


async def test_private_and_shared_collection_boundaries(db_session):
    org1 = await _org(db_session, "Private Org 1")
    org2 = await _org(db_session, "Private Org 2")
    owner = await _user(db_session, org1, "Owner")
    peer = await _user(db_session, org1, "Peer")
    outsider = await _user(db_session, org2, "Outsider")
    admin = await _user(db_session, None, "Admin", superuser=True)
    external = await _user(db_session, org1, "External", external=True)

    private = HomeCollection(
        owner_id=owner.id,
        shared=False,
        organization_id=None,
        name="Private",
        description="",
        icon="folder",
        resource_keys=[],
    )
    org_shared = HomeCollection(
        owner_id=admin.id,
        shared=True,
        organization_id=org1.id,
        name="Org Shared",
        description="",
        icon="folder",
        resource_keys=[],
    )
    global_shared = HomeCollection(
        owner_id=admin.id,
        shared=True,
        organization_id=None,
        name="Global Shared",
        description="",
        icon="folder",
        resource_keys=[],
    )
    db_session.add_all([private, org_shared, global_shared])
    await db_session.flush()

    assert [
        item.name
        for item in (await home.get_home(db_session, _principal(owner))).collections
    ] == [
        "Global Shared",
        "Org Shared",
        "Private",
    ]
    assert "Private" not in [
        item.name
        for item in (await home.get_home(db_session, _principal(peer))).collections
    ]
    assert "Org Shared" not in [
        item.name
        for item in (await home.get_home(db_session, _principal(outsider))).collections
    ]
    assert "Global Shared" not in [
        item.name
        for item in (
            await home.get_home(db_session, _principal(external, is_external=True))
        ).collections
    ]
    assert {"Global Shared", "Org Shared"} <= {
        item.name
        for item in (
            await home.get_home(db_session, _principal(admin, is_superuser=True))
        ).collections
    }


async def test_shared_collection_writes_require_named_authority(db_session):
    org = await _org(db_session, "Shared Edit Org")
    admin = await _user(db_session, None, "Admin", superuser=True)
    user = await _user(db_session, org, "User")

    with pytest.raises(HTTPException) as denied_create:
        await home.save_collection(
            db_session,
            _principal(user),
            _collection_write(shared=True, organization_id=org.id),
        )
    assert denied_create.value.status_code == 403

    shared = await home.save_collection(
        db_session,
        _principal(admin, is_superuser=True),
        _collection_write(shared=True, organization_id=org.id),
    )
    with pytest.raises(HTTPException) as denied_edit:
        await home.save_collection(
            db_session,
            _principal(user),
            _collection_write(name="Nope", shared=True, organization_id=org.id),
            shared.id,
        )
    assert denied_edit.value.status_code == 403


async def test_shared_to_private_collection_becomes_current_admin_private_collection(
    db_session,
):
    owner_admin = await _user(db_session, None, "Owner Admin", superuser=True)
    editor_admin = await _user(db_session, None, "Editor Admin", superuser=True)
    row = HomeCollection(
        owner_id=owner_admin.id,
        shared=True,
        organization_id=None,
        name="Shared",
        description="",
        icon="folder",
        resource_keys=[],
    )
    db_session.add(row)
    await db_session.flush()

    result = await home.save_collection(
        db_session,
        _principal(editor_admin, is_superuser=True),
        _collection_write(name="My Copy", shared=False, organization_id=None),
        row.id,
    )
    await db_session.refresh(row)

    assert result.shared is False
    assert row.owner_id == editor_admin.id
    # The old owner now needs explicit Read All Home authority. Platform
    # Admin holds that power through the wildcard, rather than ownership.
    assert await home.get_collection(
        db_session, _principal(owner_admin, is_superuser=True), row.id
    ) is row
    from shared.sdk_users import set_platform_admin
    owner_admin.organization_id = (await _org(db_session, "Contoso Former Owner")).id
    await set_platform_admin(db_session, owner_admin, False, assigned_by="test")
    with pytest.raises(HTTPException) as owner_denied:
        await home.get_collection(db_session, _principal(owner_admin), row.id)
    assert owner_denied.value.status_code == 404


async def test_unavailable_collection_refs_and_preferences_are_rejected(db_session):
    org = await _org(db_session, "Unavailable Org")
    user = await _user(db_session, org, "User")

    with pytest.raises(HTTPException) as collection_error:
        await home.save_collection(
            db_session,
            _principal(user),
            _collection_write(resource_keys=[f"app:{uuid4()}"]),
        )
    assert collection_error.value.status_code == 422

    with pytest.raises(HTTPException) as preference_error:
        await home.save_preference(
            db_session,
            _principal(user),
            f"app:{uuid4()}",
            HomePreferenceWrite(pinned=True),
        )
    assert preference_error.value.status_code == 404


async def test_edit_preserves_existing_hidden_refs_without_exposing_them(db_session):
    org1 = await _org(db_session, "Hidden Preserve Org 1")
    org2 = await _org(db_session, "Hidden Preserve Org 2")
    user = await _user(db_session, org1, "Owner")
    visible_app = await _launchable_app(db_session, "Visible Preserve App", org1)
    hidden_app = await _launchable_app(db_session, "Hidden Preserve App", org2)
    stale_key = f"app:{uuid4()}"
    row = HomeCollection(
        owner_id=user.id,
        shared=False,
        organization_id=None,
        name="Daily",
        description="",
        icon="folder",
        resource_keys=[f"app:{visible_app.id}", f"app:{hidden_app.id}", stale_key],
    )
    db_session.add(row)
    await db_session.flush()

    principal = _principal(user)
    response = await home.get_home(db_session, principal)
    public = next(item for item in response.collections if item.id == row.id)
    assert public.resource_keys == [f"app:{visible_app.id}"]

    result = await home.save_collection(
        db_session,
        principal,
        _collection_write(
            name="Daily renamed",
            resource_keys=[f"app:{visible_app.id}"],
        ),
        row.id,
    )
    await db_session.refresh(row)

    assert result.resource_keys == [f"app:{visible_app.id}"]
    assert row.resource_keys == [
        f"app:{visible_app.id}",
        f"app:{hidden_app.id}",
        stale_key,
    ]


async def test_edit_allows_submitted_existing_hidden_refs_but_not_unknown_refs(
    db_session,
):
    org1 = await _org(db_session, "Known Hidden Org 1")
    org2 = await _org(db_session, "Known Hidden Org 2")
    user = await _user(db_session, org1, "Owner")
    visible_app = await _launchable_app(db_session, "Known Visible App", org1)
    hidden_app = await _launchable_app(db_session, "Known Hidden App", org2)
    row = HomeCollection(
        owner_id=user.id,
        shared=False,
        organization_id=None,
        name="Known Hidden",
        description="",
        icon="folder",
        resource_keys=[f"app:{visible_app.id}", f"app:{hidden_app.id}"],
    )
    db_session.add(row)
    await db_session.flush()

    principal = _principal(user)
    await home.save_collection(
        db_session,
        principal,
        _collection_write(
            name="Known Hidden",
            resource_keys=[f"app:{hidden_app.id}", f"app:{visible_app.id}"],
        ),
        row.id,
    )
    await db_session.refresh(row)
    assert row.resource_keys == [f"app:{visible_app.id}", f"app:{hidden_app.id}"]

    with pytest.raises(HTTPException) as unknown_error:
        await home.save_collection(
            db_session,
            principal,
            _collection_write(
                name="Known Hidden",
                resource_keys=[f"app:{visible_app.id}", f"app:{uuid4()}"],
            ),
            row.id,
        )
    assert unknown_error.value.status_code == 422


async def test_audience_change_rejects_when_existing_hidden_refs_would_be_lost(
    db_session,
):
    org1 = await _org(db_session, "Hidden Audience Org 1")
    org2 = await _org(db_session, "Hidden Audience Org 2")
    admin = await _user(db_session, None, "Audience Admin", superuser=True)
    visible_app = await _launchable_app(db_session, "Audience Visible App", org1)
    stale_key = f"app:{uuid4()}"
    row = HomeCollection(
        owner_id=admin.id,
        shared=True,
        organization_id=org1.id,
        name="Audience",
        description="",
        icon="folder",
        resource_keys=[f"app:{visible_app.id}", stale_key],
    )
    db_session.add(row)
    await db_session.flush()

    with pytest.raises(HTTPException) as admin_error:
        await home.save_collection(
            db_session,
            _principal(admin, is_superuser=True),
            _collection_write(
                name="Audience",
                shared=True,
                organization_id=org2.id,
                resource_keys=[],
            ),
            row.id,
        )
    assert admin_error.value.status_code == 409
    assert "unavailable resources" in admin_error.value.detail
    assert stale_key not in admin_error.value.detail


async def test_preference_open_and_pin_persist_for_available_resource(db_session):
    org = await _org(db_session, "Preference Org")
    user = await _user(db_session, org, "User")
    app = await _launchable_app(db_session, "Pinned App", org)
    principal = _principal(user)

    await home.save_preference(
        db_session,
        principal,
        f"app:{app.id}",
        HomePreferenceWrite(pinned=True, opened=True),
    )
    pref = await db_session.scalar(
        select(HomeResourcePreference).where(HomeResourcePreference.user_id == user.id)
    )

    assert pref is not None
    assert pref.pinned is True
    assert pref.last_opened_at is not None


async def test_home_writes_use_named_scope_and_ignore_actor_admin_claim(db_session):
    from src.models.orm.users import Role, RolePermission, UserRole, UserRoleBoundary

    org = await _org(db_session, "Contoso")
    target = await _org(db_session, "Fabrikam")
    user = await _user(db_session, org, "Contoso User")
    forged = _principal(user, is_superuser=True)
    with pytest.raises(HTTPException) as denied:
        await home.save_collection(db_session, forged, _collection_write(shared=True, organization_id=target.id))
    assert denied.value.status_code == 403

    role = Role(name=f"Contoso Home Editor {uuid4().hex}", created_by="test")
    db_session.add(role)
    await db_session.flush()
    db_session.add_all([
        RolePermission(role_id=role.id, permission="home.readwrite"),
        UserRole(user_id=user.id, role_id=role.id, assigned_by="test"),
        UserRoleBoundary(user_id=user.id, role_id=role.id, kind="organization", organization_id=target.id),
    ])
    await db_session.flush()
    saved = await home.save_collection(db_session, _principal(user), _collection_write(shared=True, organization_id=target.id))
    assert saved.organization_id == target.id and saved.can_edit
    with pytest.raises(HTTPException) as wrong_boundary:
        await home.save_collection(db_session, forged, _collection_write(shared=True, organization_id=org.id))
    assert wrong_boundary.value.status_code == 403


async def test_home_private_owner_and_all_permissions_use_original_user_and_reach():
    from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
    from src.services.authorization.enforce import Caller
    original_id, home_org, target_org = uuid4(), uuid4(), uuid4()
    actor = UserPrincipal(user_id=uuid4(), email="actor@contoso.example", organization_id=home_org, is_superuser=True)
    caller = Caller(actor, AuthorizationContext(
        user_id=original_id, home_organization_id=home_org, base_role_id=uuid4(),
        is_external=False, base_permissions=frozenset(), role_grants=(
            RoleGrant(uuid4(), frozenset({"home.read.all", "home.readwrite.all"}), (Boundary(BoundaryKind.ORGANIZATION, target_org),)),
        ),
    ))
    own = HomeCollection(owner_id=original_id, organization_id=None, shared=False)
    actor_owned = HomeCollection(owner_id=actor.user_id, organization_id=None, shared=False)
    scoped = HomeCollection(owner_id=uuid4(), organization_id=target_org, shared=False)
    foreign = HomeCollection(owner_id=uuid4(), organization_id=uuid4(), shared=False)
    assert home.can_read_collection(own, caller) and home.can_edit_collection(own, caller)
    assert not home.can_read_collection(actor_owned, caller)
    assert home.can_read_collection(scoped, caller) and home.can_edit_collection(scoped, caller)
    assert not home.can_read_collection(foreign, caller) and not home.can_edit_collection(foreign, caller)
