"""Local development provenance survives every supported client transport."""
import asyncio
import time
from uuid import uuid4

import httpx
import pytest

from bifrost import tables
from bifrost.client import BifrostAPIError, BifrostClient, _local_dev_client
from bifrost.solution_dev.session import DEV_CONTEXT_HEADER, SolutionDevSession


@pytest.mark.asyncio
async def test_dev_context_renews_without_replacing_user_credentials(monkeypatch):
    solution_id, manifest_app, installed_app = map(str, (uuid4(), uuid4(), uuid4()))
    requests = []
    issued = 0

    def respond(request):
        nonlocal issued
        requests.append(request)
        if request.url.path.endswith("/dev-context"):
            assert DEV_CONTEXT_HEADER not in request.headers
            issued += 1
            return httpx.Response(200, json={
                "token": f"signed-context-{issued}",
                "expires_at": time.time() + 600,
                "solution_id": solution_id,
                "organization_id": None,
                "app_id": installed_app,
            })
        return httpx.Response(200, json={"ok": True})

    client = BifrostClient("https://selected.test", "real-user-token")
    http = httpx.AsyncClient(
        base_url=client.api_url, transport=httpx.MockTransport(respond),
        headers={"Authorization": "Bearer real-user-token"},
    )
    monkeypatch.setattr(client, "_get_async_client", lambda: http)
    client._sync_http.close()
    client._sync_http = httpx.Client(
        base_url=client.api_url, transport=httpx.MockTransport(respond),
        headers={"Authorization": "Bearer real-user-token"},
    )
    session = SolutionDevSession(client, solution_id, manifest_app)
    client._solution_dev_session = session
    await client.engine_request("POST", "/api/tables/items/documents/query", json={})
    assert session.app_id == installed_app
    await client.request("DELETE", "/api/tables/items/documents/batch", json={"ids": []})
    async with client.stream("POST", "/api/ai/chat", json={}) as response:
        assert response.status_code == 200
    session._expires_at = 0
    client.get_sync("/api/config/items")
    async with client.engine_stream("POST", "/api/ai/chat", json={}) as response:
        assert response.status_code == 200
    assert issued == 2
    assert all(r.headers["Authorization"] == "Bearer real-user-token" for r in requests)
    data_requests = [r for r in requests if not r.url.path.endswith("/dev-context")]
    assert all(r.headers.get(DEV_CONTEXT_HEADER, "").startswith("signed-context-") for r in data_requests)
    assert data_requests[-1].headers[DEV_CONTEXT_HEADER] == "signed-context-2"
    await http.aclose()
    await client.close()


@pytest.mark.asyncio
async def test_context_renewal_can_refresh_user_before_the_data_request(monkeypatch):
    solution_id = str(uuid4())
    requests = []
    clients = []
    refreshes = 0
    original_http_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        if request.headers["Authorization"] == "Bearer expired-user-token":
            return httpx.Response(401)
        if request.url.path.endswith("/dev-context"):
            return httpx.Response(200, json={
                "token": "signed-context", "expires_at": time.time() + 600,
                "solution_id": solution_id, "organization_id": None, "app_id": str(uuid4()),
            })
        assert request.headers[DEV_CONTEXT_HEADER] == "signed-context"
        return httpx.Response(200, json={})

    def make_http(**kwargs):
        http = original_http_client(**kwargs, transport=httpx.MockTransport(respond))
        clients.append(http)
        return http

    monkeypatch.setattr("bifrost.client.httpx.AsyncClient", make_http)
    client = BifrostClient("https://selected.test", "expired-user-token")

    async def refresh(observed):
        nonlocal refreshes
        refreshes += 1
        assert observed == "expired-user-token"
        client.install_access_token("renewed-user-token")
        return True

    monkeypatch.setattr(client, "_refresh_and_update", refresh)
    client._solution_dev_session = SolutionDevSession(client, solution_id, str(uuid4()))
    try:
        response = await client.get("/api/tables/items/documents/count")
        assert response.status_code == 200
        assert refreshes == 1
        assert requests[-1].headers["Authorization"] == "Bearer renewed-user-token"
        assert len(requests) == 3
    finally:
        await client.close()
        for http in clients:
            await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["query", "count"])
async def test_unresolved_local_table_is_not_reported_as_empty(monkeypatch, operation):
    client = BifrostClient("https://selected.test", "user-token")
    client._solution_dev_session = SolutionDevSession(client, str(uuid4()), str(uuid4()))

    async def denied(*args, **kwargs):
        return httpx.Response(404, json={"detail": "Table not found"}, request=httpx.Request("POST", client.api_url))

    monkeypatch.setattr(client, "engine_request", denied)
    token = _local_dev_client.set(client)
    try:
        with pytest.raises(BifrostAPIError, match="not an empty table"):
            await getattr(tables, operation)("items")
    finally:
        _local_dev_client.reset(token)
        await client.close()


@pytest.mark.asyncio
async def test_local_function_retains_selected_client_and_restores_context(tmp_path):
    from bifrost.solution_dev.function_host import FunctionHost

    source = tmp_path / "local.py"
    source.write_text(
        "from bifrost import workflow\n"
        "from bifrost.client import get_client\n"
        "@workflow\n"
        "async def run():\n"
        "    return get_client().api_url\n"
    )
    selected = BifrostClient("https://selected.test", "user-token")
    other = BifrostClient("https://other.test", "other-token")
    host = FunctionHost(tmp_path, client=selected)
    host.reload()
    token = _local_dev_client.set(other)
    try:
        results = await asyncio.gather(*(host.run("local.py::run", {}) for _ in range(2)))
        assert results == [selected.api_url, selected.api_url]
        assert _local_dev_client.get() is other
    finally:
        _local_dev_client.reset(token)
        await selected.close()
        await other.close()
