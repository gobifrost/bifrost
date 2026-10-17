"""SDK scope selection is forwarded; the API owns current permission checks."""

from __future__ import annotations

import pytest

from bifrost._context import (
    clear_execution_context,
    resolve_scope,
    set_execution_context,
)
from bifrost._execution_context import ExecutionContext, Organization


@pytest.fixture(autouse=True)
def _reset_ctx():
    clear_execution_context()
    yield
    clear_execution_context()


def _make_ctx(
    *,
    org_id: str | None = "00000000-0000-0000-0000-000000000000",
    is_provider: bool = False,
    is_platform_admin: bool = False,
) -> ExecutionContext:
    org = (
        Organization(id=org_id, name="t", is_active=True, is_provider=is_provider)
        if org_id is not None
        else None
    )
    return ExecutionContext(
        user_id="00000000-0000-0000-0000-000000000999",
        email="t@example.com",
        name="t",
        scope=org_id if org_id else "GLOBAL",
        organization=org,
        is_platform_admin=is_platform_admin,
        is_function_key=False,
        execution_id="00000000-0000-0000-0000-000000000111",
        workflow_name="wf",
        public_url="http://localhost",
    )


def test_cli_mode_returns_scope_unchanged():
    # No ExecutionContext = CLI mode. API will gate via JWT.
    assert resolve_scope("11111111-1111-1111-1111-111111111111") == "11111111-1111-1111-1111-111111111111"


def test_scope_matching_default_passes():
    own = "00000000-0000-0000-0000-000000000001"
    set_execution_context(_make_ctx(org_id=own, is_provider=False, is_platform_admin=False))
    assert resolve_scope(own) == own


def test_provider_org_bypass():
    set_execution_context(_make_ctx(is_provider=True, is_platform_admin=False))
    assert (
        resolve_scope("99999999-9999-9999-9999-999999999999")
        == "99999999-9999-9999-9999-999999999999"
    )


def test_platform_admin_bypass():
    """Pre-overhaul this raised PermissionError because the gate ignored
    is_platform_admin. The fix flips it."""
    set_execution_context(_make_ctx(is_provider=False, is_platform_admin=True))
    assert (
        resolve_scope("99999999-9999-9999-9999-999999999999")
        == "99999999-9999-9999-9999-999999999999"
    )


def test_ordinary_runtime_forwards_explicit_scope_for_server_admission():
    set_execution_context(_make_ctx(is_provider=False, is_platform_admin=False))
    assert resolve_scope("99999999-9999-9999-9999-999999999999") == "99999999-9999-9999-9999-999999999999"


def test_set_scope_platform_admin_in_non_provider_org_allowed():
    ctx = _make_ctx(is_provider=False, is_platform_admin=True)
    ctx.set_scope("99999999-9999-9999-9999-999999999999")
    assert ctx.org_id == "99999999-9999-9999-9999-999999999999"


def test_set_scope_provider_org_member_allowed():
    ctx = _make_ctx(is_provider=True, is_platform_admin=False)
    ctx.set_scope("99999999-9999-9999-9999-999999999999")
    assert ctx.org_id == "99999999-9999-9999-9999-999999999999"


def test_ordinary_runtime_can_select_scope_for_server_admission():
    ctx = _make_ctx(is_provider=False, is_platform_admin=False)
    ctx.set_scope("99999999-9999-9999-9999-999999999999")
    assert ctx.org_id == "99999999-9999-9999-9999-999999999999"


def test_set_scope_to_own_org_is_noop():
    ctx = _make_ctx(
        org_id="00000000-0000-0000-0000-000000000001",
        is_provider=False,
        is_platform_admin=False,
    )
    ctx.set_scope("00000000-0000-0000-0000-000000000001")
    assert ctx.org_id == "00000000-0000-0000-0000-000000000001"


def test_set_scope_none_resets_override():
    ctx = _make_ctx(is_provider=True, is_platform_admin=False)
    ctx.set_scope("99999999-9999-9999-9999-999999999999")
    ctx.set_scope(None)
    # Back to the ctx's own org.
    assert ctx.org_id == ctx.organization.id  # type: ignore[union-attr]
