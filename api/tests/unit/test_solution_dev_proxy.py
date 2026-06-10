import asyncio
import socket

import aiohttp
import httpx
from aiohttp import web

from bifrost.solution_dev.proxy import DevProxyConfig, build_dev_app


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _StubHost:
    def __init__(self, refs):
        self._refs = set(refs)
        self.last_call = None

    def has(self, ref):
        return ref in self._refs

    async def run(self, ref, params):
        self.last_call = (ref, params)
        return {"ran_local": ref, "params": params}


def _make_upstream(record):
    async def execute(request):
        record["execute_body"] = await request.json()
        return web.json_response({"ran_upstream": True})

    async def other(request):
        record["other_path"] = request.path
        record["other_org"] = request.headers.get("X-Bifrost-Org")
        return web.json_response({"upstream_other": True})

    async def ws_echo(request):
        record["ws_query"] = request.rel_url.query_string
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                await ws.send_str(f"echo:{msg.data}")
                break
        await ws.close()
        return ws

    async def ws_proto(request):
        ws = web.WebSocketResponse(protocols=("vite-hmr",))
        await ws.prepare(request)
        record["upstream_proto"] = ws.ws_protocol
        async for _ in ws:
            pass
        return ws

    async def ws_hold(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        record["upstream_connected"].set()
        async for _ in ws:
            pass
        record["upstream_closed"].set()
        return ws

    app = web.Application()
    app.router.add_post("/api/workflows/execute", execute)
    app.router.add_get("/ws/echo", ws_echo)
    app.router.add_get("/ws/proto", ws_proto)
    app.router.add_get("/ws/hold", ws_hold)
    app.router.add_route("*", "/api/{tail:.*}", other)
    return app


async def _serve(app, port):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    return runner


async def test_local_path_ref_runs_in_function_host():
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost({"functions/hello.py::main"})
    cfg = DevProxyConfig(upstream_url=f"http://127.0.0.1:{up_port}", token="t", app_id="A", org_id="O")
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                             json={"workflow_id": "functions/hello.py::main", "input_data": {"x": 1}, "app_id": "A"})
        assert r.status_code == 200
        assert r.json()["result"] == {"ran_local": "functions/hello.py::main", "params": {"x": 1}}
        assert host.last_call == ("functions/hello.py::main", {"x": 1})
        assert "execute_body" not in record  # never hit upstream
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


class _RaisingHost:
    def has(self, ref):
        return True

    async def run(self, ref, params):
        raise ValueError("boom in the workflow")


async def test_local_error_returns_200_with_error_field():
    # A local workflow exception must surface the real error to the SDK, which
    # reads `body.error` on a 200 (deployed contract); a non-200 would only show
    # statusText. So the proxy returns 200 + {"error": "...boom..."}.
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream({}), up_port)
    cfg = DevProxyConfig(upstream_url=f"http://127.0.0.1:{up_port}", token="t", app_id="A", org_id="O")
    dev_runner = await _serve(build_dev_app(cfg, _RaisingHost(), vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                             json={"workflow_id": "functions/boom.py::main", "input_data": {}})
        assert r.status_code == 200
        err = r.json()["error"]
        assert "boom in the workflow" in err
        assert "ValueError" in err  # includes the exception type + traceback
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


async def test_unknown_ref_proxies_to_upstream():
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost(set())
    cfg = DevProxyConfig(upstream_url=f"http://127.0.0.1:{up_port}", token="t", app_id="A", org_id="O")
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                             json={"workflow_id": "11111111-1111-1111-1111-111111111111", "input_data": {}, "app_id": "A"})
        assert r.status_code == 200
        assert r.json()["ran_upstream"] is True
        assert record["execute_body"]["app_id"] == "A"
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


async def test_other_api_path_proxies_with_org_header():
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost(set())
    cfg = DevProxyConfig(upstream_url=f"http://127.0.0.1:{up_port}", token="t", app_id="A", org_id="O")
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"http://127.0.0.1:{dev_port}/api/tables/foo")
        assert r.status_code == 200
        assert r.json()["upstream_other"] is True
        assert record["other_path"] == "/api/tables/foo"
        assert record["other_org"] == "O"
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


async def test_ws_upgrade_bridges_to_upstream():
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost(set())
    cfg = DevProxyConfig(upstream_url=f"http://127.0.0.1:{up_port}", token="t", app_id="A", org_id="O")
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                f"http://127.0.0.1:{dev_port}/ws/echo?channels=x&token=tok"
            ) as ws:
                await ws.send_str("ping")
                msg = await ws.receive()
                assert msg.type == aiohttp.WSMsgType.TEXT
                assert msg.data == "echo:ping"
        # rel_url (channels + token) is forwarded verbatim to the dev API.
        assert record["ws_query"] == "channels=x&token=tok"
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


async def test_ws_proxy_echoes_subprotocol():
    # Vite's HMR client connects with subprotocol "vite-hmr"; browsers MUST
    # fail the connection if the server doesn't select the requested
    # subprotocol, so the proxy has to echo it on the client handshake and
    # forward it upstream.
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost(set())
    cfg = DevProxyConfig(upstream_url=f"http://127.0.0.1:{up_port}", token="t", app_id="A", org_id="O")
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                f"http://127.0.0.1:{dev_port}/ws/proto", protocols=("vite-hmr",)
            ) as ws:
                assert ws.protocol == "vite-hmr"
        assert record["upstream_proto"] == "vite-hmr"
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


async def test_ws_proxy_closes_upstream_when_client_disconnects():
    # Half-close: when the browser side goes away (every page reload), the
    # proxy must tear down the upstream socket instead of leaking the pump,
    # the ClientSession, and the upstream connection forever.
    record = {
        "upstream_connected": asyncio.Event(),
        "upstream_closed": asyncio.Event(),
    }
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost(set())
    cfg = DevProxyConfig(upstream_url=f"http://127.0.0.1:{up_port}", token="t", app_id="A", org_id="O")
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(f"http://127.0.0.1:{dev_port}/ws/hold")
            await asyncio.wait_for(record["upstream_connected"].wait(), timeout=5)
            await ws.close()
            # Upstream must observe the close — no leaked half-open pump.
            await asyncio.wait_for(record["upstream_closed"].wait(), timeout=5)
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()
