"""Evaluator-only rules: role grants and their boundaries, and the operation
classes that are not permission checks."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from shared.builtin_roles import (
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_PERMISSIONS,
    PLATFORM_OPERATOR_ROLE_ID,
    SECRETS_READER_PERMISSIONS,
    SECRETS_READER_ROLE_ID,
    USER_BASE_PERMISSIONS,
    USER_ROLE_ID,
)
from src.core.constants import PROVIDER_ORG_ID
from src.models.contracts.access_list import AccessClass, AccessEntry, CurrentGate, InlineEffect
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.evaluator import GLOBAL, HOME, cross_org, decide

CUSTOMER_A = UUID("00000000-0000-0000-0000-00000000a001")
CUSTOMER_B = UUID("00000000-0000-0000-0000-00000000a002")


def _entry(access_class: AccessClass, **kwargs) -> AccessEntry:
    kwargs.setdefault("current_gate", CurrentGate.AUTHENTICATED)
    return AccessEntry(method="GET", path="/x", access_class=access_class, reason="test", **kwargs)


def _perm_entry(permission: str, boundary: str = "organization", **kwargs) -> AccessEntry:
    return _entry(AccessClass.PERMISSION, permission=permission, boundary=boundary, **kwargs)


def _ctx(*grants: RoleGrant, home: UUID | None = CUSTOMER_A, base: UUID = USER_ROLE_ID) -> AuthorizationContext:
    return AuthorizationContext(
        user_id=uuid4(),
        home_organization_id=home,
        base_role_id=base,
        is_external=False,
        base_permissions=USER_BASE_PERMISSIONS if base == USER_ROLE_ID else frozenset(),
        role_grants=tuple(grants),
    )


def _operator(home: UUID = PROVIDER_ORG_ID) -> AuthorizationContext:
    grant = RoleGrant(
        PLATFORM_OPERATOR_ROLE_ID,
        PLATFORM_OPERATOR_PERMISSIONS,
        (Boundary(BoundaryKind.MANAGED_ORGANIZATIONS),),
    )
    return _ctx(grant, home=home)


class TestPlatformOperatorAtManagedOrganizations:
    entry = _perm_entry("organizations.read")

    def test_allowed_at_a_customer_org(self) -> None:
        decision = decide(_operator(), self.entry, cross_org(CUSTOMER_A))
        assert decision.allowed
        assert decision.rule == f"role:{PLATFORM_OPERATOR_ROLE_ID}:organizations.read@managed_organizations"

    def test_denied_at_the_provider_org(self) -> None:
        assert not decide(_operator(), self.entry, cross_org(PROVIDER_ORG_ID)).allowed

    def test_denied_at_global(self) -> None:
        assert not decide(_operator(), self.entry, GLOBAL).allowed

    def test_denied_for_a_permission_the_role_lacks(self) -> None:
        assert not decide(_operator(), _perm_entry("agents.readwrite"), cross_org(CUSTOMER_A)).allowed

    def test_denied_for_a_platform_boundary_entry(self) -> None:
        assert not decide(_operator(), _perm_entry("metrics.read", boundary="platform"), cross_org(CUSTOMER_A)).allowed


class TestCustomRoleAtOrganizationBoundary:
    grant = RoleGrant(uuid4(), frozenset({"agents.readwrite"}), (Boundary(BoundaryKind.ORGANIZATION, CUSTOMER_A),))
    entry = _perm_entry("agents.readwrite")

    def test_allowed_at_exactly_that_org(self) -> None:
        assert decide(_ctx(self.grant, home=PROVIDER_ORG_ID), self.entry, cross_org(CUSTOMER_A)).allowed

    def test_denied_at_another_org(self) -> None:
        assert not decide(_ctx(self.grant, home=PROVIDER_ORG_ID), self.entry, cross_org(CUSTOMER_B)).allowed

    def test_denied_at_global(self) -> None:
        assert not decide(_ctx(self.grant, home=PROVIDER_ORG_ID), self.entry, GLOBAL).allowed

    def test_applies_at_home_when_home_is_the_boundary_org(self) -> None:
        assert decide(_ctx(self.grant, home=CUSTOMER_A), self.entry, HOME).allowed

    def test_explicit_own_org_is_home(self) -> None:
        assert decide(_ctx(self.grant, home=CUSTOMER_A), self.entry, cross_org(CUSTOMER_A)).allowed


class TestPlatformBoundary:
    grant = RoleGrant(uuid4(), frozenset({"settings.readwrite"}), (Boundary(BoundaryKind.PLATFORM),))

    def test_satisfies_a_platform_boundary_entry(self) -> None:
        assert decide(_ctx(self.grant), _perm_entry("settings.readwrite", boundary="platform"), HOME).allowed

    def test_covers_global_for_an_organization_entry(self) -> None:
        assert decide(_ctx(self.grant), _perm_entry("settings.readwrite"), GLOBAL).allowed

    def test_does_not_cover_a_customer_org(self) -> None:
        assert not decide(_ctx(self.grant), _perm_entry("settings.readwrite"), cross_org(CUSTOMER_B)).allowed

    def test_base_role_never_satisfies_a_platform_boundary_entry(self) -> None:
        assert not decide(_ctx(), _perm_entry("settings.read", boundary="platform"), HOME).allowed


class TestBaseRole:
    def test_base_permission_applies_at_home_only(self) -> None:
        entry = _perm_entry("agents.read")
        assert decide(_ctx(), entry, HOME).rule == "base_role:agents.read"
        assert not decide(_ctx(), entry, cross_org(CUSTOMER_B)).allowed
        assert not decide(_ctx(), entry, GLOBAL).allowed

    def test_missing_permission_is_denied_with_its_name(self) -> None:
        decision = decide(_ctx(), _perm_entry("agents.readwrite"), HOME)
        assert (decision.allowed, decision.rule) == (False, "denied:missing:agents.readwrite")


class TestPlatformAdmin:
    admin = _ctx(base=PLATFORM_ADMIN_ROLE_ID, home=PROVIDER_ORG_ID)

    def test_allowed_at_home_for_any_permission(self) -> None:
        assert decide(self.admin, _perm_entry("agents.readwrite"), HOME).rule == "platform_admin"

    def test_allowed_beyond_own_org_only_where_the_operation_acts_beyond_it(self) -> None:
        wide = _perm_entry("agents.read", inline_effect=InlineEffect.WIDENS_FOR_SUPERUSER, inline_checks=("is_superuser",))
        confined = _perm_entry("agents.read")
        assert decide(self.admin, wide, cross_org(CUSTOMER_A)).allowed
        assert not decide(self.admin, confined, cross_org(CUSTOMER_A)).allowed

    def test_superuser_gate_acts_beyond_own_org(self) -> None:
        entry = _perm_entry("settings.readwrite", current_gate=CurrentGate.SUPERUSER)
        assert decide(self.admin, entry, GLOBAL).allowed

    def test_wildcard_satisfies_extended_reads(self) -> None:
        entry = _perm_entry("apps.read.all", current_gate=CurrentGate.SUPERUSER)
        assert decide(self.admin, entry, HOME).rule == "platform_admin"


class TestSecretDecryption:
    """``secrets.read`` is never implied by the Platform Admin wildcard."""

    entry = _perm_entry("secrets.read", current_gate=CurrentGate.SUPERUSER)

    def test_platform_admin_without_an_explicit_grant_is_denied(self) -> None:
        admin = _ctx(base=PLATFORM_ADMIN_ROLE_ID, home=PROVIDER_ORG_ID)
        for target in (HOME, cross_org(CUSTOMER_A), GLOBAL):
            decision = decide(admin, self.entry, target)
            assert (decision.allowed, decision.rule) == (False, "denied:missing:secrets.read")

    def test_platform_admin_with_an_explicit_grant_is_allowed_where_it_applies(self) -> None:
        grant = RoleGrant(uuid4(), frozenset({"secrets.read"}), (Boundary(BoundaryKind.ORGANIZATION, CUSTOMER_A),))
        admin = _ctx(grant, base=PLATFORM_ADMIN_ROLE_ID, home=PROVIDER_ORG_ID)
        assert decide(admin, self.entry, cross_org(CUSTOMER_A)).allowed
        assert not decide(admin, self.entry, cross_org(CUSTOMER_B)).allowed

    def test_platform_admin_holding_secrets_reader_is_allowed(self) -> None:
        reader = RoleGrant(SECRETS_READER_ROLE_ID, SECRETS_READER_PERMISSIONS, (Boundary(BoundaryKind.ORGANIZATION, CUSTOMER_A),))
        admin = _ctx(reader, base=PLATFORM_ADMIN_ROLE_ID, home=PROVIDER_ORG_ID)
        decision = decide(admin, self.entry, cross_org(CUSTOMER_A))
        assert decision.rule == f"role:{SECRETS_READER_ROLE_ID}:secrets.read@organization"

    def test_user_with_an_explicit_grant_is_allowed(self) -> None:
        grant = RoleGrant(uuid4(), frozenset({"secrets.read"}), (Boundary(BoundaryKind.ORGANIZATION, CUSTOMER_A),))
        assert decide(_ctx(grant, home=CUSTOMER_A), self.entry, HOME).allowed


class TestNonPermissionClasses:
    @pytest.mark.parametrize(
        "access_class",
        [AccessClass.PERSONAL, AccessClass.EXECUTE, AccessClass.OWN_PRIVATE_AGENT, AccessClass.TABLE_POLICY],
    )
    def test_signed_in_user_at_home_only(self, access_class: AccessClass) -> None:
        entry = _entry(access_class)
        assert decide(_ctx(), entry, HOME).rule == "signed_in"
        assert not decide(_ctx(), entry, cross_org(CUSTOMER_B)).allowed
        assert not decide(None, entry, HOME).allowed

    def test_public_allows_anonymous_at_home(self) -> None:
        entry = _entry(AccessClass.PUBLIC, current_gate=CurrentGate.NONE)
        assert decide(None, entry, HOME).rule == "public"
        assert decide(_ctx(), entry, HOME).allowed

    def test_embed_is_never_a_user_decision(self) -> None:
        entry = _entry(AccessClass.EMBED, current_gate=CurrentGate.EMBED)
        admin = _ctx(base=PLATFORM_ADMIN_ROLE_ID)
        assert not decide(admin, entry, HOME).allowed
        assert not decide(None, entry, HOME).allowed

    def test_external_decides_like_a_user(self) -> None:
        external = AuthorizationContext(
            user_id=uuid4(),
            home_organization_id=CUSTOMER_A,
            base_role_id=USER_ROLE_ID,
            is_external=True,
            base_permissions=USER_BASE_PERMISSIONS,
        )
        assert decide(external, _perm_entry("agents.read"), HOME).allowed
        assert not decide(external, _perm_entry("agents.readwrite"), HOME).allowed
