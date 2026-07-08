"""Unit tests for ``resolve_target_org`` — the file/table/knowledge/claims
scope-hop resolver.

The gate widened from ``is_superuser`` only to the canonical two-flag bypass
``is_superuser OR is_provider_org`` (repositories/README.md — "Why two
independent bypass flags?"). Provider-org members (portal-hopping platform
staff) may target another org or global for write operations, same as platform
admins; a plain org user is still pinned to its own org regardless of the scope
it passes.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.org_filter import resolve_target_org
from src.core.principal import UserPrincipal


def _user(
    *,
    org_id,
    is_superuser: bool = False,
    is_provider_org: bool = False,
) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="t@example.com",
        organization_id=org_id,
        is_superuser=is_superuser,
        is_provider_org=is_provider_org,
    )


# ---------------------------------------------------------------------------
# Provider-org member (non-admin) can scope-hop, just like a superuser.
# ---------------------------------------------------------------------------


def test_provider_org_member_can_target_other_org() -> None:
    other = uuid4()
    user = _user(org_id=uuid4(), is_superuser=False, is_provider_org=True)
    assert resolve_target_org(user, str(other)) == other


def test_provider_org_member_can_target_global() -> None:
    user = _user(org_id=uuid4(), is_superuser=False, is_provider_org=True)
    assert resolve_target_org(user, "global") is None


def test_provider_org_member_unset_uses_default() -> None:
    default = uuid4()
    user = _user(org_id=uuid4(), is_superuser=False, is_provider_org=True)
    assert resolve_target_org(user, None, default_org_id=default) == default


# ---------------------------------------------------------------------------
# Platform admin path is unchanged.
# ---------------------------------------------------------------------------


def test_platform_admin_can_target_other_org() -> None:
    other = uuid4()
    user = _user(org_id=uuid4(), is_superuser=True, is_provider_org=False)
    assert resolve_target_org(user, str(other)) == other


# ---------------------------------------------------------------------------
# Plain org user (neither flag) is pinned to its own org — scope ignored.
# ---------------------------------------------------------------------------


def test_plain_org_user_ignores_scope() -> None:
    own = uuid4()
    other = uuid4()
    user = _user(org_id=own, is_superuser=False, is_provider_org=False)
    # Any scope value collapses to the caller's own org.
    assert resolve_target_org(user, str(other)) == own
    assert resolve_target_org(user, "global") == own
    assert resolve_target_org(user, None) == own


# ---------------------------------------------------------------------------
# Bad scope from a bypass caller still raises (fail-loud, no coercion).
# ---------------------------------------------------------------------------


def test_provider_org_member_garbage_scope_raises() -> None:
    user = _user(org_id=uuid4(), is_superuser=False, is_provider_org=True)
    with pytest.raises(ValueError):
        resolve_target_org(user, "not-a-uuid")
