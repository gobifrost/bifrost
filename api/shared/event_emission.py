"""Shared application service for topic event emission (``bifrost.events.emit``).

Single implementation of the fixed emission behavior used by the HTTP
handler (``api/src/routers/events.py::emit_topic_event``) and a future
engine-local dispatcher. Both paths share authorization, topic
validation, scope parsing, service-org confinement, target Solution
resolution, the inbound gate, and trustworthy caller resolution — so HTTP
and local results are identical by construction.

All failures raise :class:`EventEmissionError` (transport-neutral); the
HTTP adapter maps them to ``HTTPException`` and a future local dispatcher
maps them to ``ok: false`` frames.

Trust model (read carefully before wiring a local dispatcher):

- The service takes a trusted explicit principal plus trusted context —
  :class:`EventEmissionCaller` — and a validated request
  (``EmitEventRequest``). It never takes a raw child actor/caller claim.
  The caller identity used for the inbound own-call bypass is derived
  internally via ``resolve_trustworthy_caller``.
- Trustworthy caller precedence (existing behavior, preserved): the SIGNED
  engine claims on the execution-scoped token (``engine_execution_id`` /
  ``engine_solution_id`` — unforgeable without ``SECRET_KEY``) beat the
  DB-backed app-header install (``app_id``), which beats the SDK-attested
  ``caller_solution`` value, and then only on engine requests
  (``is_engine_user``). Everyone else resolves to ``None`` (outside any
  install).
- The constrained ``request.caller_solution`` case is preserved exactly:
  it is consulted only when the internally resolved caller is ``None``
  AND the principal is an engine user; malformed values resolve to
  ``None``. A local dispatcher must therefore supply a token-equivalent
  principal carrying the engine claims (``engine_execution_id`` /
  ``engine_solution_id``) from verified parent metadata — never from child
  frame fields — for the bypass to apply. Child-supplied
  ``caller_solution`` strings without that engine proof are ignored by
  construction.

Parent-side only: touches the database (Solution resolution, inbound
gate) and the durable emitter. A workflow child never imports this
module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.models.contracts.events import EmitEventRequest, EmitEventResponse

logger = logging.getLogger(__name__)


class EventEmissionError(Exception):
    """Transport-neutral emission failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and the local dispatcher (``ok: false`` frames) can map the same
    failure to their own transport. Status codes preserve the historical
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
    """Trusted caller for topic event emission.

    Carries the auth-verified ``UserPrincipal``, the database session, and
    the authenticated context ids (``solution_id`` from ``?solution=`` /
    ``X-Bifrost-App``, ``caller_solution_id`` from the format-gated
    ``?caller_solution=`` param, ``app_id`` from the app header). Built by
    the HTTP adapter from its authenticated context; a future parent
    caller constructs the token-equivalent principal from trusted parent
    metadata. Never derive authority from child-supplied fields.
    """

    user: UserPrincipal
    db: AsyncSession
    solution_id: str | None = None
    caller_solution_id: str | None = None
    app_id: str | None = None

    @classmethod
    def from_context(cls, ctx: Any, principal: UserPrincipal) -> EventEmissionCaller:
        """Build a trusted caller from an authenticated execution context."""
        return cls(
            user=principal,
            db=ctx.db,
            solution_id=getattr(ctx, "solution_id", None),
            caller_solution_id=getattr(ctx, "caller_solution_id", None),
            app_id=getattr(ctx, "app_id", None),
        )


async def emit_topic_event(
    caller: EventEmissionCaller,
    request: EmitEventRequest,
) -> EmitEventResponse:
    """Emit a topic event with the historical handler semantics.

    Preserves the exact status/error precedence, response DTO, subscriber
    count, event actor (``triggered_by=str(principal.user_id)``), and
    transaction/delivery behavior of
    ``api/src/routers/events.py::emit_topic_event``: durable emission and
    delivery go through ``src.services.events.emit_event`` (own session,
    commit, queue), which the service reuses rather than re-implements.

    Order: authorization (superuser or service principal, else 403) >
    topic validation (400) > scope parsing (400) > service-org
    confinement (403) > target Solution resolution + inbound gate (404)
    > durable emit.

    Args:
        caller: Trusted principal plus authenticated context ids.
        request: Validated emit request (topic, data, scope, solution,
            caller_solution).

    Returns:
        ``EmitEventResponse`` with the created event id and the number of
        subscriptions that will receive the event.

    Raises:
        EventEmissionError: 403/400/404 per the preserved precedence.
    """
    from src.services.solution_scope import (
        check_inbound_allowed,
        is_engine_user,
        is_service_principal,
        resolve_solution_ref,
        resolve_trustworthy_caller,
    )
    from src.services.events.validation import validate_topic

    service_caller = is_service_principal(caller.user)
    if not caller.user.is_superuser and not service_caller:
        raise EventEmissionError(403, "Not authorized to emit events")

    try:
        validate_topic(request.topic)
    except ValueError as exc:
        raise EventEmissionError(400, str(exc)) from exc

    organization_id: UUID | None = None
    if request.scope and request.scope != "GLOBAL":
        try:
            organization_id = UUID(request.scope)
        except ValueError:
            raise EventEmissionError(
                400,
                f"Invalid scope: must be a UUID or 'GLOBAL', got '{request.scope}'",
            ) from None

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
        if (
            resolved_caller is None
            and request.caller_solution
            and is_engine_user(caller.user)
        ):
            try:
                resolved_caller = UUID(str(request.caller_solution))
            except ValueError:
                resolved_caller = None
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
