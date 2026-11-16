"""The protocol-independent provisioning rules, against the database."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.models.orm.identity_providers import (
    IdentityProvider,
    IdentityProviderGroupMapping,
    IdentityProviderGroupRole,
    IdentityProviderUser,
    OrganizationDomain,
)
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, User, UserRole
from src.services.identity_provisioning.core import (
    GroupRecord,
    ProvisioningError,
    ProvisioningErrorKind,
    Provisioner,
    UserRecord,
)


def _record(email: str, *, external_id: str | None = None, active: bool = True) -> UserRecord:
    return UserRecord(
        user_name=email,
        email=email,
        external_id=external_id or f"ext-{email}",
        display_name="Pat Example",
        active=active,
        attributes={"userName": email},
    )


@pytest_asyncio.fixture
async def setup(db_session):
    suffix = uuid4().hex[:10]
    domain = f"contoso-{suffix}.example"
    organization = Organization(name=f"Contoso {suffix}", created_by="test")
    other = Organization(name=f"Fabrikam {suffix}", created_by="test")
    db_session.add_all([organization, other])
    await db_session.flush()
    db_session.add(
        OrganizationDomain(
            organization_id=organization.id,
            domain=domain,
            verification_token="t",
            verified_at=datetime.now(timezone.utc),
            created_by="test",
        )
    )
    connection = IdentityProvider(
        organization_id=organization.id,
        name="Contoso Entra",
        source_type="scim",
        vendor="entra",
        created_by="test",
    )
    role = Role(name=f"Support Tech {suffix}", created_by="test")
    db_session.add_all([connection, role])
    await db_session.flush()
    return {
        "session": db_session,
        "organization": organization,
        "other": other,
        "domain": domain,
        "connection": connection,
        "role": role,
    }


async def _user(session, link: IdentityProviderUser) -> User:
    user = await session.get(User, link.user_id)
    await session.refresh(user)
    return user


async def _map(session, group_id, role_ids=()):
    session.add(IdentityProviderGroupMapping(group_id=group_id, created_by="test"))
    await session.flush()
    for role_id in role_ids:
        session.add(IdentityProviderGroupRole(group_id=group_id, role_id=role_id))
    await session.flush()


@pytest.mark.asyncio
async def test_creates_an_active_verified_user_in_the_connection_org(setup):
    provisioner = Provisioner(setup["session"], setup["connection"])

    link = await provisioner.create_user(_record(f"pat@{setup['domain']}"))

    user = await _user(setup["session"], link)
    assert user.organization_id == setup["organization"].id
    assert user.is_active and user.is_verified and not user.is_registered
    assert link.created_user
    assert [topic for topic, _ in provisioner.pending_events] == ["user.provisioned"]


@pytest.mark.asyncio
async def test_refuses_an_email_domain_the_org_has_not_verified(setup):
    provisioner = Provisioner(setup["session"], setup["connection"])

    with pytest.raises(ProvisioningError) as raised:
        await provisioner.create_user(_record("pat@unclaimed.example"))

    assert raised.value.kind is ProvisioningErrorKind.DOMAIN_NOT_CLAIMED


@pytest.mark.asyncio
async def test_adopts_an_existing_user_in_the_same_org(setup):
    session = setup["session"]
    email = f"invited@{setup['domain']}"
    existing = User(email=email, organization_id=setup["organization"].id)
    session.add(existing)
    await session.flush()

    link = await Provisioner(session, setup["connection"]).create_user(_record(email))

    assert link.user_id == existing.id
    assert not link.created_user


@pytest.mark.asyncio
async def test_refuses_to_take_a_user_from_another_org(setup):
    session = setup["session"]
    email = f"elsewhere@{setup['domain']}"
    session.add(User(email=email, organization_id=setup["other"].id))
    await session.flush()

    with pytest.raises(ProvisioningError) as raised:
        await Provisioner(session, setup["connection"]).create_user(_record(email))

    assert raised.value.kind is ProvisioningErrorKind.CONFLICT


@pytest.mark.asyncio
async def test_mapped_group_grants_tagged_roles_and_removing_it_revokes_access(setup):
    session, role = setup["session"], setup["role"]
    provisioner = Provisioner(session, setup["connection"])
    link = await provisioner.create_user(_record(f"tech@{setup['domain']}"))
    group = await provisioner.create_group(
        GroupRecord(display_name="Techs", external_id="g1", attributes={}), [link.id]
    )
    await _map(session, group.id, [role.id])
    await provisioner.reconcile_all()

    assignment = await session.scalar(
        select(UserRole).where(UserRole.user_id == link.user_id, UserRole.role_id == role.id)
    )
    assert assignment is not None
    assert assignment.identity_provider_id == setup["connection"].id

    await provisioner.set_group_members(group, [])

    user = await _user(session, link)
    assert not user.is_active
    assert (
        await session.scalar(
            select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id)
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_manual_grant_of_a_mapped_role_survives_unmapping(setup):
    session, role = setup["session"], setup["role"]
    provisioner = Provisioner(session, setup["connection"])
    link = await provisioner.create_user(_record(f"manual@{setup['domain']}"))
    session.add(UserRole(user_id=link.user_id, role_id=role.id, assigned_by="admin@test"))
    await session.flush()
    group = await provisioner.create_group(
        GroupRecord(display_name="Techs", external_id="g1", attributes={}), [link.id]
    )
    await _map(session, group.id, [role.id])
    await provisioner.reconcile_all()

    await provisioner.delete_group(group)

    manual = await session.scalar(
        select(UserRole).where(UserRole.user_id == link.user_id, UserRole.role_id == role.id)
    )
    assert manual is not None and manual.identity_provider_id is None


@pytest.mark.asyncio
async def test_a_connection_without_mappings_keeps_ungrouped_users_active(setup):
    provisioner = Provisioner(setup["session"], setup["connection"])

    link = await provisioner.create_user(_record(f"solo@{setup['domain']}"))

    assert (await _user(setup["session"], link)).is_active


@pytest.mark.asyncio
async def test_inactive_record_disables_the_user(setup):
    provisioner = Provisioner(setup["session"], setup["connection"])
    email = f"leaver@{setup['domain']}"
    link = await provisioner.create_user(_record(email))

    await provisioner.replace_user(link, _record(email, external_id=link.external_id, active=False))

    assert not (await _user(setup["session"], link)).is_active
    assert "user.deprovisioned" in [topic for topic, _ in provisioner.pending_events]


@pytest.mark.asyncio
async def test_deleting_a_never_signed_in_user_it_created_removes_the_account(setup):
    session = setup["session"]
    provisioner = Provisioner(session, setup["connection"])
    link = await provisioner.create_user(_record(f"probe@{setup['domain']}"))
    user_id = link.user_id

    await provisioner.delete_user(link)

    session.expunge_all()
    assert await session.get(User, user_id) is None


@pytest.mark.asyncio
async def test_deleting_a_user_who_signed_in_disables_and_unlinks(setup):
    session = setup["session"]
    provisioner = Provisioner(session, setup["connection"])
    link = await provisioner.create_user(_record(f"veteran@{setup['domain']}"))
    user = await _user(session, link)
    user.last_login = datetime.now(timezone.utc)
    await session.flush()

    await provisioner.delete_user(link)

    await session.refresh(user)
    assert not user.is_active
    assert (
        await session.scalar(
            select(IdentityProviderUser).where(IdentityProviderUser.user_id == user.id)
        )
        is None
    )
