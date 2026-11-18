"""Provisioning revokes at once and batches grants into the access pass.

Every grant (activation, role, rename) changes the global authority epoch,
which retires every access token on the instance. These tests count epoch
changes as ``test_authz_epoch_triggers`` does: constraint triggers run
IMMEDIATE, the once-per-transaction flag is cleared before each simulated
request, and the count is this transaction's updates to ``authz_versions``.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from src.models.orm.identity_providers import (
    IdentityProvider,
    IdentityProviderGroupMapping,
    IdentityProviderGroupRole,
    IdentityProviderUser,
    OrganizationDomain,
)
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, User, UserRole
from src.services.identity_provisioning.access_pass import run_access_pass
from src.services.identity_provisioning.core import GroupRecord, Provisioner, UserRecord

_CLEAR_FLAG = text("SELECT set_config('bifrost.authz_epoch_bumped', '', true)")
_EPOCH_UPDATES = text(
    "SELECT COALESCE((SELECT n_tup_upd FROM pg_stat_xact_user_tables "
    "WHERE relname = 'authz_versions'), 0)"
)


class EpochCounter:
    def __init__(self, session) -> None:
        self.session = session

    async def start(self) -> None:
        await self.session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        self.baseline = await self._updates()

    async def request(self) -> None:
        """Begin a simulated request: its own transaction may bump once."""
        await self.session.flush()
        await self.session.execute(_CLEAR_FLAG)

    async def changes(self) -> int:
        await self.session.flush()
        return await self._updates() - self.baseline

    async def _updates(self) -> int:
        return (await self.session.execute(_EPOCH_UPDATES)).scalar_one()


def _record(email: str, *, name: str = "Pat Example") -> UserRecord:
    return UserRecord(
        user_name=email,
        email=email,
        external_id=f"ext-{email}",
        display_name=name,
        active=True,
        attributes={"userName": email},
    )


@pytest_asyncio.fixture
async def setup(db_session):
    suffix = uuid4().hex[:10]
    domain = f"contoso-{suffix}.example"
    organization = Organization(name=f"Contoso {suffix}", created_by="test")
    db_session.add(organization)
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
    counter = EpochCounter(db_session)
    await counter.start()
    return {
        "session": db_session,
        "domain": domain,
        "connection": connection,
        "role": role,
        "epoch": counter,
    }


async def _mapped_group(setup):
    session = setup["session"]
    provisioner = Provisioner(session, setup["connection"])
    group = await provisioner.create_group(
        GroupRecord(display_name="Techs", external_id=f"g-{uuid4().hex}", attributes={}), []
    )
    session.add(IdentityProviderGroupMapping(group_id=group.id, created_by="test"))
    await session.flush()
    session.add(IdentityProviderGroupRole(group_id=group.id, role_id=setup["role"].id))
    await session.flush()
    return group


async def _refreshed(session, link: IdentityProviderUser) -> tuple[User, IdentityProviderUser]:
    # Refresh the link first: a rolled-back savepoint expires it.
    await session.refresh(link)
    user = await session.get(User, link.user_id)
    await session.refresh(user)
    return user, link


async def _has_role(session, user_id, role_id) -> bool:
    return (
        await session.scalar(
            select(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role_id)
        )
    ) is not None


@pytest.mark.asyncio
async def test_a_provisioning_burst_changes_the_epoch_once_in_the_pass(setup):
    session, epoch, role = setup["session"], setup["epoch"], setup["role"]
    group = await _mapped_group(setup)
    links = []
    for index in range(5):
        await epoch.request()
        links.append(
            await Provisioner(session, setup["connection"]).create_user(
                _record(f"tech{index}@{setup['domain']}")
            )
        )
    await epoch.request()
    await Provisioner(session, setup["connection"]).set_group_members(
        group, [link.id for link in links]
    )

    assert await epoch.changes() == 0
    for link in links:
        user, link = await _refreshed(session, link)
        assert not user.is_active and link.access_pending_since is not None
        assert not await _has_role(session, user.id, role.id)

    await epoch.request()
    result = await run_access_pass(session)

    assert result.failed == 0 and result.applied >= len(links)
    assert await epoch.changes() == 1
    for link in links:
        user, link = await _refreshed(session, link)
        assert user.is_active and link.access_pending_since is None
        assert await _has_role(session, user.id, role.id)


@pytest.mark.asyncio
async def test_access_changed_reports_the_grants_the_pass_applied(setup):
    session, role = setup["session"], setup["role"]
    group = await _mapped_group(setup)
    provisioner = Provisioner(session, setup["connection"])
    link = await provisioner.create_user(_record(f"tech@{setup['domain']}"))

    await provisioner.set_group_members(group, [link.id])

    topics = [topic for topic, _ in provisioner.pending_events]
    assert "user.groups_changed" in topics and "user.access_changed" not in topics

    result = await run_access_pass(session)

    [body] = [
        body
        for applied in result.provisioners
        for topic, body in applied.pending_events
        if topic == "user.access_changed" and body["user"]["id"] == str(link.user_id)
    ]
    assert body["access"] == {
        "activated": True,
        "roles_added": [str(role.id)],
        "roles_removed": [],
    }


@pytest.mark.asyncio
async def test_losing_the_mapped_group_revokes_in_the_request(setup):
    session, epoch, role = setup["session"], setup["epoch"], setup["role"]
    group = await _mapped_group(setup)
    provisioner = Provisioner(session, setup["connection"])
    link = await provisioner.create_user(_record(f"leaver@{setup['domain']}"))
    await provisioner.set_group_members(group, [link.id])
    await run_access_pass(session)

    await epoch.request()
    await provisioner.set_group_members(group, [])

    user, link = await _refreshed(session, link)
    assert not user.is_active
    assert not await _has_role(session, user.id, role.id)
    assert link.access_pending_since is None


@pytest.mark.asyncio
async def test_a_new_user_on_an_unmapped_connection_starts_active_without_a_bump(setup):
    session, epoch = setup["session"], setup["epoch"]

    await epoch.request()
    link = await Provisioner(session, setup["connection"]).create_user(
        _record(f"solo@{setup['domain']}")
    )

    user, link = await _refreshed(session, link)
    assert user.is_active and link.access_pending_since is None
    assert await epoch.changes() == 0


@pytest.mark.asyncio
async def test_a_rename_waits_for_the_pass(setup):
    session = setup["session"]
    provisioner = Provisioner(session, setup["connection"])
    email = f"pat@{setup['domain']}"
    link = await provisioner.create_user(_record(email))

    await provisioner.replace_user(link, _record(email, name="Pat Renamed"))

    user, link = await _refreshed(session, link)
    assert user.name == "Pat Example" and link.access_pending_since is not None
    await run_access_pass(session)
    user, link = await _refreshed(session, link)
    assert user.name == "Pat Renamed" and link.access_pending_since is None


@pytest.mark.asyncio
async def test_deleting_the_last_mapped_group_releases_users_outside_it(setup):
    session = setup["session"]
    group = await _mapped_group(setup)
    provisioner = Provisioner(session, setup["connection"])
    # Created after the mapping, never in the group: inactive.
    outsider = await provisioner.create_user(_record(f"outsider@{setup['domain']}"))
    assert not (await _refreshed(session, outsider))[0].is_active

    # Deleting a group changes policy: its own exclusive-lock transaction.
    await Provisioner(session, setup["connection"]).delete_group(group)
    await run_access_pass(session)

    user, link = await _refreshed(session, outsider)
    assert user.is_active and link.access_pending_since is None


@pytest.mark.asyncio
async def test_a_failing_link_rolls_back_alone_and_waits(setup, monkeypatch):
    session, role = setup["session"], setup["role"]
    group = await _mapped_group(setup)
    provisioner = Provisioner(session, setup["connection"])
    broken = await provisioner.create_user(_record(f"broken@{setup['domain']}"))
    fine = await provisioner.create_user(_record(f"fine@{setup['domain']}"))
    await provisioner.set_group_members(group, [broken.id, fine.id])
    queued_at = (await _refreshed(session, broken))[1].access_pending_since
    broken_user_id = broken.user_id

    audit = Provisioner._audit

    async def failing_audit(self, action, user, details, **kwargs):
        # Fails after activating, granting and queueing the event.
        if action == "identity_provider.user_activated" and user.id == broken_user_id:
            raise RuntimeError("boom")
        await audit(self, action, user, details, **kwargs)

    monkeypatch.setattr(Provisioner, "_audit", failing_audit)
    result = await run_access_pass(session)

    assert result.failed == 1
    fine_user, fine = await _refreshed(session, fine)
    assert fine_user.is_active and fine.access_pending_since is None
    broken_user, broken = await _refreshed(session, broken)
    assert not broken_user.is_active
    assert not await _has_role(session, broken_user_id, role.id)
    # Re-queued behind everything already waiting.
    assert broken.access_pending_since is not None and broken.access_pending_since > queued_at
    # Nothing from the rolled-back link leaks out after the commit.
    [applied] = result.provisioners
    assert broken_user_id not in applied.stale_users
    assert all(body["user"]["id"] != str(broken_user_id) for _, body in applied.pending_events)


@pytest.mark.asyncio
async def test_a_deadlocked_link_counts_as_a_conflict_and_waits(setup, monkeypatch):
    import asyncpg
    from sqlalchemy.exc import DBAPIError

    session = setup["session"]
    group = await _mapped_group(setup)
    provisioner = Provisioner(session, setup["connection"])
    link = await provisioner.create_user(_record(f"raced@{setup['domain']}"))
    await provisioner.set_group_members(group, [link.id])

    async def deadlocked(self, link_ids):
        raise DBAPIError("INSERT", {}, asyncpg.exceptions.DeadlockDetectedError("deadlock"))

    monkeypatch.setattr(Provisioner, "apply_pending", deadlocked)
    result = await run_access_pass(session)

    assert result.summary() == {"applied": 0, "failed": 0, "conflicts": 1}
    user, link = await _refreshed(session, link)
    assert not user.is_active and link.access_pending_since is not None


@pytest.mark.asyncio
async def test_the_oldest_grant_goes_first_across_connections(setup, monkeypatch):
    session = setup["session"]
    other = IdentityProvider(
        organization_id=setup["connection"].organization_id,
        name="Contoso Okta",
        source_type="scim",
        vendor="okta",
        created_by="test",
    )
    session.add(other)
    await session.flush()
    older = await Provisioner(session, other).create_user(_record(f"older@{setup['domain']}"))
    newer = await Provisioner(session, setup["connection"]).create_user(
        _record(f"newer@{setup['domain']}")
    )
    for link, renamed in ((older, "Older Renamed"), (newer, "Newer Renamed")):
        connection = other if link is older else setup["connection"]
        await Provisioner(session, connection).replace_user(
            link, _record(link.user_name, name=renamed)
        )
    monkeypatch.setattr("src.services.identity_provisioning.access_pass.BATCH_LIMIT", 1)

    await run_access_pass(session)

    assert (await _refreshed(session, older))[1].access_pending_since is None
    assert (await _refreshed(session, newer))[1].access_pending_since is not None


@pytest.mark.asyncio
async def test_a_waiting_grant_shows_on_the_users_identity_source(setup):
    from src.services.identity_providers import identity_sources

    session = setup["session"]
    group = await _mapped_group(setup)
    provisioner = Provisioner(session, setup["connection"])
    link = await provisioner.create_user(_record(f"waiting@{setup['domain']}"))
    await provisioner.set_group_members(group, [link.id])

    source = (await identity_sources(session, [link.user_id]))[link.user_id]
    assert source.name == "Contoso Entra" and source.access_pending_since is not None

    await run_access_pass(session)

    source = (await identity_sources(session, [link.user_id]))[link.user_id]
    assert source.access_pending_since is None


@pytest.mark.asyncio
async def test_a_new_hire_waiting_for_activation_is_listed_as_activating(setup):
    from shared.sdk_users import list_users
    from tests.helpers.authorization import admin_caller

    session = setup["session"]
    group = await _mapped_group(setup)
    provisioner = Provisioner(session, setup["connection"])
    hire = await provisioner.create_user(_record(f"hire@{setup['domain']}"))
    outsider = await provisioner.create_user(_record(f"outsider@{setup['domain']}"))
    await provisioner.set_group_members(group, [hire.id])

    async def listed():
        items, _ = await list_users(session, admin_caller(), search=setup["domain"])
        return {u.email.split("@")[0]: u for u in items}

    users = await listed()
    assert set(users) == {"hire"}
    assert not users["hire"].is_active
    source = users["hire"].identity_source
    assert source is not None and source.activating

    await run_access_pass(session)

    source = (await listed())["hire"].identity_source
    assert source is not None and not source.activating
    assert (await _refreshed(session, outsider))[0].is_active is False


@pytest.mark.asyncio
async def test_a_disabled_connections_grants_wait(setup):
    session, connection = setup["session"], setup["connection"]
    group = await _mapped_group(setup)
    provisioner = Provisioner(session, connection)
    link = await provisioner.create_user(_record(f"paused@{setup['domain']}"))
    await provisioner.set_group_members(group, [link.id])
    connection.is_enabled = False
    await session.flush()

    await run_access_pass(session)

    user, link = await _refreshed(session, link)
    assert not user.is_active and link.access_pending_since is not None
