"""Form reads and client context over ``BifrostClient.engine_request``.

Covers the migrated surface that does not need a forked child:

- the migrated forms facade rides ``BifrostClient.engine_request`` with the
  exact HTTP paths, mapping results to the public surface (``FormPublic``
  list, ``ValueError``/``PermissionError`` on get) and preserving
  HTTP-shaped errors, while the context facade now reads
  ``GET /api/sdk/context`` over the engine socket through the sync
  ``engine_request_sync`` / async ``engine_request`` entry points, keeping
  its cached ``BifrostClient.context`` (with ``user``/``organization``/
  ``default_parameters`` views);
- external callers (no injected socket) keep the HTTP path unchanged.
"""

from __future__ import annotations

import asyncio
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.enums import FormAccessLevel


def test_server_form_response_parses_in_python_sdk():
    """The SDK accepts the actual HTTP form contract, including absent file_path."""
    from bifrost.models import FormPublic as SDKFormPublic
    from src.models.contracts.forms import FormPublic as ServerFormPublic

    response = ServerFormPublic(
        id=uuid4(), name="contract-form", is_active=True,
        access_level=FormAccessLevel.AUTHENTICATED,
    ).model_dump(mode="json")

    parsed = SDKFormPublic.model_validate(response)
    assert parsed.name == "contract-form"
    assert parsed.file_path is None


@pytest.mark.asyncio
class TestFormsFacadeEngineRequest:
    """Gate C5c: the migrated forms facade rides ``engine_request``.

    ``list`` reads ``GET /api/forms`` and validates the list into
    ``FormPublic``; ``get`` reads ``GET /api/forms/{id}`` and maps 404/403 to
    the same public exceptions as the HTTP path — no dedicated-channel frames
    and no silent network fallback.
    """

    def _client(self, *responses):
        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    def _form_body(self, form_id):
        return {
            "id": form_id,
            "name": "engine-form",
            "confirmation_markdown": "Thanks",
            "workflow_id": None,
            "launch_workflow_id": None,
            "default_launch_params": None,
            "allowed_query_params": None,
            "form_schema": None,
            "access_level": "authenticated",
            "organization_id": None,
            "role_ids": [],
            "is_active": True,
            "created_at": None,
            "updated_at": None,
        }

    async def test_list_reads_exact_path_and_parses_models(self):
        from bifrost.forms import forms

        form_id = str(uuid4())
        client = self._client(
            httpx.Response(200, json=[self._form_body(form_id)])
        )
        with patch("bifrost.forms.get_client", return_value=client):
            result = await forms.list()

        assert [f.name for f in result] == ["engine-form"]
        assert result[0].id == form_id
        call = client.engine_request.await_args
        assert call.args == ("GET", "/api/forms")
        assert call.kwargs == {}

    async def test_get_reads_exact_path_and_parses_model(self):
        from bifrost.forms import forms

        form_id = str(uuid4())
        client = self._client(
            httpx.Response(200, json=self._form_body(form_id))
        )
        with patch("bifrost.forms.get_client", return_value=client):
            detail = await forms.get(form_id)

        assert detail.id == form_id
        assert detail.name == "engine-form"
        assert client.engine_request.await_args.args == (
            "GET",
            f"/api/forms/{form_id}",
        )

    async def test_get_maps_404_and_403_to_public_errors(self):
        from bifrost.forms import forms

        request = httpx.Request("GET", "http://engine/api/forms/x")
        for status, exc_type in ((404, ValueError), (403, PermissionError)):
            client = self._client(
                httpx.Response(status, json={"detail": "no"}, request=request)
            )
            with patch("bifrost.forms.get_client", return_value=client):
                with pytest.raises(exc_type):
                    await forms.get(str(uuid4()))

    async def test_list_error_statuses_surface(self):
        from bifrost.client import BifrostAPIError
        from bifrost.forms import forms

        request = httpx.Request("GET", "http://engine/api/forms")
        for status in (401, 403, 500):
            client = self._client(
                httpx.Response(status, json={"detail": "denied"}, request=request)
            )
            with patch("bifrost.forms.get_client", return_value=client):
                with pytest.raises(BifrostAPIError) as exc_info:
                    await forms.list()
            assert exc_info.value.response.status_code == status


class TestContextFacadeEngineRequest:
    """Gate C5h: the context facade rides ``engine_request``.

    ``BifrostClient.context`` reads ``GET /api/sdk/context`` over the
    engine-local transport with the synchronous ``engine_request_sync`` entry
    point; the async ``_fetch_context`` uses ``engine_request``. Both cache,
    map statuses through the shared HTTP mapping, and never fall back to the
    network once the engine socket is injected. External callers (no socket)
    keep the ordinary HTTP client.
    """

    def _client(self):
        from bifrost.client import BifrostClient

        return BifrostClient("http://127.0.0.1:9", "dead-token")

    def _payload(self, **overrides):
        data = {
            "user": {
                "id": str(uuid4()),
                "email": "engine@bifrost.internal",
                "name": "Bifrost Engine",
                "is_superuser": True,
            },
            "organization": None,
            "default_parameters": {},
            "track_executions": True,
        }
        data.update(overrides)
        return data

    def test_sync_property_uses_engine_request_sync_and_caches(self):
        payload = self._payload()
        client = self._client()
        client.engine_request_sync = MagicMock(
            return_value=httpx.Response(200, json=payload)
        )

        first = client.context
        second = client.context

        assert first == payload
        assert second == payload
        client.engine_request_sync.assert_called_once_with(
            "GET", "/api/sdk/context"
        )
        assert client.user == payload["user"]
        assert client.organization is None
        assert client.default_parameters == {}

    def test_sync_property_views_service_payload(self):
        org_id = str(uuid4())
        payload = self._payload(
            user={
                "id": str(uuid4()),
                "email": "service-abc@bifrost.internal",
                "name": "service-abc",
                "is_superuser": False,
            },
            organization={"id": org_id, "name": "Svc Org"},
        )
        client = self._client()
        client.engine_request_sync = MagicMock(
            return_value=httpx.Response(200, json=payload)
        )

        assert client.organization == {"id": org_id, "name": "Svc Org"}
        assert client.user["email"] == "service-abc@bifrost.internal"

    @pytest.mark.asyncio
    async def test_async_fetch_uses_engine_request_and_caches(self):
        payload = self._payload()
        client = self._client()
        client.engine_request = AsyncMock(
            return_value=httpx.Response(200, json=payload)
        )
        client.engine_request_sync = MagicMock(
            side_effect=AssertionError("cached fetch must not re-read")
        )

        result = await client._fetch_context()

        assert result == payload
        client.engine_request.assert_awaited_once_with("GET", "/api/sdk/context")
        # Cached: the sync property reuses the async fetch.
        assert client.context == payload
        assert client.engine_request.await_count == 1

    def test_errors_match_http_mapping(self):
        from bifrost.client import (
            BifrostAPIError,
            BifrostAuthenticationError,
            BifrostAuthorizationError,
        )

        request = httpx.Request("GET", "http://bifrost-engine/api/sdk/context")
        for status, expected in (
            (401, BifrostAuthenticationError),
            (403, BifrostAuthorizationError),
            (404, BifrostAPIError),
            (500, BifrostAPIError),
        ):
            client = self._client()
            client.engine_request_sync = MagicMock(
                return_value=httpx.Response(
                    status, json={"detail": "denied"}, request=request
                )
            )
            with pytest.raises(expected) as exc_info:
                client.context
            assert exc_info.value.response.status_code == status

    def test_injected_socket_never_uses_the_network_client(self, monkeypatch):
        payload = self._payload()
        client = self._client()
        engine_sync = MagicMock()
        engine_sync.request.return_value = httpx.Response(200, json=payload)
        network_sync = MagicMock()
        network_sync.request.side_effect = AssertionError(
            "context must not fall back to the network with a socket injected"
        )
        monkeypatch.setattr(client, "_get_engine_sync_client", lambda: engine_sync)
        monkeypatch.setattr(client, "_sync_http", network_sync)
        monkeypatch.setattr("bifrost.client._engine_socket_path", "/tmp/ctx.sock")

        assert client.context == payload

        engine_sync.request.assert_called_once()
        assert network_sync.request.call_count == 0

    def test_external_callers_keep_http(self, monkeypatch):
        payload = self._payload()
        captured: list[str] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            captured.append(str(request.url))
            return httpx.Response(200, json=payload)

        monkeypatch.setattr("bifrost.client._engine_socket_path", None)
        client = self._client()
        # No engine socket resolves the shared entry point to the ordinary
        # network sync client, which we point at a mock transport.
        client._sync_http = httpx.Client(
            base_url="http://external-api",
            transport=httpx.MockTransport(_handler),
        )

        assert client.context == payload
        assert captured == ["http://external-api/api/sdk/context"]

    @pytest.mark.asyncio
    async def test_external_async_fetch_keeps_http(self, monkeypatch):
        payload = self._payload()
        captured: list[str] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            captured.append(str(request.url))
            return httpx.Response(200, json=payload)

        monkeypatch.setattr("bifrost.client._engine_socket_path", None)
        client = self._client()
        async_http = httpx.AsyncClient(
            base_url="http://external-api",
            transport=httpx.MockTransport(_handler),
        )
        client._http = async_http
        client._http_loop = asyncio.get_running_loop()
        try:
            assert await client._fetch_context() == payload
        finally:
            await async_http.aclose()
        assert captured == ["http://external-api/api/sdk/context"]
