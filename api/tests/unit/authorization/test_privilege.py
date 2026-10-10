"""Privileged principals, the Platform Operator's role-assignment rule, and
the R3a grant ceiling (``src.services.authorization.privilege``)."""

from __future__ import annotations

from uuid import UUID, uuid4

from shared.builtin_roles import (
    DECRYPTION_ROLE_ID,
    DECRYPTION_ROLE_PERMISSIONS,
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_PERMISSIONS,
    PLATFORM_OPERATOR_ROLE_ID,
    USER_BASE_PERMISSIONS,
    USER_ROLE_ID,
    WILDCARD_PERMISSION,
)
from tests.helpers.authorization import platform_admin_grant
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.privilege import (
    is_privileged_principal,
    may_change_role_assignment,
    operator_assignable_role,
)

ORG_A = UUID("00000000-0000-0000-0000-00000000a001")
ORG_B = UUID("00000000-0000-0000-0000-00000000a002")


def _ctx(*grants: RoleGrant, admin: bool = False) -> AuthorizationContext:
    return AuthorizationContext(
        user_id=uuid4(),
        home_organization_id=ORG_A,
        base_role_id=USER_ROLE_ID,
        is_external=False,
        base_permissions=USER_BASE_PERMISSIONS,
        role_grants=(platform_admin_grant(), *grants) if admin else tuple(grants),
    )


def _grant(permissions: set[str], *boundaries: Boundary) -> RoleGrant:
    return RoleGrant(uuid4(), frozenset(permissions), boundaries or (Boundary(BoundaryKind.ORGANIZATION, ORG_A),))


class TestIsPrivilegedPrincipal:
    def test_no_permissions_is_not_privileged(self) -> None:
        assert not is_privileged_principal(frozenset())

    def test_user_base_role_is_not_privileged(self) -> None:
        assert not is_privileged_principal(_ctx().held_permissions)

    def test_one_privileged_permission_is_enough(self) -> None:
        assert is_privileged_principal(frozenset({"forms.read", "configs.readwrite"}))

    def test_read_only_and_extended_reads_are_not_privileged(self) -> None:
        assert not is_privileged_principal(frozenset({"configs.read", "agentruns.read.all", "users.read"}))

    def test_wildcard_is_privileged(self) -> None:
        assert is_privileged_principal(frozenset({WILDCARD_PERMISSION}))

    def test_platform_admin_context_is_privileged(self) -> None:
        assert is_privileged_principal(_ctx(admin=True).held_permissions)

    def test_platform_operator_holder_is_privileged(self) -> None:
        operator = _grant(set(PLATFORM_OPERATOR_PERMISSIONS), Boundary(BoundaryKind.MANAGED_ORGANIZATIONS))
        assert is_privileged_principal(_ctx(operator).held_permissions)

    def test_secrets_reader_holder_is_privileged(self) -> None:
        reader = RoleGrant(DECRYPTION_ROLE_ID, DECRYPTION_ROLE_PERMISSIONS, (Boundary(BoundaryKind.ORGANIZATION, ORG_A),))
        assert is_privileged_principal(_ctx(reader).held_permissions)

    def test_stitched_custom_roles(self) -> None:
        # Neither role is an admin role; the privileged permission sits in
        # one of them, and the principal is privileged.
        support = _grant({"users.read", "forms.read"})
        config = _grant({"configs.read", "configs.readwrite"})
        assert not is_privileged_principal(_ctx(support).held_permissions)
        assert is_privileged_principal(_ctx(support, config).held_permissions)

    def test_privileged_at_another_org_still_counts(self) -> None:
        # Privileged at Org B only: the person is a protected target when
        # acted on from Org A too. Boundaries do not narrow the check.
        at_b = _grant({"users.readwrite"}, Boundary(BoundaryKind.ORGANIZATION, ORG_B))
        assert is_privileged_principal(_ctx(at_b).held_permissions)


class TestHeldPermissions:
    def test_unions_base_and_every_grant_at_any_boundary(self) -> None:
        a = _grant({"users.read"}, Boundary(BoundaryKind.ORGANIZATION, ORG_A))
        b = _grant({"forms.readwrite"}, Boundary(BoundaryKind.ORGANIZATION, ORG_B))
        g = _grant({"settings.read"}, Boundary(BoundaryKind.PLATFORM))
        assert _ctx(a, b, g).held_permissions == USER_BASE_PERMISSIONS | {
            "users.read",
            "forms.readwrite",
            "settings.read",
        }

    def test_platform_admin_holds_the_wildcard(self) -> None:
        assert WILDCARD_PERMISSION in _ctx(admin=True).held_permissions


class TestOperatorAssignableRole:
    ordinary_target = USER_BASE_PERMISSIONS

    def test_permissionless_custom_role_on_ordinary_user(self) -> None:
        assert operator_assignable_role(
            role_id=uuid4(), role_permissions=frozenset(), target_permissions=self.ordinary_target
        )

    def test_role_with_any_permission_is_refused(self) -> None:
        for permission in ("forms.read", "agentruns.read.all", "users.readwrite"):
            assert not operator_assignable_role(
                role_id=uuid4(),
                role_permissions=frozenset({permission}),
                target_permissions=self.ordinary_target,
            ), permission

    def test_builtin_roles_are_refused_even_without_stored_permissions(self) -> None:
        # Platform Admin stores no permission rows (its access is the
        # wildcard in code), so the permission check alone would admit it.
        for role_id in (PLATFORM_ADMIN_ROLE_ID, USER_ROLE_ID, PLATFORM_OPERATOR_ROLE_ID, DECRYPTION_ROLE_ID):
            assert not operator_assignable_role(
                role_id=role_id, role_permissions=frozenset(), target_permissions=self.ordinary_target
            ), role_id

    def test_privileged_target_is_refused(self) -> None:
        assert not operator_assignable_role(
            role_id=uuid4(),
            role_permissions=frozenset(),
            target_permissions=frozenset({"users.readwrite"}),
        )

    def test_target_privileged_at_another_org_is_refused(self) -> None:
        at_b = _grant({"configs.readwrite"}, Boundary(BoundaryKind.ORGANIZATION, ORG_B))
        assert not operator_assignable_role(
            role_id=uuid4(), role_permissions=frozenset(), target_permissions=_ctx(at_b).held_permissions
        )

    def test_platform_admin_target_is_refused(self) -> None:
        assert not operator_assignable_role(
            role_id=uuid4(),
            role_permissions=frozenset(),
            target_permissions=_ctx(admin=True).held_permissions,
        )


class TestGrantCeiling:
    """Every actor who is not a Platform Admin is held to the Operator rule,
    whatever identity permissions their roles give them."""

    def _may(self, *, admin: bool, role_id=None, permissions=frozenset(), target=USER_BASE_PERMISSIONS) -> bool:
        return may_change_role_assignment(
            actor_is_platform_admin=admin,
            role_id=role_id or uuid4(),
            role_permissions=frozenset(permissions),
            target_permissions=target,
        )

    def test_platform_admin_may_change_anything(self) -> None:
        assert self._may(admin=True, role_id=PLATFORM_OPERATOR_ROLE_ID)
        assert self._may(admin=True, role_id=PLATFORM_ADMIN_ROLE_ID)
        assert self._may(admin=True, permissions={"userlifecycle.readwrite"}, target={"configs.readwrite"})

    def test_delegate_may_change_a_permissionless_custom_role_on_an_ordinary_user(self) -> None:
        assert self._may(admin=False)

    def test_delegate_may_not_change_a_role_with_permissions(self) -> None:
        for permission in ("forms.read", "users.read", "roleassignments.readwrite"):
            assert not self._may(admin=False, permissions={permission}), permission

    def test_delegate_may_not_change_builtin_roles(self) -> None:
        for role_id in (PLATFORM_ADMIN_ROLE_ID, USER_ROLE_ID, PLATFORM_OPERATOR_ROLE_ID, DECRYPTION_ROLE_ID):
            assert not self._may(admin=False, role_id=role_id), role_id

    def test_delegate_may_not_change_a_privileged_user(self) -> None:
        assert not self._may(admin=False, target=frozenset({"users.readwrite"}))
        assert not self._may(admin=False, target=_ctx(admin=True).held_permissions)
