"""Migrated ``bifrost.events`` facade tests over ``engine_request``.

``emit`` posts the exact HTTP body and errors surface as the same public
exceptions as the HTTP path — with no dedicated-channel frames and no
silent HTTP fallback.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest


@pytest.mark.asyncio
class TestEngineRequestFacade:
    """Gate C5c: the migrated events facade rides ``engine_request``.

    ``emit`` posts the exact HTTP body, keeps the shared client's default
    timeout with no override, and errors surface as the same public
    exceptions as the HTTP path — with no dedicated-channel frames and no
    silent HTTP fallback.
    """

    def _client(self, *responses):
        client = AsyncMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    async def test_emit_posts_exact_body_and_returns_dict(self):
        from bifrost.events import events as events_facade

        body = {"event_id": str(uuid4()), "subscribers_notified": 2}
        client = self._client(
            httpx.Response(200, json=body, request=httpx.Request("POST", "http://x"))
        )
        with (
            patch("bifrost.events.get_client", return_value=client),
            patch("bifrost.events.resolve_scope", return_value=None),
            patch("bifrost.events.get_effective_solution", return_value=None),
            patch("bifrost.events.get_caller_solution", return_value=None),
        ):
            result = await events_facade.emit("a.b", {"k": "v"})

        assert result == body
        call = client.engine_request.await_args
        assert call.args == ("POST", "/api/events/emit")
        assert call.kwargs["json"] == {
            "topic": "a.b",
            "data": {"k": "v"},
            "scope": None,
        }
        assert "timeout" not in call.kwargs

    async def test_emit_includes_scope_solution_and_caller(self):
        from bifrost.events import events as events_facade

        body = {"event_id": str(uuid4()), "subscribers_notified": 0}
        org_id = str(uuid4())
        solution_id = str(uuid4())
        caller_id = str(uuid4())
        client = self._client(
            httpx.Response(200, json=body, request=httpx.Request("POST", "http://x"))
        )
        with (
            patch("bifrost.events.get_client", return_value=client),
            patch("bifrost.events.resolve_scope", return_value=org_id),
            patch("bifrost.events.get_effective_solution", return_value=solution_id),
            patch("bifrost.events.get_caller_solution", return_value=caller_id),
        ):
            result = await events_facade.emit(
                "a.b", {}, scope=org_id, solution=solution_id
            )

        assert result == body
        assert client.engine_request.await_args.kwargs["json"] == {
            "topic": "a.b",
            "data": {},
            "scope": org_id,
            "solution": solution_id,
            "caller_solution": caller_id,
        }

    async def test_emit_error_statuses_surface_without_channel(self):
        from bifrost.client import BifrostAuthorizationError, BifrostAPIError
        from bifrost.events import events as events_facade

        request = httpx.Request("POST", "http://engine/api/events/emit")
        for status, exc_type in (
            (400, BifrostAPIError),
            (403, BifrostAuthorizationError),
            (500, BifrostAPIError),
        ):
            client = self._client(
                httpx.Response(status, json={"detail": "denied"}, request=request)
            )
            with patch("bifrost.events.get_client", return_value=client):
                with pytest.raises(exc_type) as exc_info:
                    await events_facade.emit("a.b", {})
            assert exc_info.value.response.status_code == status
