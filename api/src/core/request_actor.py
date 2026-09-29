"""Shared audit-actor builder for the API and worker-local SDK apps.

Both the main API app (``src.main``) and the worker-local engine SDK app
(``src.services.execution.worker_sdk_http``) install the same request-context
middleware (``src.core.app_wiring``). That middleware decodes the bearer
token and calls :func:`actor_from_token_payload` to turn its claims into the
:class:`~src.services.audit_context.ActorContext` that
:func:`src.services.audit.emit_audit` attributes events to. Keeping the
mapping in one pure function means HTTP and worker-local (Unix socket)
requests audit identically.

The engine token's ``engine_caller_*`` claims are audit attribution only:
they are never read for authorization, and they do not change ``sub``,
``is_superuser``, or the request's :class:`UserPrincipal`.
"""

from __future__ import annotations

from uuid import UUID

from src.core.constants import SYSTEM_USER_ID, SYSTEM_USER_UUID
from src.services.audit_context import ActorContext


def _uuid_or_none(value: object) -> UUID | None:
    """Parse a UUID claim, treating any malformed value as absent."""
    if value is None or value == "":
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


# Self-reported surface values a plain human access token's caller may claim
# via the ``X-Bifrost-Surface`` header. Attribution only: a caller can send
# any value here, so this is never used for authorization, only to label
# which first-party client made the call in the audit log.
_SELF_REPORTED_SURFACES = {"cli", "mcp"}


def resolve_self_reported_surface(surface_header: str | None) -> str:
    """Resolve the ``surface`` for a plain human/anonymous access token.

    ``X-Bifrost-Surface: cli|mcp`` is sent by the first-party CLI HTTP
    client and the MCP thin-wrapper bridge on themselves (see
    ``bifrost/client.py`` and ``services/mcp_server/tools/_http_bridge.py``).
    It is attribution only, exactly like the ``engine_caller_*`` claims:
    never read for authorization, only to label the audit row. Any other
    value, or no header, means an ordinary browser request.
    """
    if surface_header in _SELF_REPORTED_SURFACES:
        return surface_header
    return "web"


def actor_from_token_payload(
    payload: dict | None,
    *,
    ip_address: str | None,
    user_agent: str | None,
    surface_header: str | None = None,
) -> ActorContext | None:
    """Build the audit actor for one decoded access-token payload.

    Args:
        payload: Decoded token claims, or ``None`` when no valid token was
            presented. ``None`` yields ``None`` — the middleware substitutes
            an anonymous actor so unauthenticated events still carry network
            metadata.
        ip_address: Client IP, or ``None`` on a Unix-socket (child) request.
        user_agent: Request ``User-Agent`` header value, if any.
        surface_header: The request's self-reported ``X-Bifrost-Surface``
            header value, if any. Only consulted for a plain human access
            token; every other token type has an unforgeable surface
            derived from its own claims.

    Returns:
        The :class:`ActorContext` for the request, or ``None`` when
        ``payload`` is ``None``.

    Attribution rules:

    - Human access token (no engine/service/embed claim): ``sub`` is the
      user, ``org_id`` the scope, ``source`` ``"http"``, ``surface``
      resolved from ``X-Bifrost-Surface`` (see
      :func:`resolve_self_reported_surface`).
    - Embed session token (``embed`` claim): ``source`` ``"http"``,
      ``surface`` ``"embed"``.
    - Service token (``service_id`` claim): the service acts for the system
      sentinel user, ``source`` ``"service"``, ``surface`` ``"service"``,
      ``execution_id`` from ``engine_execution_id``.
    - Engine token (``engine_execution_id`` claim) with a signed human
      caller: the caller's claims attribute the event to the person whose
      workflow ran, ``source`` ``"workflow"``, ``surface`` ``"workflow"``.
    - Engine token without a human caller (absent or sentinel
      ``engine_caller_user_id``): the system sentinel user, ``source``
      ``"workflow"``, ``surface`` ``"workflow"``.

    Invalid UUID strings in any of those claims are treated as absent
    (never raise).
    """
    if payload is None:
        return None

    email = payload.get("email")
    name = payload.get("name")

    if "service_id" in payload:
        return ActorContext(
            user_id=SYSTEM_USER_UUID,
            organization_id=_uuid_or_none(payload.get("org_id")),
            email=email,
            name=name,
            ip_address=ip_address,
            user_agent=user_agent,
            source="service",
            execution_id=_uuid_or_none(payload.get("engine_execution_id")),
            surface="service",
        )

    if "engine_execution_id" in payload:
        caller_user_id = payload.get("engine_caller_user_id")
        if caller_user_id and caller_user_id != SYSTEM_USER_ID:
            return ActorContext(
                user_id=_uuid_or_none(caller_user_id),
                organization_id=_uuid_or_none(
                    payload.get("engine_caller_org_id")
                ),
                email=payload.get("engine_caller_email"),
                name=payload.get("engine_caller_name"),
                ip_address=ip_address,
                user_agent=user_agent,
                source="workflow",
                execution_id=_uuid_or_none(payload.get("engine_execution_id")),
                surface="workflow",
            )
        return ActorContext(
            user_id=SYSTEM_USER_UUID,
            organization_id=_uuid_or_none(payload.get("engine_caller_org_id")),
            email=email,
            name=name,
            ip_address=ip_address,
            user_agent=user_agent,
            source="workflow",
            execution_id=_uuid_or_none(payload.get("engine_execution_id")),
            surface="workflow",
        )

    if payload.get("embed"):
        return ActorContext(
            user_id=_uuid_or_none(payload.get("sub")),
            organization_id=_uuid_or_none(payload.get("org_id")),
            email=email,
            name=name,
            ip_address=ip_address,
            user_agent=user_agent,
            source="http",
            execution_id=None,
            surface="embed",
        )

    return ActorContext(
        user_id=_uuid_or_none(payload.get("sub")),
        organization_id=_uuid_or_none(payload.get("org_id")),
        email=email,
        name=name,
        ip_address=ip_address,
        user_agent=user_agent,
        source="http",
        execution_id=None,
        surface=resolve_self_reported_surface(surface_header),
    )
