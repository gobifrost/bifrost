from src.sdk.context import ExecutionContext


class TestRegisterSecret:
    def _make_ctx(self):
        return ExecutionContext(
            user_id="u1", email="e@e.com", name="Test",
            scope="GLOBAL", organization=None,
            is_platform_admin=False, is_function_key=False,
            execution_id="exec-1",
        )

    def test_with_active_context(self):
        from bifrost._context import set_execution_context, clear_execution_context, register_secret
        ctx = self._make_ctx()
        set_execution_context(ctx)
        try:
            register_secret("my-oauth-token-abc")
            assert "my-oauth-token-abc" in ctx._collect_secret_values()
        finally:
            clear_execution_context()

    def test_a_secret_registered_before_any_context_is_kept_for_the_next_one(self):
        """An access token refreshed while the engine loads the workflow, before
        the execution context exists, is still redacted from that run."""
        from bifrost._context import clear_execution_context, register_secret, set_execution_context
        clear_execution_context()
        register_secret("refreshed-before-context-abc")  # must not raise
        ctx = self._make_ctx()
        set_execution_context(ctx)
        try:
            assert "refreshed-before-context-abc" in ctx._collect_secret_values()
        finally:
            clear_execution_context()
        later = self._make_ctx()
        set_execution_context(later)
        try:
            # Cleared with the context it was handed to.
            assert "refreshed-before-context-abc" not in later._collect_secret_values()
        finally:
            clear_execution_context()

    def test_short_value_excluded(self):
        from bifrost._context import set_execution_context, clear_execution_context, register_secret
        ctx = self._make_ctx()
        set_execution_context(ctx)
        try:
            register_secret("ab")
            assert "ab" not in ctx._collect_secret_values()
        finally:
            clear_execution_context()
