"""
Events SDK for Bifrost.

Publish events to topics; subscribed workflows receive them.

Usage:
    from bifrost import events

    result = await events.emit(
        "acme.deal_won",
        {"deal_id": "...", "amount": 50000},
    )
"""

from __future__ import annotations

from .client import get_client, raise_for_status_with_detail
from ._context import resolve_scope, get_caller_solution, get_effective_solution


class events:
    """Event publishing operations (async)."""

    @staticmethod
    async def emit(
        topic: str,
        data: dict,
        scope: str | None = None,
        solution: str | None = None,
    ) -> dict:
        """
        Publish an event to a topic. Workflows subscribed to this topic will run.

        Inside an engine child this emits through the parent over the
        dedicated local transport (same service as the HTTP endpoint);
        elsewhere it calls the SDK API endpoint. A local attempt never
        falls back to HTTP — failures raise loudly (an emit may already
        have committed, so a retry could double-emit).

        Args:
            topic: Lowercase string, dot-separated (e.g. "acme.deal_won").
                   Validated server-side: ^[a-z0-9_.]+$, must contain a dot.
            data: JSON-serializable payload. Available to subscribers via
                  context.event.data.
            scope: Organization scope override. Omit to use the execution
                   context org (default). Pass an org UUID to target a specific
                   org (provider org context required, same rule as config.get).
            solution: Target solution install (UUID or slug/name) in the
                   resolved scope. Unset → the active execution's own
                   install, if any. Per-call only.

        Returns:
            dict with keys: event_id (str), subscribers_notified (int)

        Raises:
            httpx.HTTPStatusError: If the API returns a non-2xx response.

        Example:
            >>> from bifrost import events
            >>> result = await events.emit("acme.deal_won", {"amount": 50000})
            >>> print(result["subscribers_notified"])
        """
        from ._local_transport import get as _get_local_transport

        resolved = resolve_scope(scope)
        solution_id = get_effective_solution(solution)
        transport = _get_local_transport()
        if transport is not None:
            # Engine-local path: the parent emits through the shared
            # event-emission service over the dedicated channel. Scope
            # and solution ride as untrusted requested targets the
            # service re-validates; actor, app, org, and caller_solution
            # identity come from the parent's dispatch context, never
            # from child frames. A local attempt never falls back to
            # HTTP.
            return await transport.call_events_emit(
                topic,
                data,
                resolved,
                solution_id,
            )
        client = get_client()
        payload = {"topic": topic, "data": data, "scope": resolved}
        if solution_id:
            payload["solution"] = str(solution_id)
        caller = get_caller_solution()
        if caller:
            payload["caller_solution"] = str(caller)
        response = await client.post(
            "/api/events/emit",
            json=payload,
        )
        raise_for_status_with_detail(response)
        return response.json()
