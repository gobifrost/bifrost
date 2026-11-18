"""Identity provisioning against a concurrent transaction, with committed data.

Each test holds a lock in one session, starts the competing change in
another, checks that it waits, then lets the first commit and checks the
second decides from the committed state.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from src.models.orm.identity_providers import (
    IdentityProvider,
    IdentityProviderGroupMapping,
    IdentityProviderGroupRole,
    IdentityProviderToken,
    OrganizationDomain,
)
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, RolePermission, User, UserRole
from src.services.identity_provisioning.core import (
    GroupRecord,
    Provisioner,
    UserRecord,
)
from tests.helpers.authorization import admin_caller

# Long enough that a change which doesn't wait would have finished.
_STILL_WAITING = 0.5


def _record(email: str) -> UserRecord:
    return UserRecord(
        user_name=email,
        email=email,
        external_id=f"ext-{email}",
        display_name="Pat Example",
        active=True,
        attributes={},
    )


@pytest_asyncio.fixture
async def committed(async_session_factory):
    suffix = uuid4().hex[:10]
    domain = f"race-{suffix}.example"
    async with async_session_factory() as session:
        organization = Organization(name=f"Race {suffix}", created_by="test")
        other = Organization(name=f"Elsewhere {suffix}", created_by="test")
        session.add_all([organization, other])
        await session.flush()
        connection = IdentityProvider(
            organization_id=organization.id,
            name="Race Entra",
            source_type="scim",
            vendor="entra",
            created_by="test",
        )
        role = Role(name=f"Race Tech {suffix}", created_by="test")
        session.add_all(
            [
                connection,
                role,
                OrganizationDomain(
                    organization_id=organization.id,
                    domain=domain,
                    verification_token="t",
                    verified_at=datetime.now(timezone.utc),
                    created_by="test",
                ),
            ]
        )
        await session.commit()
    data = {
        "domain": domain,
        "organization_id": organization.id,
        "other_id": other.id,
        "connection_id": connection.id,
        "role_id": role.id,
    }
    yield data
    from src.models.orm.audit import AuditLog

    organization_ids = [organization.id, other.id]
    async with async_session_factory() as session:
        await session.execute(delete(User).where(User.email.like(f"%@{domain}")))
        await session.execute(delete(Role).where(Role.id == role.id))
        await session.execute(delete(AuditLog).where(AuditLog.organization_id.in_(organization_ids)))
        await session.execute(delete(Organization).where(Organization.id.in_(organization_ids)))
        await session.commit()


async def _waits(task: asyncio.Task) -> bool:
    await asyncio.sleep(_STILL_WAITING)
    return not task.done()


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True])
async def test_an_admin_and_a_directory_deactivating_one_user_dont_deadlock(
    async_session_factory, committed, bulk
):
    from shared.account_guards import refuse_last_active_platform_admin_removal
    from shared.sdk_users import bulk_update_users, update_user
    from src.models.contracts.users import BulkUserOperation

    async with async_session_factory() as session:
        user = User(email=f"leaver@{committed['domain']}", organization_id=committed["organization_id"])
        session.add(user)
        await session.commit()
        user_id = user.id

    async def deactivate() -> None:
        async with async_session_factory() as session:
            if bulk:
                await bulk_update_users(
                    session,
                    admin_caller(),
                    BulkUserOperation(user_ids=[user_id], operation="set_active", is_active=False),
                )
            else:
                await update_user(
                    session,
                    admin_caller(),
                    user_id=str(user_id),
                    fields={"is_active"},
                    is_active=False,
                )
            await session.commit()

    async with asyncio.timeout(30):
        async with async_session_factory() as directory:
            # What a directory deactivation does: lock the user, then run
            # the last-admin guard, which locks Platform Admin for update.
            await directory.execute(select(User.id).where(User.id == user_id).with_for_update())
            admin = asyncio.create_task(deactivate())
            assert await _waits(admin)
            # Had the admin taken the role lock before the user, this would
            # close a cycle and PostgreSQL would abort one side.
            await refuse_last_active_platform_admin_removal(directory, {user_id})
            await directory.commit()
        await admin


async def _move(session, user_id, organization_id, *, bulk: bool) -> bool:
    """Move the user as an administrator would; whether it was allowed."""
    from shared.sdk_users import UserServiceError, bulk_update_users, update_user
    from src.models.contracts.users import BulkUserOperation

    if bulk:
        result = await bulk_update_users(
            session,
            admin_caller(),
            BulkUserOperation(
                user_ids=[user_id], operation="move_org", organization_id=organization_id
            ),
        )
        return not result.failed
    try:
        await update_user(
            session,
            admin_caller(),
            user_id=str(user_id),
            fields={"organization_id"},
            organization_id=organization_id,
        )
    except UserServiceError as exc:
        assert exc.status_code == 409
        return False
    return True


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True])
async def test_a_move_waits_for_an_adoption_and_is_refused(
    async_session_factory, committed, bulk
):
    email = f"mover@{committed['domain']}"
    async with async_session_factory() as session:
        user = User(email=email, organization_id=committed["organization_id"])
        session.add(user)
        await session.commit()
        user_id = user.id

    async def move() -> bool:
        async with async_session_factory() as session:
            allowed = await _move(session, user_id, committed["other_id"], bulk=bulk)
            await session.commit()
            return allowed

    async with asyncio.timeout(30):
        async with async_session_factory() as adopter:
            connection = await adopter.get(IdentityProvider, committed["connection_id"])
            assert connection is not None
            # Adoption holds the user's row lock with the link not yet committed.
            await Provisioner(adopter, connection).create_user(_record(email))
            moving = asyncio.create_task(move())
            assert await _waits(moving)
            await adopter.commit()
        assert await moving is False

    async with async_session_factory() as session:
        moved = await session.get(User, user_id)
        assert moved is not None and moved.organization_id == committed["organization_id"]


@pytest.mark.asyncio
async def test_a_grant_waits_for_a_permission_edit_and_refuses_the_new_privilege(
    async_session_factory, committed
):
    from shared.account_guards import _lock_roles

    async with async_session_factory() as session:
        connection = await session.get(IdentityProvider, committed["connection_id"])
        assert connection is not None
        provisioner = Provisioner(session, connection)
        group = await provisioner.create_group(
            GroupRecord(display_name="Techs", external_id="g-race", attributes={}), []
        )
        session.add(IdentityProviderGroupMapping(group_id=group.id, created_by="test"))
        await session.flush()
        session.add(IdentityProviderGroupRole(group_id=group.id, role_id=committed["role_id"]))
        await session.flush()
        link = await provisioner.create_user(_record(f"hire@{committed['domain']}"))
        await provisioner.set_group_members(group, [link.id])
        await session.commit()
        link_id, user_id = link.id, link.user_id

    async def apply_grant() -> None:
        async with async_session_factory() as session:
            connection = await session.get(IdentityProvider, committed["connection_id"])
            assert connection is not None
            await Provisioner(session, connection).apply_pending([link_id])
            await session.commit()

    async with asyncio.timeout(30):
        async with async_session_factory() as editor:
            # What a permission edit does: lock the role, then change it.
            await _lock_roles(editor, {committed["role_id"]})
            editor.add(RolePermission(role_id=committed["role_id"], permission="users.readwrite"))
            await editor.flush()
            grant = asyncio.create_task(apply_grant())
            assert await _waits(grant)
            await editor.commit()
        await grant

    async with async_session_factory() as session:
        assert (
            await session.scalar(
                select(UserRole.role_id).where(
                    UserRole.user_id == user_id, UserRole.role_id == committed["role_id"]
                )
            )
            is None
        )


@pytest.mark.asyncio
async def test_concurrent_rotations_leave_one_full_lifetime_token(
    async_session_factory, committed
):
    from src.services.identity_providers import rotate_token

    async def rotate(session):
        with patch(
            "src.services.identity_providers.clear_expiry_notifications", AsyncMock()
        ):
            return await rotate_token(session, admin_caller(), committed["connection_id"], 365)

    async def second_rotation():
        async with async_session_factory() as session:
            issued = await rotate(session)
            await session.commit()
            return issued

    async with asyncio.timeout(30):
        async with async_session_factory() as session:
            first = await rotate(session)
            second_task = asyncio.create_task(second_rotation())
            assert await _waits(second_task)
            await session.commit()
        second = await second_task

    async with async_session_factory() as session:
        tokens = {
            token.token_hint: token
            for token in await session.scalars(
                select(IdentityProviderToken).where(
                    IdentityProviderToken.identity_provider_id == committed["connection_id"]
                )
            )
        }
    # The second rotation shortened the first's token and reported it.
    shortened = tokens[first.info.hint].expires_at
    assert shortened < first.info.expires_at
    assert second.previous_tokens_expire_at == shortened
    assert tokens[second.info.hint].expires_at == second.info.expires_at


async def _pending_hires(async_session_factory, committed, count: int) -> list:
    """Linked users waiting for the mapped role, on a connection that
    provisions External users."""
    async with async_session_factory() as session:
        connection = await session.get(IdentityProvider, committed["connection_id"])
        assert connection is not None
        connection.provision_as_external = True
        provisioner = Provisioner(session, connection)
        group = await provisioner.create_group(
            GroupRecord(display_name="Portal", external_id="g-portal", attributes={}), []
        )
        session.add(IdentityProviderGroupMapping(group_id=group.id, created_by="test"))
        await session.flush()
        session.add(IdentityProviderGroupRole(group_id=group.id, role_id=committed["role_id"]))
        await session.flush()
        links = [
            await provisioner.create_user(_record(f"portal{i}@{committed['domain']}"))
            for i in range(count)
        ]
        await provisioner.set_group_members(group, [link.id for link in links])
        await session.commit()
        return [link.id for link in links]


@pytest.mark.asyncio
async def test_grants_of_one_role_to_external_users_dont_wait_for_each_other(
    async_session_factory, committed
):
    first, second = await _pending_hires(async_session_factory, committed, 2)

    async def apply(link_id) -> None:
        async with async_session_factory() as session:
            connection = await session.get(IdentityProvider, committed["connection_id"])
            assert connection is not None
            await Provisioner(session, connection).apply_pending([link_id])
            await session.commit()

    async with asyncio.timeout(30):
        async with async_session_factory() as session:
            connection = await session.get(IdentityProvider, committed["connection_id"])
            assert connection is not None
            await Provisioner(session, connection).apply_pending([first])
            # Holding that grant open, a second grant of the same role goes
            # through: both only share-lock the role, so neither can be left
            # waiting to upgrade a lock the other shares.
            await asyncio.wait_for(apply(second), timeout=_STILL_WAITING * 4)
            await session.commit()


@pytest.mark.asyncio
async def test_a_mapping_waits_for_a_permission_edit_and_refuses_the_new_privilege(
    async_session_factory, committed
):
    from fastapi import HTTPException

    from shared.account_guards import _lock_roles
    from src.services.identity_providers import set_group_mapping

    async with async_session_factory() as session:
        connection = await session.get(IdentityProvider, committed["connection_id"])
        assert connection is not None
        group = await Provisioner(session, connection).create_group(
            GroupRecord(display_name="Admins", external_id="g-admins", attributes={}), []
        )
        await session.commit()
        group_id = group.id

    async def map_group() -> None:
        async with async_session_factory() as session:
            await set_group_mapping(
                session, admin_caller(), committed["connection_id"], group_id, [committed["role_id"]]
            )
            await session.commit()

    async with asyncio.timeout(30):
        async with async_session_factory() as editor:
            await _lock_roles(editor, {committed["role_id"]})
            editor.add(RolePermission(role_id=committed["role_id"], permission="users.readwrite"))
            await editor.flush()
            mapping = asyncio.create_task(map_group())
            assert await _waits(mapping)
            await editor.commit()
        with pytest.raises(HTTPException) as raised:
            await mapping

    assert raised.value.status_code == 422
