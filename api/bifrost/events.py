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
from ._context import resolve_scope


class events:
    """Event publishing operations (async)."""

    @staticmethod
    async def emit(
        topic: str,
        data: dict,
        scope: str | None = None,
    ) -> dict:
        """
        Publish an event to a topic. Workflows subscribed to this topic will run.

        Args:
            topic: Lowercase string, dot-separated (e.g. "acme.deal_won").
                   Validated server-side: ^[a-z0-9_.]+$, must contain a dot.
            data: JSON-serializable payload. Available to subscribers via
                  context.event.data.
            scope: Organization scope override. Omit to use the execution
                   context org (default). Pass an org UUID to target a specific
                   org (provider org context required, same rule as config.get).

        Returns:
            dict with keys: event_id (str), subscribers_notified (int)

        Raises:
            httpx.HTTPStatusError: If the API returns a non-2xx response.

        Example:
            >>> from bifrost import events
            >>> result = await events.emit("acme.deal_won", {"amount": 50000})
            >>> print(result["subscribers_notified"])
        """
        client = get_client()
        resolved = resolve_scope(scope)
        response = await client.post(
            "/api/events/emit",
            json={"topic": topic, "data": data, "scope": resolved},
        )
        raise_for_status_with_detail(response)
        return response.json()
