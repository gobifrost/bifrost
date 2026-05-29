"""Unit tests for ``_resolve_sdk_org_id`` scope validation and authorization.

The 2026-05 org-scoping overhaul replaced the pre-existing forgery surface
(``DeveloperContext.default_org_id``, settable by any authenticated user
via ``PUT /api/sdk/context``) with auth-verified sourcing from
``current_user.organization_id``. The C2 bypass gate allows both platform
admins (``is_superuser``) AND provider-org members to target other orgs;
all other callers are pinned to their own org.

These tests assert the post-overhaul contract:

    1. The "own org" used for the UNSET / explicit-own-org rules comes
       from the auth-verified principal, not request body or DB default.
    2. Platform admins can target any org or global.
    3. Provider-org members can target any org or global.
    4. Everyone else gets 403 on cross-org or explicit-global requests.
    5. Garbage scope strings raise 422 (no asyncpg 500).
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.auth import UserPrincipal
from src.routers.cli import _resolve_sdk_org_id


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _user(*, org_id: UUID | None, is_superuser: bool = False) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="t@example.com",
        organization_id=org_id,
        is_superuser=is_superuser,
    )


def _db_with_provider(is_provider: bool):
    """Mock db.execute returning the ``is_provider`` flag for the caller's org."""
    class FakeResult:
        def scalar_one_or_none(self):
            return is_provider
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult())
    return db


def _db_no_lookup():
    """Mock db.execute that should never be called (UNSET / own-org paths)."""
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=AssertionError("provider lookup should not run on UNSET/own-org paths")
    )
    return db


# ---------------------------------------------------------------------------
# Platform admin: bypass on every cross-scope request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_platform_admin_global_returns_none():
    user = _user(org_id=uuid4(), is_superuser=True)
    db = _db_with_provider(False)
    assert await _resolve_sdk_org_id(user, "global", db) is None


@pytest.mark.asyncio
async def test_platform_admin_can_target_any_org():
    user = _user(org_id=uuid4(), is_superuser=True)
    target = str(uuid4())
    db = _db_with_provider(False)
    assert await _resolve_sdk_org_id(user, target, db) == target


# ---------------------------------------------------------------------------
# Provider-org member: bypass without platform-admin flag
# (C2 gate — both flags are independent paths through the resolver.)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_org_member_can_request_global():
    """A regular user in a provider org gets the bypass even without is_superuser."""
    user = _user(org_id=uuid4(), is_superuser=False)
    db = _db_with_provider(True)
    assert await _resolve_sdk_org_id(user, "global", db) is None


@pytest.mark.asyncio
async def test_provider_org_member_can_target_other_org():
    user = _user(org_id=uuid4(), is_superuser=False)
    other = str(uuid4())
    db = _db_with_provider(True)
    assert await _resolve_sdk_org_id(user, other, db) == other


# ---------------------------------------------------------------------------
# Cross-tenant traversal: blocked
# (This is the bug class the overhaul exists to close.)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_admin_non_provider_cannot_request_global():
    user = _user(org_id=uuid4(), is_superuser=False)
    db = _db_with_provider(False)
    with pytest.raises(HTTPException) as exc:
        await _resolve_sdk_org_id(user, "global", db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_non_provider_cannot_target_other_org():
    user = _user(org_id=uuid4(), is_superuser=False)
    other = str(uuid4())
    db = _db_with_provider(False)
    with pytest.raises(HTTPException) as exc:
        await _resolve_sdk_org_id(user, other, db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_can_target_own_org():
    """Explicit request matching ``current_user.organization_id`` is always allowed.

    The provider-org lookup is skipped on this path — the request matches
    the caller's own org, no bypass needed.
    """
    own_org = uuid4()
    user = _user(org_id=own_org, is_superuser=False)
    db = _db_no_lookup()
    assert await _resolve_sdk_org_id(user, str(own_org), db) == str(own_org)


# ---------------------------------------------------------------------------
# UNSET behavior — uses caller_org_id from the auth-verified principal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unset_scope_uses_current_user_org():
    """``scope=None`` falls back to ``current_user.organization_id``,
    NOT to a per-user mutable default. This is the forgery-fix path —
    pre-overhaul a user could set DeveloperContext.default_org_id to
    another org and this branch would silently return that org.
    """
    own_org = uuid4()
    user = _user(org_id=own_org, is_superuser=False)
    db = _db_no_lookup()
    assert await _resolve_sdk_org_id(user, None, db) == str(own_org)


@pytest.mark.asyncio
async def test_unset_scope_with_no_org_returns_none():
    """A user with no organization_id (e.g. system account) on UNSET → None."""
    user = _user(org_id=None, is_superuser=True)
    db = _db_no_lookup()
    assert await _resolve_sdk_org_id(user, None, db) is None


@pytest.mark.asyncio
async def test_empty_string_treated_as_unset():
    """Empty string scope preserves the UNSET semantics for CLI clients."""
    own_org = uuid4()
    user = _user(org_id=own_org, is_superuser=False)
    db = _db_no_lookup()
    assert await _resolve_sdk_org_id(user, "", db) == str(own_org)


# ---------------------------------------------------------------------------
# Input validation: malformed scope is 422, not a downstream 500
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_garbage_scope_raises_422():
    user = _user(org_id=uuid4(), is_superuser=False)
    db = _db_no_lookup()
    with pytest.raises(HTTPException) as exc:
        await _resolve_sdk_org_id(user, "not-a-uuid", db)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_uppercase_uuid_accepted_for_admin():
    user = _user(org_id=uuid4(), is_superuser=True)
    upper = str(uuid4()).upper()
    db = _db_with_provider(False)
    result = await _resolve_sdk_org_id(user, upper, db)
    assert UUID(result) == UUID(upper)
