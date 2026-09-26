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
