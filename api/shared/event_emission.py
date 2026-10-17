"""Emit topic events after current operation permission and scope admission.

HTTP and worker-local callers share the same typed runtime boundary. Solution
provenance comes from durable runtime state or validated local development,
never from the emission request's claimed caller install.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.models.contracts.events import EmitEventRequest, EmitEventResponse

class EventEmissionError(Exception):
    """Transport-neutral emission failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and callers reached over the worker-local engine socket read the same status/detail. Status codes preserve the historical
    handler responses exactly: 403 for authorization/service-confinement,
    400 for topic/scope validation, 404 for unknown/sealed Solution
    targets.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class EventEmissionCaller:
    """Authenticated principal, session, and selected Solution context."""

    user: UserPrincipal
    db: AsyncSession
    solution_id: str | None = None
    # Server-validated local-dev provenance.  Never populated from an emit DTO.
    dev_solution_id: str | None = None

    @classmethod
    def from_context(cls, ctx: Any, principal: UserPrincipal) -> EventEmissionCaller:
        """Build a trusted caller from an authenticated execution context."""
        return cls(
            user=principal,
            db=ctx.db,
            solution_id=getattr(ctx, "solution_id", None),
            dev_solution_id=getattr(ctx, "dev_solution_id", None),
        )


async def emit_topic_event(
    caller: EventEmissionCaller,
    request: EmitEventRequest,
) -> EmitEventResponse:
    """Authorize the actual target before Solution resolution or durable IO."""
    from shared.sdk_config import ScopeResolutionError, resolve_sdk_scope
    from src.services.authorization.enforce import load_caller, require_operation, org_target
    from src.services.solution_scope import (
        check_inbound_allowed,
        is_service_principal,
        resolve_solution_ref,
        resolve_trustworthy_caller,
    )
    from src.services.events.validation import validate_topic

    try:
        authority = await load_caller(caller.db, caller.user)
        organization_id = await resolve_sdk_scope("global" if request.scope == "GLOBAL" else request.scope, caller=authority)
        require_operation(authority, "POST /api/events/emit", org_target(organization_id))
    except ScopeResolutionError as exc:
        raise EventEmissionError(400 if exc.status_code == 422 else exc.status_code, exc.detail) from exc
    except HTTPException as exc:
        raise EventEmissionError(exc.status_code, str(exc.detail)) from exc

    service_caller = is_service_principal(caller.user)
    try:
        validate_topic(request.topic)
    except ValueError as exc:
        raise EventEmissionError(400, str(exc)) from exc

    if service_caller:
        # Services emit org-scoped only, into their own org: the token's
        # organization is the confinement boundary (no GLOBAL, no cross-org).
        if organization_id is None or organization_id != caller.user.organization_id:
            raise EventEmissionError(
                403, "Services may only emit into their own organization"
            )

    solution_id: UUID | None = None
    requested_solution = request.solution or caller.solution_id
    if requested_solution:
        solution_id = await resolve_solution_ref(
            caller.db, str(requested_solution), organization_id
        )
        if solution_id is None:
            raise EventEmissionError(404, "Solution not found")
        resolved_caller = await resolve_trustworthy_caller(caller.db, caller)
        if not await check_inbound_allowed(caller.db, solution_id, resolved_caller):
            raise EventEmissionError(404, "Solution not found")

    from src.services.events import emit_event as durable_emit_event

    event_id, subscribers_notified = await durable_emit_event(
        request.topic,
        request.data,
        organization_id=organization_id,
        solution_id=solution_id,
        triggered_by=str(caller.user.user_id),
    )

    return EmitEventResponse(
        event_id=str(event_id),
        subscribers_notified=subscribers_notified,
    )


__all__ = [
    "EventEmissionCaller",
    "EventEmissionError",
    "emit_topic_event",
]
