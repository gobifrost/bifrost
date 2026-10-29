"""R3a identity rules in the services: per-field user updates, bulk per-user
outcomes, the base-role writer, role assignments with boundaries and named
privileged access, and role permission editing."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select, update

import shared.account_guards as account_guards
from shared.builtin_roles import (
    DECRYPTION_ROLE_ID,
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_ROLE_ID,
    USER_BASE_PERMISSIONS,
    USER_ROLE_ID,
)
from src.core.constants import PROVIDER_ORG_ID
from src.core.principal import UserPrincipal
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import Caller
from src.services.user_role_assignments import RoleAssignmentError
from tests.helpers.authorization import admin_caller

pytestmark = pytest.mark.asyncio


def _delegate(
    *grants: tuple[set[str], tuple[Boundary, ...]],
    home: UUID = PROVIDER_ORG_ID,
    user_id: UUID | None = None,
) -> Caller:
    user_id = user_id or uuid4()
    ctx = AuthorizationContext(
        user_id=user_id,
        home_organization_id=home,
        base_role_id=USER_ROLE_ID,
        is_external=False,
        base_permissions=USER_BASE_PERMISSIONS,
        role_grants=tuple(RoleGrant(uuid4(), frozenset(p), b) for p, b in grants),
    )
    return Caller(
        UserPrincipal(user_id=user_id, email="delegate@example.com", organization_id=home),
        ctx,
    )


def _at(org: UUID) -> tuple[Boundary, ...]:
    return (Boundary(BoundaryKind.ORGANIZATION, org),)


async def _org(db_session):
    from src.models.orm.organizations import Organization

    org = Organization(name=f"ia-{uuid4().hex[:8]}", is_active=True, created_by="t")
    db_session.add(org)
    await db_session.flush()
    return org


async def _user(
    db_session, org_id: UUID | None, *, admin: bool = False, external: bool = False
):
    from shared.sdk_users import set_platform_admin
    from src.models import User as UserORM

    # A Global user is inserted as a superuser (ck_users_org_requires_superuser);
    # the assignment follows from the writer.
    user = UserORM(
        email=f"ia-{uuid4().hex[:8]}@example.com",
        organization_id=org_id,
        name="Before",
        is_superuser=admin,
        is_external=external,
    )
    db_session.add(user)
    await db_session.flush()
    await set_platform_admin(db_session, user, admin, assigned_by="t")
    return user


async def _role(db_session, *permissions: str):
    from src.models import Role, RolePermission

    role = Role(name=f"ia-role-{uuid4().hex[:8]}", created_by="t")
    db_session.add(role)
    await db_session.flush()
    for permission in permissions:
        db_session.add(RolePermission(role_id=role.id, permission=permission))
    await db_session.flush()
    return role


async def _boundaries(db_session, user_id: UUID, role_id: UUID) -> set[tuple[str, UUID | None]]:
    from src.models.orm.users import UserRoleBoundary

    rows = (
        await db_session.execute(
            select(UserRoleBoundary.kind, UserRoleBoundary.organization_id).where(
                UserRoleBoundary.user_id == user_id, UserRoleBoundary.role_id == role_id
            )
        )
    ).all()
    return {(kind, org) for kind, org in rows}


class TestUpdateFieldClassification:
    async def test_every_update_field_is_classified(self) -> None:
        from shared.sdk_users import UPDATE_FIELD_PERMISSIONS
        from src.models import UserUpdate

        assert set(UPDATE_FIELD_PERMISSIONS) == set(UserUpdate.model_fields)

    async def test_supplied_fields_are_classified_explicit_null_included(self) -> None:
        from shared.sdk_users import update_field_permissions
        from src.models import UserUpdate

        request = UserUpdate.model_validate({"name": "x", "email": None, "is_superuser": False})
        assert update_field_permissions(set(request.model_fields_set)) == {
            "name": "users.readwrite",
            "email": "userlifecycle.readwrite",
            "is_superuser": "privilegedaccess.readwrite",
        }

    async def test_an_empty_update_still_needs_user_support(self) -> None:
        from shared.sdk_users import update_field_permissions

        assert set(update_field_permissions(set()).values()) == {"users.readwrite"}


class TestUpdateUser:
    async def _update(self, db_session, caller, user, **fields):
        from shared.sdk_users import update_user

        return await update_user(
            db_session, caller, user_id=str(user.id), fields=set(fields), **fields
        )

    async def test_support_at_the_users_org(self, db_session) -> None:
        org = await _org(db_session)
        user = await _user(db_session, org.id)
        support = _delegate(({"users.readwrite"}, _at(org.id)))
        updated = await self._update(db_session, support, user, name="After", mfa_enabled=False)
        assert updated.name == "After"

    async def test_support_does_not_reach_lifecycle_fields(self, db_session) -> None:
        org = await _org(db_session)
        user = await _user(db_session, org.id)
        support = _delegate(({"users.readwrite"}, _at(org.id)))
        for fields in ({"email": "new@example.com"}, {"is_external": True}, {"email": None}):
            with pytest.raises(HTTPException) as exc_info:
                await self._update(db_session, support, user, **fields)
            assert exc_info.value.status_code == 403, fields
        lifecycle = _delegate(({"users.readwrite", "userlifecycle.readwrite"}, _at(org.id)))
        with pytest.raises(HTTPException) as exc_info:
            await self._update(db_session, lifecycle, user, is_superuser=False)
        assert exc_info.value.status_code == 403

    async def test_a_move_needs_the_source_and_the_destination(self, db_session) -> None:
        source, destination = await _org(db_session), await _org(db_session)
        user = await _user(db_session, source.id)
        lifecycle = {"userlifecycle.readwrite"}
        only_source = _delegate((lifecycle, _at(source.id)))
        with pytest.raises(HTTPException):
            await self._update(db_session, only_source, user, organization_id=destination.id)
        both = _delegate((lifecycle, _at(source.id) + _at(destination.id)))
        moved = await self._update(db_session, both, user, organization_id=destination.id)
        assert moved.organization_id == destination.id

    async def test_a_platform_operator_cannot_be_moved_out_of_the_provider_org(self, db_session) -> None:
        from shared.sdk_users import OPERATOR_MOVE_MESSAGE, UserServiceError
        from src.models import UserRole

        destination = await _org(db_session)
        operator = await _user(db_session, PROVIDER_ORG_ID)
        db_session.add(UserRole(user_id=operator.id, role_id=PLATFORM_OPERATOR_ROLE_ID, assigned_by="t"))
        await db_session.flush()

        with pytest.raises(UserServiceError) as exc_info:
            await self._update(db_session, admin_caller(), operator, organization_id=destination.id)
        assert (exc_info.value.status_code, exc_info.value.detail) == (409, OPERATOR_MOVE_MESSAGE)
        assert operator.organization_id == PROVIDER_ORG_ID

        updated = await self._update(db_session, admin_caller(), operator, organization_id=PROVIDER_ORG_ID)
        assert updated.organization_id == PROVIDER_ORG_ID

        await db_session.execute(
            UserRole.__table__.delete().where(UserRole.user_id == operator.id)
        )
        moved = await self._update(db_session, admin_caller(), operator, organization_id=destination.id)
        assert moved.organization_id == destination.id

    async def test_a_privileged_user_needs_a_platform_admin(self, db_session) -> None:
        org = await _org(db_session)
        target = await _user(db_session, org.id)
        privileged = await _role(db_session, "configs.readwrite")
        from src.models import UserRole

        db_session.add(UserRole(user_id=target.id, role_id=privileged.id, assigned_by="t"))
        await db_session.flush()
        support = _delegate(({"users.readwrite"}, _at(org.id)))
        with pytest.raises(HTTPException) as exc_info:
            await self._update(db_session, support, target, name="x")
        assert exc_info.value.status_code == 403
        updated = await self._update(db_session, admin_caller(), target, name="Admin did it")
        assert updated.is_protected is True

    async def test_demoting_keeps_a_custom_base_role(self, db_session) -> None:
        org = await _org(db_session)
        custom = await _role(db_session)
        user = await _user(db_session, org.id, admin=True)
        await _user(db_session, PROVIDER_ORG_ID, admin=True)
        user.base_role_id = custom.id
        await db_session.flush()
        updated = await self._update(db_session, admin_caller(), user, is_superuser=False)
        assert updated.is_superuser is False
        assert user.base_role_id == custom.id
        assert await _boundaries(db_session, user.id, PLATFORM_ADMIN_ROLE_ID) == set()

    async def test_demoting_a_global_admin_needs_an_organization_first(self, db_session) -> None:
        from shared.sdk_users import UserServiceError

        user = await _user(db_session, None, admin=True)
        with pytest.raises(UserServiceError) as exc_info:
            await self._update(db_session, admin_caller(), user, is_superuser=False)
        assert exc_info.value.status_code == 409
        assert user.is_superuser is True

    async def test_promoting_adds_platform_admin_and_keeps_the_base_role(self, db_session) -> None:
        user = await _user(db_session, PROVIDER_ORG_ID)
        updated = await self._update(db_session, admin_caller(), user, is_superuser=True)
        assert updated.is_superuser is True
        assert user.base_role_id == USER_ROLE_ID
        assert await _boundaries(db_session, user.id, PLATFORM_ADMIN_ROLE_ID) == {("platform", None)}

    async def test_last_active_platform_admin_cannot_be_demoted_or_deactivated(self, db_session) -> None:
        from shared.sdk_users import UserServiceError

        admin = await _user(db_session, PROVIDER_ORG_ID, admin=True)
        for fields in ({"is_superuser": False}, {"is_active": False}):
            with pytest.raises(UserServiceError) as exc_info:
                await self._update(db_session, admin_caller(), admin, **fields)
            assert exc_info.value.status_code == 409
            assert "active person" in exc_info.value.detail

    async def test_external_user_cannot_become_privileged(self, db_session) -> None:
        from shared.sdk_users import UserServiceError
        from src.models import UserRole

        org = await _org(db_session)
        user = await _user(db_session, org.id)
        role = await _role(db_session, "configs.readwrite")
        db_session.add(UserRole(user_id=user.id, role_id=role.id, assigned_by="test"))
        await db_session.flush()

        with pytest.raises(UserServiceError) as exc_info:
            await self._update(db_session, admin_caller(), user, is_external=True)
        assert exc_info.value.status_code == 409
        assert "External users" in exc_info.value.detail

    async def test_user_cannot_become_external_and_platform_admin_in_one_update(self, db_session) -> None:
        from shared.sdk_users import UserServiceError

        user = await _user(db_session, PROVIDER_ORG_ID)
        with pytest.raises(UserServiceError) as exc_info:
            await self._update(
                db_session,
                admin_caller(),
                user,
                is_external=True,
                is_superuser=True,
            )
        assert exc_info.value.status_code == 409
        assert user.is_external is False
        assert user.is_superuser is False

    async def test_concurrent_deactivation_and_role_removal_keep_an_active_platform_admin(
        self, db_session, async_session_factory
    ) -> None:
        from src.models import User as UserORM
        from src.models import UserRole

        first = await _user(db_session, PROVIDER_ORG_ID, admin=True)
        second = await _user(db_session, PROVIDER_ORG_ID, admin=True)
        await db_session.commit()

        try:
            async with async_session_factory() as deactivate_session, async_session_factory() as remove_session:
                await account_guards.refuse_last_active_platform_admin_removal(deactivate_session, {first.id})
                await deactivate_session.execute(
                    update(UserORM).where(UserORM.id == first.id).values(is_active=False)
                )
                await deactivate_session.flush()

                async def remove_second_admin() -> str | None:
                    try:
                        await account_guards.refuse_last_active_platform_admin_removal(remove_session, {second.id})
                    except account_guards.AccountGuardError as exc:
                        await remove_session.rollback()
                        return exc.detail
                    await remove_session.execute(
                        delete(UserRole).where(
                            UserRole.user_id == second.id,
                            UserRole.role_id == PLATFORM_ADMIN_ROLE_ID,
                        )
                    )
                    await remove_session.commit()
                    return None

                removal = asyncio.create_task(remove_second_admin())
                await asyncio.sleep(0)
                assert not removal.done()
                await deactivate_session.commit()
                assert await removal == "At least one active person must hold the Platform Admin role"
        finally:
            async with async_session_factory() as cleanup:
                await cleanup.execute(delete(UserRole).where(UserRole.user_id.in_([first.id, second.id])))
                await cleanup.execute(delete(UserORM).where(UserORM.id.in_([first.id, second.id])))
                await cleanup.commit()


class TestBulk:
    async def test_per_user_outcomes(self, db_session) -> None:
        from shared.sdk_users import bulk_update_users
        from src.models import BulkUserOperation

        org_a, org_b = await _org(db_session), await _org(db_session)
        ordinary = await _user(db_session, org_a.id)
        elsewhere = await _user(db_session, org_b.id)
        admin = await _user(db_session, org_a.id, admin=True)
        support = _delegate(({"users.readwrite"}, _at(org_a.id)))
        missing = uuid4()
        result = await bulk_update_users(
            db_session,
            support,
            BulkUserOperation(
                user_ids=[ordinary.id, elsewhere.id, admin.id, missing],
                operation="set_active",
                is_active=False,
            ),
        )
        assert result.succeeded == [ordinary.id]
        reasons = {f.user_id: f.reason for f in result.failed}
        assert reasons[elsewhere.id] == "You don't have permission to manage users"
        assert reasons[admin.id] == "You need Read and Write Privileged Access to change this account"
        assert reasons[missing] == "User not found"
        assert ordinary.is_active is False and elsewhere.is_active is True

    async def test_move_org_refuses_a_platform_operator_leaving_the_provider_org(self, db_session) -> None:
        from shared.sdk_users import OPERATOR_MOVE_MESSAGE, bulk_update_users
        from src.models import BulkUserOperation, UserRole

        destination = await _org(db_session)
        operator = await _user(db_session, PROVIDER_ORG_ID)
        ordinary = await _user(db_session, PROVIDER_ORG_ID)
        db_session.add(UserRole(user_id=operator.id, role_id=PLATFORM_OPERATOR_ROLE_ID, assigned_by="t"))
        await db_session.flush()

        result = await bulk_update_users(
            db_session,
            admin_caller(),
            BulkUserOperation(
                user_ids=[operator.id, ordinary.id], operation="move_org", organization_id=destination.id
            ),
        )
        assert result.succeeded == [ordinary.id]
        assert [(f.user_id, f.reason) for f in result.failed] == [(operator.id, OPERATOR_MOVE_MESSAGE)]
        assert operator.organization_id == PROVIDER_ORG_ID
        assert ordinary.organization_id == destination.id

        result = await bulk_update_users(
            db_session,
            admin_caller(),
            BulkUserOperation(user_ids=[operator.id], operation="move_org", organization_id=None),
        )
        assert [f.reason for f in result.failed] == [OPERATOR_MOVE_MESSAGE]

    async def test_last_active_platform_admin_cannot_be_bulk_deactivated(self, db_session) -> None:
        from shared.sdk_users import UserServiceError, bulk_update_users
        from src.models import BulkUserOperation

        admin = await _user(db_session, PROVIDER_ORG_ID, admin=True)
        with pytest.raises(UserServiceError) as exc_info:
            await bulk_update_users(
                db_session,
                admin_caller(),
                BulkUserOperation(user_ids=[admin.id], operation="set_active", is_active=False),
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "At least one active person must hold the Platform Admin role"

    async def test_bulk_role_grant_checks_the_current_external_flag(self, db_session) -> None:
        from shared.sdk_users import bulk_update_users
        from src.models import BulkUserOperation, User as UserORM

        user = await _user(db_session, PROVIDER_ORG_ID)
        role = await _role(db_session, "configs.readwrite")
        # Preserve the loaded account snapshot while the database reflects an
        # external transition before the role writer takes its lock.
        await db_session.execute(
            update(UserORM).where(UserORM.id == user.id).values(is_external=True)
            .execution_options(synchronize_session=False)
        )
        assert user.is_external is False
        result = await bulk_update_users(
            db_session, admin_caller(),
            BulkUserOperation(user_ids=[user.id], operation="replace_roles", role_ids=[role.id]),
        )
        assert result.succeeded == []
        assert [failure.reason for failure in result.failed] == [
            "External users cannot hold privileged roles"
        ]
        assert await _boundaries(db_session, user.id, role.id) == set()

    async def test_no_reach_is_refused_outright(self, db_session) -> None:
        from shared.sdk_users import bulk_update_users
        from src.models import BulkUserOperation

        org = await _org(db_session)
        user = await _user(db_session, org.id)
        support = _delegate(({"users.readwrite"}, _at(org.id)))
        with pytest.raises(HTTPException) as exc_info:
            await bulk_update_users(
                db_session,
                support,
                BulkUserOperation(user_ids=[user.id], operation="move_org", organization_id=org.id),
            )
        assert exc_info.value.status_code == 403

    async def test_replace_roles_keeps_boundaries_and_applies_the_ceiling(self, db_session) -> None:
        from shared.sdk_users import bulk_update_users
        from src.models import BulkUserOperation

        org = await _org(db_session)
        user = await _user(db_session, org.id)
        plain, kept, with_permission = (
            await _role(db_session),
            await _role(db_session),
            await _role(db_session, "forms.read"),
        )
        assigner = _delegate(({"roleassignments.readwrite"}, _at(org.id)))

        result = await bulk_update_users(
            db_session,
            assigner,
            BulkUserOperation(user_ids=[user.id], operation="replace_roles", role_ids=[kept.id]),
        )
        assert result.succeeded == [user.id]
        assert await _boundaries(db_session, user.id, kept.id) == {("organization", org.id)}

        result = await bulk_update_users(
            db_session,
            assigner,
            BulkUserOperation(
                user_ids=[user.id], operation="replace_roles", role_ids=[kept.id, plain.id, with_permission.id]
            ),
        )
        assert result.succeeded == [user.id]
        for role in (kept, plain, with_permission):
            assert await _boundaries(db_session, user.id, role.id) == {("organization", org.id)}

        result = await bulk_update_users(
            db_session,
            admin_caller(),
            BulkUserOperation(
                user_ids=[user.id], operation="replace_roles", role_ids=[kept.id, PLATFORM_OPERATOR_ROLE_ID]
            ),
        )
        assert "Built-in roles" in result.failed[0].reason


class TestBaseRoleWriter:
    async def test_custom_role_is_a_valid_base_and_leaves_is_superuser_alone(self, db_session) -> None:
        from shared.sdk_users import set_user_base_role

        org = await _org(db_session)
        user = await _user(db_session, org.id, admin=True)
        custom = await _role(db_session)
        await set_user_base_role(db_session, user, custom.id)
        assert (user.base_role_id, user.is_superuser) == (custom.id, True)
        await set_user_base_role(db_session, user, USER_ROLE_ID)
        assert (user.base_role_id, user.is_superuser) == (USER_ROLE_ID, True)

    async def test_admin_operator_and_secrets_reader_are_never_base(self, db_session) -> None:
        from shared.sdk_users import set_user_base_role

        user = await _user(db_session, (await _org(db_session)).id)
        for role_id in (PLATFORM_ADMIN_ROLE_ID, PLATFORM_OPERATOR_ROLE_ID, DECRYPTION_ROLE_ID, uuid4()):
            with pytest.raises(ValueError):
                await set_user_base_role(db_session, user, role_id)


class TestRoleDelete:
    async def test_a_base_role_in_use_cannot_be_deleted(self, db_session) -> None:
        from shared.sdk_roles import RoleServiceError, delete_role

        custom = await _role(db_session)
        user = await _user(db_session, (await _org(db_session)).id)
        user.base_role_id = custom.id
        await db_session.flush()
        with pytest.raises(RoleServiceError) as exc_info:
            await delete_role(db_session, role_id=custom.id)
        assert exc_info.value.status_code == 409
        assert "base role of 1 user" in exc_info.value.detail


class TestRolePermissions:
    async def test_ordinary_caller_cannot_add_privileged_identity_permission(self, db_session) -> None:
        from src.services.role_permissions import RolePermissionError, replace_identity_permissions

        role = await _role(db_session)
        with pytest.raises(RolePermissionError) as exc_info:
            await replace_identity_permissions(
                db_session,
                caller=_delegate(),
                role_id=role.id,
                permissions=["users.impersonate"],
            )
        assert exc_info.value.status_code == 403

    async def test_org_bound_privileged_access_cannot_edit_privileged_role(self, db_session) -> None:
        from src.services.role_permissions import RolePermissionError, replace_identity_permissions

        role = await _role(db_session)
        with pytest.raises(RolePermissionError) as exc_info:
            await replace_identity_permissions(
                db_session,
                caller=_delegate(
                    (
                        {"privilegedaccess.readwrite"},
                        (Boundary(BoundaryKind.ORGANIZATION, uuid4()),),
                    ),
                ),
                role_id=role.id,
                permissions=["users.impersonate"],
            )
        assert exc_info.value.status_code == 403

    async def test_privileged_role_permissions_require_global_privileged_access(self, db_session) -> None:
        from src.services.role_permissions import RolePermissionError, get_role_permissions, replace_identity_permissions

        role = await _role(db_session, "users.impersonate")
        with pytest.raises(RolePermissionError) as exc_info:
            await replace_identity_permissions(
                db_session, caller=_delegate(), role_id=role.id, permissions=[]
            )
        assert exc_info.value.status_code == 403
        assert await get_role_permissions(db_session, role_id=role.id) == {"users.impersonate"}

        response = await replace_identity_permissions(
            db_session,
            caller=_delegate(
                ({"privilegedaccess.readwrite"}, (Boundary(BoundaryKind.PLATFORM),)),
            ),
            role_id=role.id,
            permissions=[],
        )
        assert response.permissions == []

    async def test_identity_edit_keeps_other_permissions(self, db_session) -> None:
        from src.services.role_permissions import get_role_permissions, replace_identity_permissions

        role = await _role(db_session, "agents.readwrite", "users.read")
        response = await replace_identity_permissions(
            db_session, caller=admin_caller(), role_id=role.id, permissions=["users.readwrite", "organizations.read"]
        )
        assert await get_role_permissions(db_session, role_id=role.id) == {
            "agents.readwrite",
            "users.readwrite",
            "organizations.read",
        }
        items = {item.permission: item for item in response.permissions}
        assert items["agents.readwrite"].editable is False
        assert items["users.readwrite"].editable and items["users.readwrite"].privileged
        assert not items["organizations.read"].privileged

    async def test_non_identity_permissions_are_refused(self, db_session) -> None:
        from src.services.role_permissions import RolePermissionError, replace_identity_permissions

        role = await _role(db_session)
        for permission in ("agents.readwrite", "secrets.read", "userlifecycle.read"):
            with pytest.raises(RolePermissionError) as exc_info:
                await replace_identity_permissions(db_session, caller=admin_caller(), role_id=role.id, permissions=[permission])
            assert exc_info.value.status_code == 422, permission

    async def test_builtin_roles_are_read_only(self, db_session) -> None:
        from src.services.role_permissions import (
            RolePermissionError,
            describe_role_permissions,
            replace_identity_permissions,
        )

        with pytest.raises(RolePermissionError) as exc_info:
            await replace_identity_permissions(db_session, caller=admin_caller(), role_id=PLATFORM_OPERATOR_ROLE_ID, permissions=[])
        assert exc_info.value.status_code == 409
        described = await describe_role_permissions(db_session, role_id=PLATFORM_OPERATOR_ROLE_ID)
        assert described.is_builtin
        assert not any(item.editable for item in described.permissions + described.identity_permissions)

    async def test_role_with_external_holders_cannot_gain_a_privileged_permission(self, db_session) -> None:
        from src.models import UserRole
        from src.services.role_permissions import RolePermissionError, replace_identity_permissions

        org = await _org(db_session)
        external = await _user(db_session, org.id, external=True)
        role = await _role(db_session)
        db_session.add(UserRole(user_id=external.id, role_id=role.id, assigned_by="test"))
        await db_session.flush()

        with pytest.raises(RolePermissionError) as exc_info:
            await replace_identity_permissions(
                db_session, caller=admin_caller(), role_id=role.id, permissions=["users.readwrite"]
            )
        assert exc_info.value.status_code == 409
        assert "External users" in exc_info.value.detail

    async def test_role_with_external_base_holder_cannot_gain_a_privileged_permission(self, db_session) -> None:
        from shared.sdk_users import set_user_base_role
        from src.services.role_permissions import RolePermissionError, replace_identity_permissions

        org = await _org(db_session)
        external = await _user(db_session, org.id, external=True)
        role = await _role(db_session)
        await set_user_base_role(db_session, external, role.id)

        with pytest.raises(RolePermissionError) as exc_info:
            await replace_identity_permissions(
                db_session, caller=admin_caller(), role_id=role.id, permissions=["users.readwrite"]
            )
        assert exc_info.value.status_code == 409

    async def test_permission_elevation_rechecks_an_external_transition_that_commits_while_waiting_for_the_role_lock(
        self, db_session, async_session_factory, monkeypatch
    ) -> None:
        from shared.sdk_users import set_user_base_role
        from src.models import Role as RoleORM
        from src.models import RolePermission, UserRole
        from src.models import User as UserORM
        from src.models.orm.organizations import Organization
        from src.services.role_permissions import RolePermissionError, set_role_permissions

        org = await _org(db_session)
        user = await _user(db_session, org.id)
        role = await _role(db_session)
        await set_user_base_role(db_session, user, role.id)
        await db_session.commit()

        try:
            async with async_session_factory() as external_session, async_session_factory() as permission_session:
                await account_guards.assert_user_can_be_external(external_session, user.id)
                await external_session.execute(
                    update(UserORM).where(UserORM.id == user.id).values(is_external=True)
                )
                await external_session.flush()

                lock_attempted = asyncio.Event()
                original_lock = account_guards._lock_roles

                async def observe_lock_attempt(session, role_ids):
                    if session is permission_session:
                        lock_attempted.set()
                    await original_lock(session, role_ids)

                monkeypatch.setattr(account_guards, "_lock_roles", observe_lock_attempt)
                elevation = asyncio.create_task(
                    set_role_permissions(
                        permission_session,
                        role_id=role.id,
                        permissions=frozenset({"users.readwrite"}),
                    )
                )
                await lock_attempted.wait()
                assert not elevation.done()
                await external_session.commit()

                with pytest.raises(RolePermissionError) as exc_info:
                    await asyncio.gather(elevation)
                assert exc_info.value.status_code == 409
        finally:
            async with async_session_factory() as cleanup:
                await cleanup.execute(delete(UserRole).where(UserRole.user_id == user.id))
                await cleanup.execute(delete(UserORM).where(UserORM.id == user.id))
                await cleanup.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
                await cleanup.execute(delete(RoleORM).where(RoleORM.id == role.id))
                await cleanup.execute(delete(Organization).where(Organization.id == org.id))
                await cleanup.commit()

    async def test_permission_elevation_rechecks_an_external_role_assignment_that_commits_while_waiting_for_the_role_lock(
        self, db_session, async_session_factory, monkeypatch
    ) -> None:
        from src.models import Role as RoleORM
        from src.models import RolePermission, UserRole
        from src.models import User as UserORM
        from src.models.orm.organizations import Organization
        from src.services.role_permissions import RolePermissionError, set_role_permissions

        org = await _org(db_session)
        user = await _user(db_session, org.id, external=True)
        role = await _role(db_session)
        await db_session.commit()

        try:
            async with async_session_factory() as assignment_session, async_session_factory() as permission_session:
                await account_guards.assert_role_assignment_allowed_for_user(
                    assignment_session, user_id=user.id, role_id=role.id
                )
                assignment_session.add(
                    UserRole(user_id=user.id, role_id=role.id, assigned_by="test")
                )
                await assignment_session.flush()

                lock_attempted = asyncio.Event()
                original_lock = account_guards._lock_roles

                async def observe_lock_attempt(session, role_ids):
                    if session is permission_session:
                        lock_attempted.set()
                    await original_lock(session, role_ids)

                monkeypatch.setattr(account_guards, "_lock_roles", observe_lock_attempt)
                elevation = asyncio.create_task(
                    set_role_permissions(
                        permission_session,
                        role_id=role.id,
                        permissions=frozenset({"users.readwrite"}),
                    )
                )
                await lock_attempted.wait()
                assert not elevation.done()
                await assignment_session.commit()

                with pytest.raises(RolePermissionError) as exc_info:
                    await asyncio.gather(elevation)
                assert exc_info.value.status_code == 409
        finally:
            async with async_session_factory() as cleanup:
                await cleanup.execute(delete(UserRole).where(UserRole.user_id == user.id))
                await cleanup.execute(delete(UserORM).where(UserORM.id == user.id))
                await cleanup.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
                await cleanup.execute(delete(RoleORM).where(RoleORM.id == role.id))
                await cleanup.execute(delete(Organization).where(Organization.id == org.id))
                await cleanup.commit()


class TestRoleAssignments:
    async def _put(self, db_session, caller, user, base=None, additional=()):
        from src.models.contracts.role_assignments import UserRoleAssignmentsUpdate
        from src.services.user_role_assignments import replace_role_assignments

        return await replace_role_assignments(
            db_session,
            caller,
            user_id=user.id,
            request=UserRoleAssignmentsUpdate.model_validate(
                {"base_role_id": base or user.base_role_id, "additional": list(additional)}
            ),
        )

    async def test_admin_assigns_operator_at_managed_organizations(self, db_session) -> None:
        user = await _user(db_session, PROVIDER_ORG_ID)
        response = await self._put(
            db_session,
            admin_caller(),
            user,
            additional=[{"role_id": PLATFORM_OPERATOR_ROLE_ID, "boundaries": [{"kind": "managed_organizations"}]}],
        )
        assert [a.role_id for a in response.additional] == [PLATFORM_OPERATOR_ROLE_ID]
        assert response.is_protected is True
        assert await _boundaries(db_session, user.id, PLATFORM_OPERATOR_ROLE_ID) == {
            ("managed_organizations", None)
        }

    async def test_external_user_cannot_receive_a_privileged_role(self, db_session) -> None:
        from src.services.user_role_assignments import RoleAssignmentError

        external = await _user(db_session, PROVIDER_ORG_ID, external=True)
        privileged = await _role(db_session, "configs.readwrite")
        with pytest.raises(RoleAssignmentError) as exc_info:
            await self._put(
                db_session,
                admin_caller(),
                external,
                additional=[{"role_id": privileged.id}],
            )
        assert exc_info.value.status_code == 409
        assert "External users" in exc_info.value.detail

    async def test_assignable_roles_say_where_each_may_apply(self, db_session) -> None:
        from src.services.user_role_assignments import get_role_assignments

        org = await _org(db_session)
        user = await _user(db_session, org.id)
        provider_user = await _user(db_session, PROVIDER_ORG_ID)
        plain = await _role(db_session)

        as_admin = {
            r.id: r
            for r in (
                await get_role_assignments(db_session, admin_caller(), user_id=user.id)
            ).assignable_roles
        }
        operator = {
            r.id: r
            for r in (
                await get_role_assignments(db_session, admin_caller(), user_id=provider_user.id)
            ).assignable_roles
        }[PLATFORM_OPERATOR_ROLE_ID]
        assert operator.boundary_kinds == ["organization", "managed_organizations"]
        assert operator.provider_organization_allowed is False
        assert operator.description
        assert as_admin[plain.id].boundary_kinds == [
            "organization",
            "managed_organizations",
            "platform",
        ]
        assert as_admin[plain.id].provider_organization_allowed is True

        assigner = _delegate(({"roleassignments.read", "roleassignments.readwrite"}, _at(org.id)))
        as_delegate = {
            r.id: r
            for r in (
                await get_role_assignments(db_session, assigner, user_id=user.id)
            ).assignable_roles
        }
        assert as_delegate[plain.id].boundary_kinds == [
            "organization",
            "managed_organizations",
            "platform",
        ]
        assert as_delegate[plain.id].provider_organization_allowed is True

    async def test_operator_boundaries_and_base_are_limited(self, db_session) -> None:
        from src.services.user_role_assignments import RoleAssignmentError

        user = await _user(db_session, PROVIDER_ORG_ID)
        for boundaries in (
            [{"kind": "platform"}],
            [{"kind": "organization", "organization_id": str(PROVIDER_ORG_ID)}],
        ):
            with pytest.raises(RoleAssignmentError) as exc_info:
                await self._put(
                    db_session,
                    admin_caller(),
                    user,
                    additional=[{"role_id": PLATFORM_OPERATOR_ROLE_ID, "boundaries": boundaries}],
                )
            assert exc_info.value.status_code == 422
        with pytest.raises(RoleAssignmentError) as exc_info:
            await self._put(db_session, admin_caller(), user, base=PLATFORM_OPERATOR_ROLE_ID)
        assert exc_info.value.status_code == 422

    async def test_operator_is_only_for_provider_org_people(self, db_session) -> None:
        from src.services.user_role_assignments import (
            OPERATOR_HOLDER_MESSAGE,
            RoleAssignmentError,
            get_role_assignments,
        )

        customer = await _user(db_session, (await _org(db_session)).id)
        view = await get_role_assignments(db_session, admin_caller(), user_id=customer.id)
        assert PLATFORM_OPERATOR_ROLE_ID not in {r.id for r in view.assignable_roles}

        with pytest.raises(RoleAssignmentError) as exc_info:
            await self._put(
                db_session,
                admin_caller(),
                customer,
                additional=[
                    {"role_id": PLATFORM_OPERATOR_ROLE_ID, "boundaries": [{"kind": "managed_organizations"}]}
                ],
            )
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail == OPERATOR_HOLDER_MESSAGE

    async def test_an_operator_moved_out_of_the_provider_org_can_still_lose_it(self, db_session) -> None:
        from src.services.user_role_assignments import get_role_assignments

        user = await _user(db_session, PROVIDER_ORG_ID)
        await self._put(
            db_session,
            admin_caller(),
            user,
            additional=[{"role_id": PLATFORM_OPERATOR_ROLE_ID, "boundaries": [{"kind": "managed_organizations"}]}],
        )
        user.organization_id = (await _org(db_session)).id
        await db_session.flush()

        view = await get_role_assignments(db_session, admin_caller(), user_id=user.id)
        listed = {r.id: r for r in view.assignable_roles}[PLATFORM_OPERATOR_ROLE_ID]
        assert listed.can_be_additional is False

        response = await self._put(db_session, admin_caller(), user, additional=[])
        assert response.additional == []

    async def test_secrets_reader_is_assigned_with_its_fixed_boundaries(self, db_session) -> None:
        from src.services.user_role_assignments import SECRETS_READER_BOUNDARIES, get_role_assignments

        user = await _user(db_session, PROVIDER_ORG_ID, admin=True)
        view = await get_role_assignments(db_session, admin_caller(), user_id=user.id)
        listed = {r.id: r for r in view.assignable_roles}[DECRYPTION_ROLE_ID]
        assert listed.boundary_kinds == []
        assert {(b.kind, b.organization_id) for b in listed.fixed_boundaries} == {
            (b.kind.value, b.organization_id) for b in SECRETS_READER_BOUNDARIES
        }

        response = await self._put(
            db_session,
            admin_caller(),
            user,
            additional=[{"role_id": PLATFORM_ADMIN_ROLE_ID}, {"role_id": DECRYPTION_ROLE_ID}],
        )
        assert {a.role_id for a in response.additional} == {PLATFORM_ADMIN_ROLE_ID, DECRYPTION_ROLE_ID}
        assert await _boundaries(db_session, user.id, DECRYPTION_ROLE_ID) == {
            ("platform", None),
            ("managed_organizations", None),
            ("organization", PROVIDER_ORG_ID),
        }

        response = await self._put(
            db_session, admin_caller(), user, additional=[{"role_id": PLATFORM_ADMIN_ROLE_ID}]
        )
        assert [a.role_id for a in response.additional] == [PLATFORM_ADMIN_ROLE_ID]

    async def test_platform_admin_can_add_and_remove_own_secrets_reader(self, db_session) -> None:
        from src.models.orm.audit import AuditLog
        from src.services.audit_context import ActorContext, clear_actor, set_actor
        from src.services.user_role_assignments import authorization_summary

        user = await _user(db_session, PROVIDER_ORG_ID, admin=True)
        caller = admin_caller(user_id=user.id)
        assert not any(
            grant.permission == "secrets.read"
            for grant in (await authorization_summary(db_session, user.id)).grants
        )

        actor_token = set_actor(ActorContext(user_id=user.id, organization_id=PROVIDER_ORG_ID))
        try:
            added = await self._put(
                db_session,
                caller,
                user,
                additional=[{"role_id": PLATFORM_ADMIN_ROLE_ID}, {"role_id": DECRYPTION_ROLE_ID}],
            )
            assert {assignment.role_id for assignment in added.additional} == {
                PLATFORM_ADMIN_ROLE_ID,
                DECRYPTION_ROLE_ID,
            }
            assert await _boundaries(db_session, user.id, DECRYPTION_ROLE_ID) == {
                ("platform", None),
                ("managed_organizations", None),
                ("organization", PROVIDER_ORG_ID),
            }

            removed = await self._put(
                db_session,
                caller,
                user,
                additional=[{"role_id": PLATFORM_ADMIN_ROLE_ID}],
            )
        finally:
            clear_actor(actor_token)

        assert [assignment.role_id for assignment in removed.additional] == [PLATFORM_ADMIN_ROLE_ID]
        assert not any(
            grant.permission == "secrets.read"
            for grant in (await authorization_summary(db_session, user.id)).grants
        )
        events = (
            await db_session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "user.role_assignments_updated",
                    AuditLog.resource_id == user.id,
                )
            )
        ).all()
        assert len(events) == 2
        assert {event.user_id for event in events} == {user.id}
        added_event = next(
            event
            for event in events
            if str(DECRYPTION_ROLE_ID) in event.details["after"]["additional"]
        )
        removed_event = next(
            event
            for event in events
            if str(DECRYPTION_ROLE_ID) in event.details["before"]["additional"]
            and str(DECRYPTION_ROLE_ID) not in event.details["after"]["additional"]
        )
        assert added_event.details["after"]["additional"][str(DECRYPTION_ROLE_ID)] == [
            "managed_organizations",
            f"organization:{PROVIDER_ORG_ID}",
            "platform",
        ]
        assert str(DECRYPTION_ROLE_ID) not in removed_event.details["after"]["additional"]

    async def test_platform_admin_can_assign_themselves_an_ordinary_role(self, db_session) -> None:
        user = await _user(db_session, PROVIDER_ORG_ID, admin=True)
        role = await _role(db_session)

        response = await self._put(
            db_session,
            admin_caller(user_id=user.id),
            user,
            additional=[{"role_id": PLATFORM_ADMIN_ROLE_ID}, {"role_id": role.id}],
        )

        assert {assignment.role_id for assignment in response.additional} == {
            PLATFORM_ADMIN_ROLE_ID,
            role.id,
        }
        assert await _boundaries(db_session, user.id, role.id) == {
            ("organization", PROVIDER_ORG_ID)
        }

    async def test_authorization_summary_lists_an_admins_grants(self, db_session) -> None:
        from src.services.user_role_assignments import authorization_summary

        user = await _user(db_session, PROVIDER_ORG_ID, admin=True)
        summary = await authorization_summary(db_session, user.id)
        held = {(g.permission, g.boundary.kind) for g in summary.grants}
        assert summary.is_platform_admin is True
        assert ("*", "platform") in held
        assert not any(permission == "secrets.read" for permission, _ in held)

        await self._put(
            db_session,
            admin_caller(),
            user,
            additional=[{"role_id": PLATFORM_ADMIN_ROLE_ID}, {"role_id": DECRYPTION_ROLE_ID}],
        )
        summary = await authorization_summary(db_session, user.id)
        assert {
            (g.boundary.kind, g.boundary.organization_id)
            for g in summary.grants
            if g.permission == "secrets.read"
        } == {("platform", None), ("managed_organizations", None), ("organization", PROVIDER_ORG_ID)}

    async def test_authorization_summary_includes_only_the_callers_home_organization_name(
        self, db_session
    ) -> None:
        from src.services.user_role_assignments import authorization_summary

        organization = await _org(db_session)
        user = await _user(db_session, organization.id)

        summary = await authorization_summary(db_session, user.id)

        assert summary.home_organization_id == organization.id
        assert summary.home_organization_name == organization.name

    async def test_secrets_reader_boundaries_cannot_be_chosen(self, db_session) -> None:
        from src.services.user_role_assignments import (
            SECRETS_READER_BOUNDARY_MESSAGE,
            RoleAssignmentError,
        )

        user = await _user(db_session, PROVIDER_ORG_ID)
        for boundaries in (
            [{"kind": "platform"}],
            [{"kind": "managed_organizations"}, {"kind": "platform"}],
        ):
            with pytest.raises(RoleAssignmentError) as exc_info:
                await self._put(
                    db_session,
                    admin_caller(),
                    user,
                    additional=[{"role_id": DECRYPTION_ROLE_ID, "boundaries": boundaries}],
                )
            assert (exc_info.value.status_code, exc_info.value.detail) == (
                422,
                SECRETS_READER_BOUNDARY_MESSAGE,
            )

    async def test_privileged_access_grants_or_removes_secrets_reader(self, db_session) -> None:

        assigner = _delegate(({"roleassignments.readwrite", "roleassignments.read"}, _at(PROVIDER_ORG_ID)))
        plain = await _user(db_session, PROVIDER_ORG_ID)
        with pytest.raises(HTTPException) as exc_info:
            await self._put(
                db_session,
                assigner,
                plain,
                additional=[{"role_id": DECRYPTION_ROLE_ID}],
            )
        assert exc_info.value.status_code == 403

        privileged_assigner = _delegate(
            (
                {"roleassignments.readwrite", "roleassignments.read", "privilegedaccess.readwrite"},
                _at(PROVIDER_ORG_ID),
            )
        )
        response = await self._put(
            db_session,
            privileged_assigner,
            plain,
            additional=[{"role_id": DECRYPTION_ROLE_ID}],
        )
        assert [assignment.role_id for assignment in response.additional] == [DECRYPTION_ROLE_ID]

        holder = await _user(db_session, PROVIDER_ORG_ID)
        await self._put(
            db_session,
            admin_caller(),
            holder,
            additional=[{"role_id": DECRYPTION_ROLE_ID}],
        )
        with pytest.raises((RoleAssignmentError, HTTPException)) as removal:
            await self._put(db_session, assigner, holder, additional=[])
        assert removal.value.status_code == 403

    async def test_admin_grants_and_removes_platform_admin_as_an_additional_role(self, db_session) -> None:
        user = await _user(db_session, PROVIDER_ORG_ID)
        await _user(db_session, PROVIDER_ORG_ID, admin=True)
        admin_item = {"role_id": PLATFORM_ADMIN_ROLE_ID}

        response = await self._put(db_session, admin_caller(), user, additional=[admin_item])
        assert user.is_superuser is True
        assert user.base_role_id == USER_ROLE_ID
        assert await _boundaries(db_session, user.id, PLATFORM_ADMIN_ROLE_ID) == {("platform", None)}
        assert response.base_role.id == USER_ROLE_ID
        assert response.is_protected is True

        response = await self._put(db_session, admin_caller(), user, additional=[])
        assert user.is_superuser is False
        assert await _boundaries(db_session, user.id, PLATFORM_ADMIN_ROLE_ID) == set()
        assert response.additional == []

    async def test_platform_admin_is_never_a_base_role(self, db_session) -> None:
        from src.services.user_role_assignments import RoleAssignmentError, get_role_assignments

        user = await _user(db_session, PROVIDER_ORG_ID)
        with pytest.raises(RoleAssignmentError) as exc_info:
            await self._put(db_session, admin_caller(), user, base=PLATFORM_ADMIN_ROLE_ID)
        assert exc_info.value.status_code == 422
        assert user.is_superuser is False

        view = await get_role_assignments(db_session, admin_caller(), user_id=user.id)
        listed = {r.id: r for r in view.assignable_roles}[PLATFORM_ADMIN_ROLE_ID]
        assert (listed.can_be_base, listed.can_be_additional) == (False, True)
        assert listed.boundary_kinds == ["platform"]

    async def test_platform_admin_applies_only_at_the_platform_boundary(self, db_session) -> None:
        from src.services.user_role_assignments import RoleAssignmentError

        user = await _user(db_session, PROVIDER_ORG_ID)
        for boundaries in (
            [{"kind": "organization", "organization_id": str(PROVIDER_ORG_ID)}],
            [{"kind": "managed_organizations"}],
        ):
            with pytest.raises(RoleAssignmentError) as exc_info:
                await self._put(
                    db_session,
                    admin_caller(),
                    user,
                    additional=[{"role_id": PLATFORM_ADMIN_ROLE_ID, "boundaries": boundaries}],
                )
            assert exc_info.value.status_code == 422
        assert user.is_superuser is False

    async def test_platform_admin_is_only_for_provider_org_and_global_people(self, db_session) -> None:
        from src.services.user_role_assignments import (
            ADMIN_HOLDER_MESSAGE,
            RoleAssignmentError,
            get_role_assignments,
        )

        customer = await _user(db_session, (await _org(db_session)).id)
        view = await get_role_assignments(db_session, admin_caller(), user_id=customer.id)
        assert PLATFORM_ADMIN_ROLE_ID not in {r.id for r in view.assignable_roles}
        with pytest.raises(RoleAssignmentError) as exc_info:
            await self._put(
                db_session, admin_caller(), customer, additional=[{"role_id": PLATFORM_ADMIN_ROLE_ID}]
            )
        assert (exc_info.value.status_code, exc_info.value.detail) == (409, ADMIN_HOLDER_MESSAGE)
        assert customer.is_superuser is False

    async def test_a_global_platform_admin_cannot_lose_the_role(self, db_session) -> None:
        from src.services.user_role_assignments import ADMIN_REMOVAL_MESSAGE, RoleAssignmentError

        global_admin = await _user(db_session, None, admin=True)
        with pytest.raises(RoleAssignmentError) as exc_info:
            await self._put(db_session, admin_caller(), global_admin, additional=[])
        assert (exc_info.value.status_code, exc_info.value.detail) == (409, ADMIN_REMOVAL_MESSAGE)
        assert global_admin.is_superuser is True

        from src.services.user_role_assignments import get_role_assignments

        view = await get_role_assignments(db_session, admin_caller(), user_id=global_admin.id)
        assert PLATFORM_ADMIN_ROLE_ID not in {r.id for r in view.assignable_roles}

    async def test_platform_admin_assignment_requires_privileged_access(self, db_session) -> None:

        assigner = _delegate(({"roleassignments.readwrite", "roleassignments.read"}, _at(PROVIDER_ORG_ID)))
        plain = await _user(db_session, PROVIDER_ORG_ID)
        with pytest.raises(HTTPException) as exc_info:
            await self._put(db_session, assigner, plain, additional=[{"role_id": PLATFORM_ADMIN_ROLE_ID}])
        assert exc_info.value.status_code == 403
        assert plain.is_superuser is False

        admin = await _user(db_session, PROVIDER_ORG_ID, admin=True)
        with pytest.raises(HTTPException) as http_exc:
            await self._put(db_session, assigner, admin, additional=[])
        assert http_exc.value.status_code == 403
        assert admin.is_superuser is True

    async def test_delegate_ceiling_and_boundary_reach(self, db_session) -> None:
        from src.services.user_role_assignments import RoleAssignmentError

        org_a, org_b = await _org(db_session), await _org(db_session)
        user = await _user(db_session, org_a.id)
        plain, with_permission = await _role(db_session), await _role(db_session, "forms.read")
        assigner = _delegate(({"roleassignments.readwrite", "roleassignments.read"}, _at(org_a.id)))

        response = await self._put(
            db_session, assigner, user, additional=[{"role_id": plain.id}, {"role_id": with_permission.id}]
        )
        assert await _boundaries(db_session, user.id, plain.id) == {("organization", org_a.id)}
        assignable = {r.id: r for r in response.assignable_roles}
        assert plain.id in assignable and with_permission.id in assignable
        assert not any(r.can_be_base for r in assignable.values())

        privileged = await _role(db_session, "users.readwrite")
        with pytest.raises(HTTPException) as exc_info:
            await self._put(
                db_session,
                assigner,
                user,
                additional=[{"role_id": plain.id}, {"role_id": with_permission.id}, {"role_id": privileged.id}],
            )
        assert exc_info.value.status_code == 403
        with pytest.raises(RoleAssignmentError) as exc_info:
            await self._put(
                db_session,
                assigner,
                user,
                additional=[
                    {
                        "role_id": plain.id,
                        "boundaries": [{"kind": "organization", "organization_id": str(org_b.id)}],
                    }
                ],
            )
        assert exc_info.value.status_code == 403
        with pytest.raises(HTTPException) as http_exc:
            await self._put(db_session, assigner, user, base=PLATFORM_ADMIN_ROLE_ID)
        assert http_exc.value.status_code == 403

    async def test_a_delegate_may_leave_an_assignment_they_could_not_make(self, db_session) -> None:
        org = await _org(db_session)
        user = await _user(db_session, org.id)
        with_permission, plain = await _role(db_session, "forms.read"), await _role(db_session)
        await self._put(db_session, admin_caller(), user, additional=[{"role_id": with_permission.id}])
        assigner = _delegate(({"roleassignments.readwrite"}, _at(org.id)))
        response = await self._put(
            db_session,
            assigner,
            user,
            additional=[{"role_id": with_permission.id}, {"role_id": plain.id}],
        )
        assert {a.role_id for a in response.additional} == {with_permission.id, plain.id}

    async def test_non_admin_cannot_change_own_assignments_with_delegated_privileged_access(self, db_session) -> None:
        from src.services.user_role_assignments import RoleAssignmentError

        user = await _user(db_session, PROVIDER_ORG_ID)
        caller = _delegate(
            (
                {
                    "roleassignments.read",
                    "roleassignments.readwrite",
                    "privilegedaccess.readwrite",
                    "userlifecycle.readwrite",
                    "users.readwrite",
                },
                _at(PROVIDER_ORG_ID),
            ),
            user_id=user.id,
        )
        with pytest.raises(RoleAssignmentError) as exc_info:
            await self._put(db_session, caller, user, additional=[{"role_id": DECRYPTION_ROLE_ID}])
        assert exc_info.value.status_code == 400

    async def test_platform_admin_cannot_remove_their_own_last_admin_role(self, db_session) -> None:
        from src.services.user_role_assignments import RoleAssignmentError

        user = await _user(db_session, PROVIDER_ORG_ID, admin=True)
        with pytest.raises(RoleAssignmentError) as exc_info:
            await self._put(db_session, admin_caller(user_id=user.id), user, additional=[])
        assert exc_info.value.status_code == 409

    async def test_assign_users_to_role_writes_the_default_boundary(self, db_session) -> None:
        from shared.sdk_roles import assign_users_to_role

        org = await _org(db_session)
        user = await _user(db_session, org.id)
        role = await _role(db_session)
        await assign_users_to_role(db_session, admin_caller(), role_id=role.id, user_ids=[str(user.id)])
        assert await _boundaries(db_session, user.id, role.id) == {("organization", org.id)}
