"""Engine-request transport for the ``users`` facade.

The migrated ``users`` facade rides ``BifrostClient.engine_request`` with the
exact HTTP method/path/body and maps statuses to the same public exceptions
(``UserPublic`` objects, ``None``/``ValueError`` on 404) with no network
fallback after a failed local call. The ``org_id``/``scope`` regression
holds: the SDK sends its ``org_id`` as the ``scope`` query parameter the
handler filters on.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest


def _user_body(user_id=None, email="facade@example.com"):
    uid = user_id or str(uuid4())
    return {
        "id": uid,
        "email": email,
        "name": "Facade User",
        "is_active": True,
        "is_superuser": False,
        "is_verified": True,
        "is_registered": False,
        "organization_id": None,
        "mfa_enabled": False,
        "created_at": "2026-09-25T00:00:00+00:00",
        "updated_at": "2026-09-25T00:00:00+00:00",
    }


def _response(method, status, payload=None):
    request = httpx.Request(method, "http://engine.local/api/users")
    return httpx.Response(status, json=payload, request=request)


class TestEngineRequestUsersFacade:
    """Gate C5d: the migrated users facade rides ``engine_request``.

    Each method sends the exact HTTP method/path/body the external path
    used. The ``org_id``/``scope`` regression holds: the SDK sends its
    ``org_id`` as the ``scope`` query parameter the handler filters on.
    Statuses map to the same public exceptions with no silent fallback.
    """

    def _client(self, *responses):
        client = AsyncMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    @pytest.mark.asyncio
    async def test_all_five_methods_use_exact_http_calls(self):
        from bifrost.users import users as users_facade

        body = _user_body()
        client = self._client(
            _response("GET", 200, [body]),
            _response("GET", 200, [body]),
            _response("POST", 201, body),
            _response("GET", 200, body),
            _response("PATCH", 200, body),
            _response("DELETE", 204, None),
        )
        with patch("bifrost.users.get_client", return_value=client):
            listed = await users_facade.list(org_id="org-123")
            assert [u.id for u in listed] == [body["id"]]
            args, kwargs = client.engine_request.await_args_list[0]
            assert args == ("GET", "/api/users")
            assert kwargs["params"] == {"scope": "org-123"}

            await users_facade.list(org_id="org-123", include_inactive=True)
            args, kwargs = client.engine_request.await_args_list[1]
            assert kwargs["params"] == {
                "scope": "org-123",
                "include_inactive": "true",
            }

            created = await users_facade.create(
                "n@example.com", "N", org_id="org-123"
            )
            assert created.id == body["id"]
            args, kwargs = client.engine_request.await_args_list[2]
            assert args == ("POST", "/api/users")
            assert kwargs["json"] == {
                "email": "n@example.com",
                "name": "N",
                "is_superuser": False,
                "is_active": True,
                "organization_id": "org-123",
            }

            assert (await users_facade.get(body["id"])).id == body["id"]
            args, _ = client.engine_request.await_args_list[3]
            assert args == ("GET", f"/api/users/{body['id']}")

            updated = await users_facade.update(body["id"], name="New")
            assert updated.id == body["id"]
            args, kwargs = client.engine_request.await_args_list[4]
            assert args == ("PATCH", f"/api/users/{body['id']}")
            assert kwargs["json"] == {"name": "New"}

            assert await users_facade.delete(body["id"]) is True
            args, _ = client.engine_request.await_args_list[5]
            assert args == ("DELETE", f"/api/users/{body['id']}")

    @pytest.mark.asyncio
    async def test_status_mapping_matches_http(self):
        from bifrost.client import BifrostAPIError, BifrostAuthorizationError
        from bifrost.users import users as users_facade

        request = httpx.Request("GET", "http://engine.local/api/users/x")
        # ``get`` returns None on 404; update/delete raise ValueError.
        client = self._client(
            httpx.Response(404, json={"detail": "x"}, request=request),
            httpx.Response(404, json={"detail": "x"}, request=request),
            httpx.Response(404, json={"detail": "x"}, request=request),
        )
        with patch("bifrost.users.get_client", return_value=client):
            assert await users_facade.get("missing") is None
            with pytest.raises(ValueError, match="User not found"):
                await users_facade.update("missing", name="x")
            with pytest.raises(ValueError, match="User not found"):
                await users_facade.delete("missing")

        # 403 (platform-admin gate) and 422 (DTO validation) surface as the
        # same public exceptions as the external path.
        client = self._client(
            httpx.Response(403, json={"detail": "denied"}, request=request),
            httpx.Response(422, json={"detail": "bad"}, request=request),
        )
        with patch("bifrost.users.get_client", return_value=client):
            with pytest.raises(BifrostAuthorizationError):
                await users_facade.create("d@example.com", "D")
            with pytest.raises(BifrostAPIError):
                await users_facade.list()
