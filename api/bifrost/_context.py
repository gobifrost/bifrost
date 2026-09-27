"""
Execution context management for Bifrost SDK.

Provides ContextVar-based context propagation from workflow engine to SDK calls.

Usage in workflows:
    from bifrost import context

    @workflow
    async def my_workflow(name: str) -> dict:
        # Access context directly - no need to pass as parameter
        user = context.user_id
        org = context.org_id
        return {"greeting": f"Hello {name} from {user}"}
"""

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bifrost._execution_context import ExecutionContext

# Context variable for current execution context
# Set by workflow engine before executing user code
_execution_context: ContextVar["ExecutionContext | None"] = ContextVar(
    "bifrost_execution_context", default=None
)


class _ContextProxy:
    """
    Proxy object that retrieves ExecutionContext from the contextvar.

    This allows workflows to access the execution context without
    having it passed as a parameter. The proxy forwards all attribute
    access to the underlying ExecutionContext.

    Example:
        from bifrost import context

        @workflow
        async def my_workflow() -> dict:
            return {"user": context.user_id, "org": context.org_id}
    """

    def __getattr__(self, name: str) -> Any:
        """Forward attribute access to the underlying ExecutionContext."""
        ctx = _execution_context.get()
        if ctx is None:
            # Raise AttributeError so hasattr() returns False correctly
            # (hasattr only catches AttributeError, not RuntimeError)
            raise AttributeError(
                f"'{type(self).__name__}' has no attribute '{name}' - "
                "No active execution context. "
                "This usually means you're trying to access 'context' outside of a workflow execution. "
                "Make sure you're inside a @workflow decorated function."
            )
        return getattr(ctx, name)

    def __repr__(self) -> str:
        ctx = _execution_context.get()
        if ctx is None:
            return "<ExecutionContext: not active>"
        return f"<ExecutionContext: user={ctx.user_id}, scope={ctx.scope}, execution={ctx.execution_id}>"


# The proxy object that workflows will import via `from bifrost import context`
context: "ExecutionContext" = _ContextProxy()  # type: ignore[assignment]


def set_execution_context(ctx: "ExecutionContext") -> None:
    """
    Set the execution context for the current workflow execution.

    Called by the workflow engine before executing user code.

    Args:
        ctx: ExecutionContext with user, org, and permission info
    """
    _execution_context.set(ctx)


def get_execution_context() -> "ExecutionContext":
    """
    Get the current execution context.

    Returns:
        ExecutionContext for the current execution

    Raises:
        RuntimeError: If called outside of a workflow execution context
    """
    ctx = _execution_context.get()
    if ctx is None:
        raise RuntimeError(
            "No execution context found. "
            "The bifrost SDK can only be used within workflow executions."
        )
    return ctx


def clear_execution_context() -> None:
    """
    Clear the execution context.

    Called by the workflow engine after user code execution completes.
    """
    _execution_context.set(None)


def get_default_scope() -> str | None:
    """
    Get the default scope from ExecutionContext if available.

    Used by SDK modules (tables, config, knowledge) to automatically
    include the correct scope when making API calls.

    Returns:
        org_id from context for scoped operations, or None for global scope.
        Returns None if not in a workflow execution context (CLI mode).
    """
    ctx = _execution_context.get()
    if ctx is None:
        return None  # Not in workflow context - let API handle it
    return ctx.org_id  # Returns None for GLOBAL scope, org UUID otherwise


def get_effective_solution(explicit: str | None) -> str | None:
    """SPIKE: per-call ``solution=`` override.

    Explicit (UUID or slug/name) wins for this call only. Unset (None) →
    inherit today's behavior (the active execution's own install, if any).
    Never mutates the session.
    """
    if explicit is not None:
        return explicit
    ctx = _execution_context.get()
    if ctx is None:
        return None
    return getattr(ctx, "solution_id", None)


def get_caller_solution() -> str | None:
    """SPIKE: the caller's OWN install for server-side inbound attestation.

    Always the inherited ExecutionContext install (never the explicit
    per-call target) — lets the server tell own-calls apart from
    cross-install calls. None outside a solution execution.
    """
    ctx = _execution_context.get()
    if ctx is None:
        return None
    return getattr(ctx, "solution_id", None)


def resolve_scope(scope: str | None) -> str | None:
    """Resolve scope for SDK calls with the C2 bypass gate.

    The same rule the API enforces: a caller asking for a scope other
    than their own org must be either a platform admin (engine carries
    this on ``ExecutionContext.is_platform_admin``) OR a member of a
    provider org. Either gate alone is sufficient.

    CLI mode (no ExecutionContext) returns the requested scope unchanged
    — the API will apply its own resolver against the user's JWT.
    """
    default = get_default_scope()
    if scope is None:
        return default
    if scope == default:
        return scope
    ctx = _execution_context.get()
    if ctx is None:
        return scope  # CLI mode — user authenticates with their own JWT
    is_platform_admin = bool(getattr(ctx, "is_platform_admin", False))
    is_provider_org = bool(ctx.organization and ctx.organization.is_provider)
    if is_platform_admin or is_provider_org:
        return scope
    raise PermissionError(
        f"Scope override to '{scope}' denied. "
        "Platform admins or provider-org members only."
    )


def register_secret(value: "str | None") -> None:
    """
    Register a plaintext secret value with the active ExecutionContext.

    Called by SDK modules after receiving decrypted secrets from the API
    so the engine scrubber will redact them from outputs and logs.

    Safe to call outside a workflow execution — silently no-ops.
    """
    ctx = _execution_context.get()
    if ctx is None:
        return
    ctx._register_dynamic_secret(value)
