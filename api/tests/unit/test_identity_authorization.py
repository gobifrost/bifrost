"""R3a identity rules in the services: per-field user updates, bulk per-user
outcomes, the base-role writer, role assignments with boundaries and the
grant ceiling, and role permission editing."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

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
from tests.helpers.authorization import admin_caller

pytestmark = pytest.mark.asyncio


def _delegate(*grants: tuple[set[str], tuple[Boundary, ...]], home: UUID = PROVIDER_ORG_ID) -> Caller:
    ctx = AuthorizationContext(
        user_id=uuid4(),
        home_organization_id=home,
        base_role_id=USER_ROLE_ID,
        is_external=False,
        base_permissions=USER_BASE_PERMISSIONS,
        role_grants=tuple(RoleGrant(uuid4(), frozenset(p), b) for p, b in grants),
    )
    return Caller(
        UserPrincipal(user_id=ctx.user_id, email="delegate@example.com", organization_id=home),
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


async def _user(db_session, org_id: UUID | None, *, admin: bool = False):
    from shared.sdk_users import set_platform_admin
    from src.models import User as UserORM

    # A Global user is inserted as a superuser (ck_users_org_requires_superuser);
    # the assignment follows from the writer.
    user = UserORM(
        email=f"ia-{uuid4().hex[:8]}@example.com",
        organization_id=org_id,
        name="Before",
        is_superuser=admin,
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
            "email": "users.lifecycle.readwrite",
            "is_superuser": "platform_admin",
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
        from shared.sdk_users import UserServiceError

        org = await _org(db_session)
        user = await _user(db_session, org.id)
        support = _delegate(({"users.readwrite"}, _at(org.id)))
        for fields in ({"email": "new@example.com"}, {"is_external": True}, {"email": None}):
            with pytest.raises(HTTPException) as exc_info:
                await self._update(db_session, support, user, **fields)
            assert exc_info.value.status_code == 403, fields
        lifecycle = _delegate(({"users.readwrite", "users.lifecycle.readwrite"}, _at(org.id)))
        with pytest.raises(UserServiceError) as exc_info:
            await self._update(db_session, lifecycle, user, is_superuser=False)
        assert exc_info.value.status_code == 403

    async def test_a_move_needs_the_source_and_the_destination(self, db_session) -> None:
        source, destination = await _org(db_session), await _org(db_session)
        user = await _user(db_session, source.id)
        lifecycle = {"users.lifecycle.readwrite"}
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
        user.base_role_id = custom.id
        await db_session.flush()
        updated = await self._update(db_session, admin_caller(), user, is_superuser=False)
        assert updated.is_superuser is False
        assert user.base_role_id == custom.id
        assert await _boundaries(db_session, user.id, PLATFORM_ADMIN_ROLE_ID) == set()

    async def test_promoting_adds_platform_admin_and_keeps_the_base_role(self, db_session) -> None:
        user = await _user(db_session, PROVIDER_ORG_ID)
        updated = await self._update(db_session, admin_caller(), user, is_superuser=True)
        assert updated.is_superuser is True
        assert user.base_role_id == USER_ROLE_ID
        assert await _boundaries(db_session, user.id, PLATFORM_ADMIN_ROLE_ID) == {("platform", None)}


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
        assert "Only a Platform Admin" in reasons[admin.id]
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
        assert result.succeeded == []
        assert "carry no permissions" in result.failed[0].reason

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
    async def test_identity_edit_keeps_other_permissions(self, db_session) -> None:
        from src.services.role_permissions import get_role_permissions, replace_identity_permissions

        role = await _role(db_session, "agents.readwrite", "users.read")
        response = await replace_identity_permissions(
            db_session, role_id=role.id, permissions=["users.readwrite", "organizations.read"]
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
        for permission in ("agents.readwrite", "secrets.read", "users.lifecycle.read"):
            with pytest.raises(RolePermissionError) as exc_info:
                await replace_identity_permissions(db_session, role_id=role.id, permissions=[permission])
            assert exc_info.value.status_code == 422, permission

    async def test_builtin_roles_are_read_only(self, db_session) -> None:
        from src.services.role_permissions import (
            RolePermissionError,
            describe_role_permissions,
            replace_identity_permissions,
        )

        with pytest.raises(RolePermissionError) as exc_info:
            await replace_identity_permissions(db_session, role_id=PLATFORM_OPERATOR_ROLE_ID, permissions=[])
        assert exc_info.value.status_code == 409
        described = await describe_role_permissions(db_session, role_id=PLATFORM_OPERATOR_ROLE_ID)
        assert described.is_builtin
        assert not any(item.editable for item in described.permissions + described.identity_permissions)


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
        assert as_delegate[plain.id].boundary_kinds == ["organization"]

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

    async def test_secrets_reader_is_not_assignable_yet(self, db_session) -> None:
        from src.services.user_role_assignments import RoleAssignmentError

        user = await _user(db_session, (await _org(db_session)).id)
        with pytest.raises(RoleAssignmentError) as exc_info:
            await self._put(db_session, admin_caller(), user, additional=[{"role_id": DECRYPTION_ROLE_ID}])
        assert exc_info.value.status_code == 409

    async def test_admin_grants_and_removes_platform_admin_as_an_additional_role(self, db_session) -> None:
        user = await _user(db_session, PROVIDER_ORG_ID)
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

    async def test_only_a_platform_admin_grants_or_removes_platform_admin(self, db_session) -> None:
        from src.services.user_role_assignments import ADMIN_ROLE_MESSAGE, RoleAssignmentError

        assigner = _delegate(({"roleassignments.readwrite", "roleassignments.read"}, _at(PROVIDER_ORG_ID)))
        plain = await _user(db_session, PROVIDER_ORG_ID)
        with pytest.raises(RoleAssignmentError) as exc_info:
            await self._put(db_session, assigner, plain, additional=[{"role_id": PLATFORM_ADMIN_ROLE_ID}])
        assert (exc_info.value.status_code, exc_info.value.detail) == (403, ADMIN_ROLE_MESSAGE)
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

        response = await self._put(db_session, assigner, user, additional=[{"role_id": plain.id}])
        assert await _boundaries(db_session, user.id, plain.id) == {("organization", org_a.id)}
        assignable = {r.id: r for r in response.assignable_roles}
        assert plain.id in assignable and with_permission.id not in assignable
        assert all(not r.permissions and not r.is_builtin for r in assignable.values())
        assert not any(r.can_be_base for r in assignable.values())

        with pytest.raises(RoleAssignmentError) as exc_info:
            await self._put(
                db_session,
                assigner,
                user,
                additional=[{"role_id": plain.id}, {"role_id": with_permission.id}],
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

    async def test_own_assignments_cannot_be_changed(self, db_session) -> None:
        from src.services.user_role_assignments import RoleAssignmentError

        org = await _org(db_session)
        user = await _user(db_session, org.id)
        caller = admin_caller(user_id=user.id)
        with pytest.raises(RoleAssignmentError) as exc_info:
            await self._put(db_session, caller, user)
        assert exc_info.value.status_code == 400

    async def test_assign_users_to_role_writes_the_default_boundary(self, db_session) -> None:
        from shared.sdk_roles import assign_users_to_role

        org = await _org(db_session)
        user = await _user(db_session, org.id)
        role = await _role(db_session)
        await assign_users_to_role(db_session, admin_caller(), role_id=role.id, user_ids=[str(user.id)])
        assert await _boundaries(db_session, user.id, role.id) == {("organization", org.id)}
