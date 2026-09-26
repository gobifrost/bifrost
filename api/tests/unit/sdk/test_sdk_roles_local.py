"""Engine-request transport for the ``roles`` facade.

The migrated ``roles`` facade rides ``BifrostClient.engine_request`` with the
exact HTTP method/path/body and maps statuses to the same public exceptions
(``Role`` objects, ``user_ids``/``form_ids`` lists, ``ValueError`` on 404)
with no network fallback after a failed local call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest


def _role_body(role_id=None, name="facade-role"):
    rid = role_id or str(uuid4())
    return {
        "id": rid,
        "name": name,
        "description": "d",
        "permissions": {},
        "created_by": "engine@bifrost.internal",
        "created_at": "2026-09-25T00:00:00+00:00",
        "updated_at": "2026-09-25T00:00:00+00:00",
    }


def _response(method, status, payload=None):
    request = httpx.Request(method, "http://engine.local")
    return httpx.Response(status, json=payload, request=request)


class TestEngineRequestRolesFacade:
    """Gate C5e: the migrated roles facade rides ``engine_request``.

    Each method sends the exact HTTP method/path/body the external path
    used, so the socket-served and network calls stay identical; statuses
    map to the same public exceptions with no silent fallback.
    """

    def _client(self, *responses):
        client = AsyncMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    @pytest.mark.asyncio
    async def test_all_nine_methods_use_exact_http_calls(self):
        from bifrost.roles import roles as roles_facade

        body = _role_body()
        client = self._client(
            _response("POST", 201, body),
            _response("GET", 200, body),
            _response("GET", 200, [body]),
            _response("PATCH", 200, body),
            _response("DELETE", 204, None),
            _response("GET", 200, {"user_ids": ["u-1"], "users": [], "total": 1}),
            _response("GET", 200, {"form_ids": ["f-1"]}),
            _response("POST", 204, None),
            _response("POST", 204, None),
        )
        with patch("bifrost.roles.get_client", return_value=client):
            created = await roles_facade.create(body["name"], description="d")
            assert created.id == body["id"]
            args, kwargs = client.engine_request.await_args_list[0]
            assert args == ("POST", "/api/roles")
            assert kwargs["json"] == {
                "name": body["name"],
                "description": "d",
                "is_active": True,
            }

            assert (await roles_facade.get(body["id"])).id == body["id"]
            args, _ = client.engine_request.await_args_list[1]
            assert args == ("GET", f"/api/roles/{body['id']}")

            listed = await roles_facade.list()
            assert [r.id for r in listed] == [body["id"]]
            args, _ = client.engine_request.await_args_list[2]
            assert args == ("GET", "/api/roles")

            updated = await roles_facade.update(body["id"], description="d2")
            assert updated.id == body["id"]
            args, kwargs = client.engine_request.await_args_list[3]
            assert args == ("PATCH", f"/api/roles/{body['id']}")
            assert kwargs["json"] == {"description": "d2"}

            assert await roles_facade.delete(body["id"]) is None
            args, _ = client.engine_request.await_args_list[4]
            assert args == ("DELETE", f"/api/roles/{body['id']}")

            assert await roles_facade.list_users(body["id"]) == ["u-1"]
            args, _ = client.engine_request.await_args_list[5]
            assert args == ("GET", f"/api/roles/{body['id']}/users")

            assert await roles_facade.list_forms(body["id"]) == ["f-1"]
            args, _ = client.engine_request.await_args_list[6]
            assert args == ("GET", f"/api/roles/{body['id']}/forms")

            assert await roles_facade.assign_users(body["id"], ["u-1"]) is None
            args, kwargs = client.engine_request.await_args_list[7]
            assert args == ("POST", f"/api/roles/{body['id']}/users")
            assert kwargs["json"] == {"user_ids": ["u-1"]}

            assert await roles_facade.assign_forms(body["id"], ["f-1"]) is None
            args, kwargs = client.engine_request.await_args_list[8]
            assert args == ("POST", f"/api/roles/{body['id']}/forms")
            assert kwargs["json"] == {"form_ids": ["f-1"]}

    @pytest.mark.asyncio
    async def test_status_mapping_matches_http(self):
        from bifrost.client import BifrostAPIError, BifrostAuthorizationError
        from bifrost.roles import roles as roles_facade

        request = httpx.Request("GET", "http://engine.local/api/roles/x")
        for call in (
            lambda m: m.get("missing"),
            lambda m: m.update("missing", description="x"),
            lambda m: m.delete("missing"),
            lambda m: m.list_users("missing"),
            lambda m: m.list_forms("missing"),
            lambda m: m.assign_users("missing", ["u-1"]),
            lambda m: m.assign_forms("missing", ["f-1"]),
        ):
            client = self._client(
                httpx.Response(404, json={"detail": "x"}, request=request)
            )
            with patch("bifrost.roles.get_client", return_value=client):
                with pytest.raises(ValueError, match="Role not found"):
                    await call(roles_facade)

        # 403 (platform-admin gate) and 422 (DTO validation) surface as the
        # same public exceptions as the external path.
        client = self._client(
            httpx.Response(403, json={"detail": "denied"}, request=request),
            httpx.Response(422, json={"detail": "bad"}, request=request),
        )
        with patch("bifrost.roles.get_client", return_value=client):
            with pytest.raises(BifrostAuthorizationError):
                await roles_facade.create("denied")
            with pytest.raises(BifrostAPIError):
                await roles_facade.list()
