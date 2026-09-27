"""Migrated ``bifrost.organizations`` facade tests over ``engine_request``.

Each method sends the exact HTTP method/path/body the external path used;
statuses map to the same public exceptions with no silent fallback.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest


def _org_body(org_id=None, name="Facade Org"):
    uid = org_id or str(uuid4())
    return {
        "id": uid,
        "name": name,
        "domain": None,
        "is_active": True,
        "is_provider": False,
        "settings": {},
        "created_at": "2026-09-25T00:00:00+00:00",
        "created_by": "engine@bifrost.internal",
        "updated_at": "2026-09-25T00:00:00+00:00",
    }


def _response(method, status, payload=None):
    request = httpx.Request(method, "http://engine.local")
    return httpx.Response(status, json=payload, request=request)


class TestEngineRequestOrganizationsFacade:
    """Gate C5d: the migrated organizations facade rides ``engine_request``.

    Each method sends the exact HTTP method/path/body the external path
    used, so the socket-served and network calls stay identical; statuses
    map to the same public exceptions with no silent fallback.
    """

    def _client(self, *responses):
        client = AsyncMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    @pytest.mark.asyncio
    async def test_all_five_methods_use_exact_http_calls(self):
        from bifrost.organizations import organizations as organizations_facade

        body = _org_body()
        client = self._client(
            _response("POST", 201, body),
            _response("GET", 200, body),
            _response("GET", 200, [body]),
            _response("PATCH", 200, body),
            _response("DELETE", 204, None),
        )
        with patch("bifrost.organizations.get_client", return_value=client):
            created = await organizations_facade.create(
                body["name"], domain="acme.com"
            )
            assert created.id == body["id"]
            args, kwargs = client.engine_request.await_args_list[0]
            assert args == ("POST", "/api/organizations")
            assert kwargs["json"] == {
                "name": body["name"],
                "domain": "acme.com",
                "is_active": True,
            }

            assert (await organizations_facade.get(body["id"])).id == body["id"]
            args, _ = client.engine_request.await_args_list[1]
            assert args == ("GET", f"/api/organizations/{body['id']}")

            listed = await organizations_facade.list()
            assert [o.id for o in listed] == [body["id"]]
            args, _ = client.engine_request.await_args_list[2]
            assert args == ("GET", "/api/organizations")

            updated = await organizations_facade.update(body["id"], name="New")
            assert updated.id == body["id"]
            args, kwargs = client.engine_request.await_args_list[3]
            assert args == ("PATCH", f"/api/organizations/{body['id']}")
            assert kwargs["json"] == {"name": "New"}

            assert await organizations_facade.delete(body["id"]) is True
            args, _ = client.engine_request.await_args_list[4]
            assert args == ("DELETE", f"/api/organizations/{body['id']}")

    @pytest.mark.asyncio
    async def test_status_mapping_matches_http(self):
        from bifrost.client import BifrostAPIError, BifrostAuthorizationError
        from bifrost.organizations import organizations as organizations_facade

        request = httpx.Request("GET", "http://engine.local/api/organizations/x")
        for call in (
            lambda m: m.get("missing"),
            lambda m: m.update("missing", name="x"),
            lambda m: m.delete("missing"),
        ):
            client = self._client(
                httpx.Response(404, json={"detail": "x"}, request=request)
            )
            with patch("bifrost.organizations.get_client", return_value=client):
                with pytest.raises(ValueError, match="Organization not found"):
                    await call(organizations_facade)

        # 403 (platform-admin gate) and 422 (DTO validation) surface as the
        # same public exceptions as the external path.
        client = self._client(
            httpx.Response(403, json={"detail": "denied"}, request=request),
            httpx.Response(422, json={"detail": "bad"}, request=request),
        )
        with patch("bifrost.organizations.get_client", return_value=client):
            with pytest.raises(BifrostAuthorizationError):
                await organizations_facade.create("denied")
            with pytest.raises(BifrostAPIError):
                await organizations_facade.list()
