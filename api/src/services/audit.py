"""
Audit log emission helper.

Call emit_audit() from any code path that performs an auditable action.
The helper reads the ActorContext from audit_context.py, writes an
audit_logs row, and swallows its own errors so audit failures never break
the primary operation.

For HTTP-originated calls, the FastAPI audit dependency populates the
ActorContext automatically, so handlers just call
emit_audit(db, "user.create", ...). For worker/CLI/scheduler calls, callers
may pass an explicit actor_override with source="sso_sync" (etc.) to record
system-initiated changes with user_id=None.
"""

from __future__ import annotations

import logging
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories.audit_logs import AuditLogRepository
from src.services.audit_context import ActorContext, current_actor

logger = logging.getLogger(__name__)

Outcome = Literal["success", "failure"]


async def emit_audit(
    db: AsyncSession,
    action: str,
    *,
    resource_type: str | None = None,
    resource_id: UUID | None = None,
    outcome: Outcome = "success",
    details: dict[str, Any] | None = None,
    actor_override: ActorContext | None = None,
) -> None:
    """
    Record an audit log entry for the current actor.

    Args:
        db: Database session (same session as the primary operation, so the
            audit row commits with it).
        action: Dotted event name, e.g. "user.create", "auth.login.failed".
        resource_type: Type of target entity ("user", "role", "organization").
        resource_id: ID of target entity.
        outcome: "success" or "failure".
        details: Event-specific metadata stored as JSONB.
        actor_override: For worker/CLI/scheduler callers that don't have an
            HTTP actor context. When passed, used instead of current_actor().

    Audit failures are logged and swallowed — they must not break the
    caller's primary operation.
    """
    actor = actor_override or current_actor()

    # No actor context = non-HTTP path without explicit override. Skip silently.
    # This avoids logging worker-originated changes as if they were user-initiated.
    if actor is None:
        return

    async def _insert(user_id: UUID | None) -> None:
        # Wrap the insert in a SAVEPOINT so a failed audit row (e.g. a dangling
        # actor FK from a stale token) rolls back only itself — never the
        # caller's primary transaction. Without this, the failed INSERT poisons
        # the shared session and the outer commit blows up, taking the real
        # operation down with it.
        async with db.begin_nested():
            repo = AuditLogRepository(db)
            await repo.create(
                action=action,
                user_id=user_id,
                organization_id=actor.organization_id,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome=outcome,
                source=actor.source,
                ip_address=actor.ip_address,
                user_agent=actor.user_agent,
                details=details,
            )

    try:
        await _insert(actor.user_id)
    except IntegrityError:
        # The actor's user_id points at a user that no longer exists (a stale
        # token after the user was deleted). Record the event without an actor
        # rather than dropping it.
        logger.warning(
            "Audit actor %s for %s no longer exists; recording without actor",
            actor.user_id,
            action,
        )
        try:
            await _insert(None)
        except Exception as exc:
            logger.warning(
                "Failed to emit audit event %s: %s", action, exc, exc_info=True
            )
    except Exception as exc:
        # Audit failures must never break the primary operation.
        logger.warning(
            "Failed to emit audit event %s: %s",
            action,
            exc,
            exc_info=True,
        )
