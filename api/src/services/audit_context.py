"""
Audit actor context.

A per-request context that captures who initiated the current operation.
Populated by a FastAPI dependency on HTTP requests; left empty in worker,
CLI, and scheduler contexts unless explicitly set.

The emit_audit() helper reads this context to tag audit events with actor
metadata. Events emitted without an actor are either skipped or tagged with
a non-http source (sso_sync, scheduler, etc.) when the caller passes one
explicitly.
"""

from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ActorContext:
    """Per-request actor metadata for audit logging."""

    user_id: UUID | None
    organization_id: UUID | None
    email: str | None = None
    name: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    source: str = "http"
    # Execution (or service attempt) that produced the event, from a signed
    # engine/service token's ``engine_execution_id``; None for human HTTP.
    execution_id: UUID | None = None
    # Transport the request came in over: "web", "cli", "mcp", "embed",
    # "workflow", or "service". Unlike ``source`` (actor type), this is
    # about which client made the call. See
    # src/core/request_actor.py::actor_from_token_payload for resolution.
    surface: str = "web"


_actor: ContextVar[ActorContext | None] = ContextVar("audit_actor", default=None)


def current_actor() -> ActorContext | None:
    """Return the actor for the current context, or None if unset."""
    return _actor.get()


def set_actor(ctx: ActorContext) -> Token[ActorContext | None]:
    """Set the actor for the current context. Returns the reset token."""
    return _actor.set(ctx)


def clear_actor(token: Token[ActorContext | None] | None = None) -> None:
    """Clear the actor context (optionally with a reset token from set_actor)."""
    if token is not None:
        _actor.reset(token)
    else:
        _actor.set(None)


# The current request's raw ASGI ``scope`` dict (or None outside a request).
# Stashed by the request-context middleware (src/core/app_wiring.py) right
# alongside the actor, before ``call_next`` — Starlette's router mutates this
# same dict in place with ``scope["route"]`` once it matches a route, so by
# the time a handler calls emit_audit(), the route (if any) is already
# present. This intentionally uses no FastAPI/Starlette internals beyond the
# public ASGI scope contract.
_request_scope: ContextVar[dict | None] = ContextVar("audit_request_scope", default=None)


def current_request_scope() -> dict | None:
    """Return the current request's ASGI scope, or None outside a request."""
    return _request_scope.get()


def set_request_scope(scope: dict) -> Token[dict | None]:
    """Set the current request's ASGI scope. Returns the reset token."""
    return _request_scope.set(scope)


def clear_request_scope(token: Token[dict | None] | None = None) -> None:
    """Clear the request scope (optionally with a reset token from set_request_scope)."""
    if token is not None:
        _request_scope.reset(token)
    else:
        _request_scope.set(None)
