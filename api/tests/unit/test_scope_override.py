"""Tests for provider org scope override guard.

Tests:
- ExecutionContext.set_scope() — provider allows, non-provider denied, reset works
- resolve_scope() in _context.py — shared scope resolution with provider guard
"""

import pytest
from src.sdk.context import ExecutionContext, Organization


class TestSetScope:
    """Test ExecutionContext.set_scope() method."""

    def _make_ctx(self, org_id: str = "org-1", is_provider: bool = False) -> ExecutionContext:
        return ExecutionContext(
            user_id="u1",
            email="e@e.com",
            name="Test",
            scope=org_id,
            organization=Organization(id=org_id, name="Test Org", is_provider=is_provider),
            is_platform_admin=False,
            is_function_key=False,
            execution_id="exec-1",
        )

    def test_provider_can_override_scope(self):
        ctx = self._make_ctx(is_provider=True)
        ctx.set_scope("org-2")
        assert ctx.org_id == "org-2"

    def test_non_provider_cannot_override_scope(self):
        ctx = self._make_ctx(is_provider=False)
        with pytest.raises(PermissionError, match="Scope override to 'org-2' denied"):
            ctx.set_scope("org-2")

    def test_reset_scope_with_none(self):
        ctx = self._make_ctx(is_provider=True)
        ctx.set_scope("org-2")
        assert ctx.org_id == "org-2"
        ctx.set_scope(None)
        assert ctx.org_id == "org-1"

    def test_same_org_is_noop(self):
        ctx = self._make_ctx(is_provider=False)
        # Setting scope to own org should not raise
        ctx.set_scope("org-1")
        assert ctx.org_id == "org-1"
        assert ctx._scope_override is None

    def test_global_scope_no_org(self):
        ctx = ExecutionContext(
            user_id="u1",
            email="e@e.com",
            name="Test",
            scope="GLOBAL",
            organization=None,
            is_platform_admin=True,
            is_function_key=False,
            execution_id="exec-1",
        )
        with pytest.raises(PermissionError):
            ctx.set_scope("org-2")

    def test_org_id_reflects_override(self):
        ctx = self._make_ctx(is_provider=True)
        assert ctx.org_id == "org-1"
        ctx.set_scope("org-2")
        assert ctx.org_id == "org-2"
        ctx.set_scope("org-3")
        assert ctx.org_id == "org-3"
        ctx.set_scope(None)
        assert ctx.org_id == "org-1"


class TestResolveScope:
    """Test resolve_scope() from bifrost._context."""

    def _make_ctx(self, org_id: str = "org-1", is_provider: bool = False) -> ExecutionContext:
        return ExecutionContext(
            user_id="u1",
            email="e@e.com",
            name="Test",
            scope=org_id,
            organization=Organization(id=org_id, name="Test Org", is_provider=is_provider),
            is_platform_admin=False,
            is_function_key=False,
            execution_id="exec-1",
        )

    def test_none_returns_default(self):
        from bifrost._context import resolve_scope, set_execution_context, clear_execution_context

        ctx = self._make_ctx()
        set_execution_context(ctx)
        try:
            result = resolve_scope(None)
            assert result == "org-1"
        finally:
            clear_execution_context()

    def test_same_scope_passes(self):
        from bifrost._context import resolve_scope, set_execution_context, clear_execution_context

        ctx = self._make_ctx()
        set_execution_context(ctx)
        try:
            result = resolve_scope("org-1")
            assert result == "org-1"
        finally:
            clear_execution_context()

    def test_different_scope_provider_allows(self):
        from bifrost._context import resolve_scope, set_execution_context, clear_execution_context

        ctx = self._make_ctx(is_provider=True)
        set_execution_context(ctx)
        try:
            result = resolve_scope("org-2")
            assert result == "org-2"
        finally:
            clear_execution_context()

    def test_different_scope_non_provider_denied(self):
        from bifrost._context import resolve_scope, set_execution_context, clear_execution_context

        ctx = self._make_ctx(is_provider=False)
        set_execution_context(ctx)
        try:
            with pytest.raises(PermissionError, match="Scope override to 'org-2' denied"):
                resolve_scope("org-2")
        finally:
            clear_execution_context()

    def test_no_context_allows_any_scope(self):
        """CLI mode — no execution context, scope passes through."""
        from bifrost._context import resolve_scope, clear_execution_context

        clear_execution_context()
        result = resolve_scope("org-99")
        assert result == "org-99"

    def test_no_context_none_returns_none(self):
        """CLI mode — no execution context, None returns None."""
        from bifrost._context import resolve_scope, clear_execution_context

        clear_execution_context()
        result = resolve_scope(None)
        assert result is None

    def test_global_string_matches_default_global_scope(self):
        from bifrost._context import resolve_scope, set_execution_context, clear_execution_context

        ctx = ExecutionContext(
            user_id="u1",
            email="e@e.com",
            name="Test",
            scope="GLOBAL",
            organization=None,
            is_platform_admin=True,
            is_function_key=False,
            execution_id="exec-1",
        )
        set_execution_context(ctx)
        try:
            assert resolve_scope("global") is None
            assert resolve_scope(None) is None
        finally:
            clear_execution_context()


class TestOrganizationIsProvider:
    """Test that is_provider field exists and defaults correctly."""

    def test_default_false(self):
        org = Organization(id="org-1", name="Test")
        assert org.is_provider is False

    def test_explicit_true(self):
        org = Organization(id="org-1", name="Test", is_provider=True)
        assert org.is_provider is True
