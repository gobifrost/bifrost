"""Unit tests for shared.scope_resolver.

The resolver is the single security boundary for "which org_id does this
operation run against?" Tests here are the contract — they must assert the
four rules unambiguously, including cross-tenant rejection and the
unspecified-vs-explicit-None distinction.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from shared.scope_resolver import (
    UNSET,
    ScopeNotAllowed,
    resolve_effective_scope,
)


ORG_A = UUID("11111111-1111-1111-1111-111111111111")
ORG_B = UUID("22222222-2222-2222-2222-222222222222")


# ---------------------------------------------------------------------------
# Rule 1: UNSET → caller's default org. Always allowed.
# ---------------------------------------------------------------------------


class TestUnsetReturnsCallerDefault:
    def test_org_user_unset_returns_their_org(self) -> None:
        result = resolve_effective_scope(
            caller_org_id=ORG_A,
            is_platform_admin=False,
            requested_scope=UNSET,
        )
        assert result == ORG_A

    def test_platform_admin_unset_returns_their_org(self) -> None:
        result = resolve_effective_scope(
            caller_org_id=ORG_A,
            is_platform_admin=True,
            requested_scope=UNSET,
        )
        assert result == ORG_A

    def test_admin_with_no_org_unset_returns_none(self) -> None:
        # Platform admin not tied to any org. UNSET means "use my default,"
        # and the default is "no org" — that's global.
        result = resolve_effective_scope(
            caller_org_id=None,
            is_platform_admin=True,
            requested_scope=UNSET,
        )
        assert result is None

    def test_unset_is_default_argument(self) -> None:
        # Calling without requested_scope keyword should behave as UNSET.
        result = resolve_effective_scope(
            caller_org_id=ORG_A,
            is_platform_admin=False,
        )
        assert result == ORG_A


# ---------------------------------------------------------------------------
# Rule 2: explicit None → global. Platform admin only.
# ---------------------------------------------------------------------------


class TestExplicitGlobalRequiresPlatformAdmin:
    def test_platform_admin_can_request_global(self) -> None:
        result = resolve_effective_scope(
            caller_org_id=ORG_A,
            is_platform_admin=True,
            requested_scope=None,
        )
        assert result is None

    def test_org_user_cannot_request_global(self) -> None:
        with pytest.raises(ScopeNotAllowed):
            resolve_effective_scope(
                caller_org_id=ORG_A,
                is_platform_admin=False,
                requested_scope=None,
            )

    def test_org_user_with_no_org_cannot_request_global(self) -> None:
        # A non-admin caller with no org is anomalous but should still fail
        # closed on an explicit global request.
        with pytest.raises(ScopeNotAllowed):
            resolve_effective_scope(
                caller_org_id=None,
                is_platform_admin=False,
                requested_scope=None,
            )


# ---------------------------------------------------------------------------
# Rule 3: requested == caller's own org → always allowed.
# ---------------------------------------------------------------------------


class TestCallerOwnOrgAlwaysAllowed:
    def test_org_user_can_request_own_org(self) -> None:
        result = resolve_effective_scope(
            caller_org_id=ORG_A,
            is_platform_admin=False,
            requested_scope=ORG_A,
        )
        assert result == ORG_A

    def test_platform_admin_can_request_own_org(self) -> None:
        result = resolve_effective_scope(
            caller_org_id=ORG_A,
            is_platform_admin=True,
            requested_scope=ORG_A,
        )
        assert result == ORG_A


# ---------------------------------------------------------------------------
# Rule 4: requested == some OTHER org → platform admin only.
# This is the cross-tenant security boundary.
# ---------------------------------------------------------------------------


class TestCrossTenantRequiresPlatformAdmin:
    def test_platform_admin_can_request_other_org(self) -> None:
        result = resolve_effective_scope(
            caller_org_id=ORG_A,
            is_platform_admin=True,
            requested_scope=ORG_B,
        )
        assert result == ORG_B

    def test_org_user_cannot_request_other_org(self) -> None:
        # The core cross-tenant guard. If this test ever passes silently,
        # the resolver has a critical bug.
        with pytest.raises(ScopeNotAllowed):
            resolve_effective_scope(
                caller_org_id=ORG_A,
                is_platform_admin=False,
                requested_scope=ORG_B,
            )

    def test_org_user_with_no_org_cannot_request_any_org(self) -> None:
        with pytest.raises(ScopeNotAllowed):
            resolve_effective_scope(
                caller_org_id=None,
                is_platform_admin=False,
                requested_scope=ORG_A,
            )

    def test_admin_with_no_org_can_request_any_org(self) -> None:
        result = resolve_effective_scope(
            caller_org_id=None,
            is_platform_admin=True,
            requested_scope=ORG_A,
        )
        assert result == ORG_A


# ---------------------------------------------------------------------------
# UNSET vs explicit None must NOT be collapsed.
#
# This is the bug class this resolver exists to prevent: adapters must not
# treat "didn't pass scope" and "passed scope=null" the same way, because they
# have different security semantics.
# ---------------------------------------------------------------------------


class TestUnsetAndExplicitNoneAreDistinct:
    def test_org_user_unset_succeeds_but_explicit_none_fails(self) -> None:
        # Same caller, two requests. UNSET → their org. None → ScopeNotAllowed.
        unset_result = resolve_effective_scope(
            caller_org_id=ORG_A,
            is_platform_admin=False,
            requested_scope=UNSET,
        )
        assert unset_result == ORG_A

        with pytest.raises(ScopeNotAllowed):
            resolve_effective_scope(
                caller_org_id=ORG_A,
                is_platform_admin=False,
                requested_scope=None,
            )

    def test_admin_no_org_unset_returns_none_explicit_none_returns_none(
        self,
    ) -> None:
        # For platform admins without an org, both paths return None, but
        # for DIFFERENT reasons: UNSET because their default is None;
        # explicit None because they were authorized to ask for global.
        unset_result = resolve_effective_scope(
            caller_org_id=None,
            is_platform_admin=True,
            requested_scope=UNSET,
        )
        explicit_result = resolve_effective_scope(
            caller_org_id=None,
            is_platform_admin=True,
            requested_scope=None,
        )
        assert unset_result is None
        assert explicit_result is None


# ---------------------------------------------------------------------------
# Defense-in-depth: a random UUID should not be coerced into something benign.
# The resolver fails closed on any unauthorized request.
# ---------------------------------------------------------------------------


class TestNeverSilentlyCoerces:
    def test_unauthorized_request_raises_not_returns_default(self) -> None:
        # If a non-admin asks for another org, we must NOT silently return
        # caller_org_id as a "safe default." That would hide the breach.
        random_other_org = uuid4()
        with pytest.raises(ScopeNotAllowed):
            resolve_effective_scope(
                caller_org_id=ORG_A,
                is_platform_admin=False,
                requested_scope=random_other_org,
            )

    def test_unauthorized_global_raises_not_returns_caller_org(self) -> None:
        with pytest.raises(ScopeNotAllowed):
            resolve_effective_scope(
                caller_org_id=ORG_A,
                is_platform_admin=False,
                requested_scope=None,
            )


# ---------------------------------------------------------------------------
# UNSET sentinel sanity checks.
# ---------------------------------------------------------------------------


class TestUnsetSentinel:
    def test_unset_is_falsy(self) -> None:
        # Falsy so `if requested_scope:` doesn't accidentally treat UNSET as
        # "present." This is belt-and-suspenders — the resolver uses
        # isinstance, not truthiness — but the sentinel's __bool__ exists
        # specifically to prevent foot-guns elsewhere.
        assert not UNSET

    def test_unset_is_singleton(self) -> None:
        from shared.scope_resolver import _Unset

        assert _Unset() is UNSET

    def test_unset_repr(self) -> None:
        assert repr(UNSET) == "UNSET"
