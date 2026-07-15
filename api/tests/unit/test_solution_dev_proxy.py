import asyncio
import socket

import aiohttp
import httpx
import yarl
from aiohttp import web

from bifrost.solution_dev.function_host import (
    LocalWorkflowImportError,
    LocalWorkflowResolutionError,
)
from bifrost.solution_dev.proxy import DevProxyConfig, _join_upstream, build_dev_app


def test_join_upstream_keeps_trusted_authority():
    """A request path can never repoint the proxy at another host (partial SSRF).

    yarl normalizes the relative URL, so even authority-smuggling shapes
    (//evil, embedded @, an absolute scheme) keep the trusted base's host/port.
    """
    base = "http://127.0.0.1:8000"

    # Ordinary path + query: grafted onto the base verbatim.
    assert (
        _join_upstream(base, yarl.URL("/api/tables/foo?x=1"))
        == "http://127.0.0.1:8000/api/tables/foo?x=1"
    )

    # Authority-smuggling attempts must still resolve to the trusted host.
    for hostile in ["//evil.example/x", "/\\evil.example", "/@evil.example/x"]:
        out = yarl.URL(_join_upstream(base, yarl.URL(hostile)))
        assert out.host == "127.0.0.1" and out.port == 8000, (out, hostile)


def test_join_upstream_preserves_flag_style_query():
    """Vite flag queries (?raw, ?url, ?worker) must NOT become ?raw= etc.

    Reparsing the query via yarl's with_query() would turn the value-less flag
    into an empty-valued param, breaking `import x from './f.md?raw'` through
    the dev proxy. We pass the raw query string through verbatim.
    """
    base = "http://127.0.0.1:8000"
    assert _join_upstream(base, yarl.URL("/src/f.md?raw")) == "http://127.0.0.1:8000/src/f.md?raw"
    assert _join_upstream(base, yarl.URL("/src/f.ts?worker&inline")) == (
        "http://127.0.0.1:8000/src/f.ts?worker&inline"
    )
    # An ordinary key=value query is preserved too.
    assert _join_upstream(base, yarl.URL("/api/x?a=1&b=2")) == "http://127.0.0.1:8000/api/x?a=1&b=2"


def test_join_upstream_accepts_default_ports_without_rewriting_origin():
    """Default HTTP/HTTPS ports are same-origin whether explicit or implicit."""
    assert (
        _join_upstream("https://bifrost.gocovi.com", yarl.URL("/api/auth/me"))
        == "https://bifrost.gocovi.com/api/auth/me"
    )
    assert (
        _join_upstream("https://bifrost.gocovi.com:443", yarl.URL("/api/auth/me"))
        == "https://bifrost.gocovi.com/api/auth/me"
    )
    assert (
        _join_upstream("http://localhost", yarl.URL("/api/auth/me"))
        == "http://localhost/api/auth/me"
    )
    assert (
        _join_upstream("http://localhost:80", yarl.URL("/api/auth/me"))
        == "http://localhost/api/auth/me"
    )


def test_join_upstream_retains_non_default_ports():
    assert (
        _join_upstream("http://localhost:8080", yarl.URL("/api/auth/me"))
        == "http://localhost:8080/api/auth/me"
    )
    assert (
        _join_upstream("https://example.test:8443", yarl.URL("/api/auth/me"))
        == "https://example.test:8443/api/auth/me"
    )


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _StubHost:
    """Stub of FunctionHost's resolver surface (resolve/refs/run)."""

    def __init__(self, refs, aliases=None, error=None):
        self._refs = set(refs)
        self._aliases = dict(aliases or {})
        self._error = error  # raised by resolve() when set
        self.last_call = None

    def refs(self):
        return sorted(self._refs)

    def resolve(self, ref):
        if self._error is not None:
            raise self._error
        if ref in self._refs:
            return ref
        return self._aliases.get(ref)

    async def run(self, ref, params):
        self.last_call = (ref, params)
        return {"ran_local": ref, "params": params}


def _make_upstream(record):
    async def execute(request):
        record["execute_body"] = await request.json()
        record["execute_headers"] = dict(request.headers)
        return web.json_response({"ran_upstream": True})

    async def other(request):
        record["other_path"] = request.path
        record["other_query"] = request.rel_url.query_string
        record["other_org"] = request.headers.get("X-Bifrost-Org")
        record["other_accept_encoding"] = request.headers.get("Accept-Encoding")
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


def _make_auth_refresh_upstream(record):
    async def auth_me(request):
        auth = request.headers.get("Authorization")
        record.setdefault("auth_headers", []).append(auth)
        if auth == "Bearer fresh-token":
            return web.json_response({"email": "dev@gobifrost.com", "name": "Dev User"})
        return web.json_response({"detail": "Not authenticated"}, status=401)

    async def branding(_request):
        record["branding_requested"] = True
        return web.json_response(
            {
                "application_name": "Covi Ops",
                "rectangle_logo_url": "/api/branding/logo/rectangle",
                "square_logo_url": "/api/branding/logo/square",
                "primary_color": "#aa3366",
                "terminology": {},
            }
        )

    app = web.Application()
    app.router.add_get("/api/auth/me", auth_me)
    app.router.add_get("/api/branding", branding)
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
            responses = [
                await c.post(
                    f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                    json={
                        "workflow_id": "functions/hello.py::main",
                        "input_data": {"x": 1},
                        "app_id": "A",
                        **({"sync": True} if legacy else {}),
                    },
                )
                for legacy in (False, True)
            ]
        for response in responses:
            assert response.status_code == 200
            body = response.json()
            assert body["execution_id"].startswith("solution-start-")
            assert body["is_transient"] is True
            assert body["status"] == "Success"
            assert body["result"] == {
                "ran_local": "functions/hello.py::main",
                "params": {"x": 1},
            }
        assert host.last_call == ("functions/hello.py::main", {"x": 1})
        assert "execute_body" not in record  # never hit upstream
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


class _RaisingHost:
    def resolve(self, ref):
        return ref

    async def run(self, ref, params):
        raise ValueError("boom in the workflow")


async def test_local_error_returns_inline_terminal_failure():
    # A local workflow exception must surface the real error to the SDK, which
    # reads `body.error` on a 200 (deployed contract); a non-200 would only show
    # statusText. The response is terminal so the streaming SDK does not poll
    # an execution that exists only inside this process. A pre-streaming SDK
    # still rejects the unchanged `error` field.
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream({}), up_port)
    cfg = DevProxyConfig(upstream_url=f"http://127.0.0.1:{up_port}", token="t", app_id="A", org_id="O")
    dev_runner = await _serve(build_dev_app(cfg, _RaisingHost(), vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                             json={"workflow_id": "functions/boom.py::main", "input_data": {}})
        assert r.status_code == 200
        body = r.json()
        assert body["execution_id"].startswith("solution-start-")
        assert body["is_transient"] is True
        assert body["status"] == "Failed"
        err = body["error"]
        assert "boom in the workflow" in err
        assert "ValueError" in err  # includes the exception type + traceback
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


async def test_local_name_ref_resolves_before_execute():
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost(
        {"functions/preview.py::recipients"},
        aliases={"Preview Recipients": "functions/preview.py::recipients"},
    )
    cfg = DevProxyConfig(
        upstream_url=f"http://127.0.0.1:{up_port}",
        token="t", app_id="A", org_id="O", solution_id="S",
    )
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                json={"workflow_id": "Preview Recipients", "input_data": {"x": 1}, "app_id": "A"},
            )
        assert r.status_code == 200
        assert r.json()["result"] == {
            "ran_local": "functions/preview.py::recipients",
            "params": {"x": 1},
        }
        assert "execute_body" not in record
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


async def test_resolution_errors_surface_as_200_error_body():
    # Ambiguity and import failures must be VISIBLE in the app: useWorkflow
    # renders body.error only on HTTP 200 (non-200 shows bare statusText).
    for error, needle in [
        (LocalWorkflowResolutionError("workflow name 'Dup' is ambiguous"), "ambiguous"),
        (LocalWorkflowImportError("local workflow 'x' failed to import: boom"), "boom"),
    ]:
        record = {}
        up_port, dev_port = _free_port(), _free_port()
        up_runner = await _serve(_make_upstream(record), up_port)
        host = _StubHost(set(), error=error)
        cfg = DevProxyConfig(
            upstream_url=f"http://127.0.0.1:{up_port}",
            token="t", app_id="A", org_id="O", solution_id="S",
            global_repo_access=True,
        )
        dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(
                    f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                    json={"workflow_id": "Dup", "input_data": {}},
                )
            assert r.status_code == 200
            body = r.json()
            assert body["execution_id"].startswith("solution-start-")
            assert body["is_transient"] is True
            assert body["status"] == "Failed"
            assert needle in body["error"]
            assert "execute_body" not in record  # even with global access: no proxy
        finally:
            await dev_runner.cleanup()
            await up_runner.cleanup()


async def test_unknown_ref_without_global_access_errors_locally():
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost({"functions/hello.py::main"})
    cfg = DevProxyConfig(
        upstream_url=f"http://127.0.0.1:{up_port}",
        token="t", app_id="A", org_id="O", solution_id="S",
        global_repo_access=False,
    )
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                json={"workflow_id": "Shared Global Workflow", "input_data": {}, "app_id": "A"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["execution_id"].startswith("solution-start-")
        assert body["is_transient"] is True
        assert body["status"] == "Failed"
        assert "not found" in body["error"]
        assert "functions/hello.py::main" in body["error"]  # lists known refs
        assert "global_repo_access" in body["error"]
        assert "execute_body" not in record
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


async def test_unknown_ref_with_global_access_proxies_without_scope_signals():
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost(set())
    cfg = DevProxyConfig(
        upstream_url=f"http://127.0.0.1:{up_port}",
        token="t", app_id="A", org_id="O", solution_id="S",
        global_repo_access=True,
    )
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                json={
                    "workflow_id": "Shared Global Workflow",
                    "input_data": {},
                    "app_id": "A",
                    "form_id": "F",
                    "solution_id": "client-sent",
                },
            )
        assert r.status_code == 200
        assert r.json()["ran_upstream"] is True
        body = record["execute_body"]
        assert body["workflow_id"] == "Shared Global Workflow"
        # EVERY signal the server derives install scope from must be gone —
        # for capture-born installs these ids exist server-side and would
        # resolve the CLOUD copy of this Solution's workflows.
        assert "solution_id" not in body
        assert "app_id" not in body
        assert "form_id" not in body
        headers = {k.lower(): v for k, v in record["execute_headers"].items()}
        assert "x-bifrost-app" not in headers
        assert headers["x-bifrost-org"] == "O"          # org scope stays
        assert headers["authorization"] == "Bearer t"   # auth stays
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


async def test_local_uuid_ref_warns_once(capsys):
    uuid_ref = "11111111-1111-1111-1111-111111111111"
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost({"functions/a.py::main"}, aliases={uuid_ref: "functions/a.py::main"})
    cfg = DevProxyConfig(
        upstream_url=f"http://127.0.0.1:{up_port}",
        token="t", app_id="A", org_id="O",
    )
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r1 = await c.post(
                f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                json={"workflow_id": uuid_ref, "input_data": {}},
            )
            r2 = await c.post(
                f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                json={"workflow_id": uuid_ref, "input_data": {}},
            )
        assert r1.status_code == 200 and r2.status_code == 200
        err = capsys.readouterr().err
        assert err.count("manifest UUID") == 1  # warned exactly once
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


async def test_other_api_path_proxies_with_org_header():
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost(set())
    cfg = DevProxyConfig(
        upstream_url=f"http://127.0.0.1:{up_port}",
        token="t",
        app_id="A",
        org_id="O",
        solution_id="S",
    )
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"http://127.0.0.1:{dev_port}/api/tables/foo?limit=10")
        assert r.status_code == 200
        assert r.json()["upstream_other"] is True
        assert record["other_path"] == "/api/tables/foo"
        assert record["other_query"] == "limit=10&solution=S"
        assert record["other_org"] == "O"
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


async def test_browser_accept_encoding_is_not_forwarded_upstream():
    # Browsers advertise encodings (br, zstd) that httpx may not be able to
    # decode. If the proxy forwards the browser's Accept-Encoding, upstream may
    # respond with one of those, httpx passes the compressed bytes through, and
    # _passthrough_headers drops Content-Encoding — so the browser gets
    # compressed bytes labeled as plain JSON and fails to parse. The proxy must
    # strip the browser's Accept-Encoding and let httpx negotiate for itself.
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost(set())
    cfg = DevProxyConfig(
        upstream_url=f"http://127.0.0.1:{up_port}",
        token="t",
        app_id="A",
        org_id="O",
        solution_id="S",
    )
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"http://127.0.0.1:{dev_port}/api/auth/me",
                headers={"Accept-Encoding": "br, gzip, zstd, x-browser-sentinel"},
            )
        assert r.status_code == 200
        assert record["other_accept_encoding"] == "identity"
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


async def test_api_proxy_refreshes_cli_token_once_after_401():
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_auth_refresh_upstream(record), up_port)
    host = _StubHost(set())

    async def refresh_token(observed_token):
        record["refresh_called"] = observed_token
        return "fresh-token"

    cfg = DevProxyConfig(
        upstream_url=f"http://127.0.0.1:{up_port}",
        token="stale-token",
        app_id="A",
        org_id="O",
        refresh_token=refresh_token,
    )
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"http://127.0.0.1:{dev_port}/api/auth/me")
        assert r.status_code == 200
        assert r.json()["email"] == "dev@gobifrost.com"
        assert record["refresh_called"] == "stale-token"
        assert record["auth_headers"] == ["Bearer stale-token", "Bearer fresh-token"]
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


async def test_api_proxy_returns_dev_auth_expired_json_when_refresh_fails():
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_auth_refresh_upstream(record), up_port)
    host = _StubHost(set())

    async def refresh_token(observed_token):
        record["refresh_called"] = observed_token
        return None

    cfg = DevProxyConfig(
        upstream_url=f"http://127.0.0.1:{up_port}",
        token="stale-token",
        app_id="A",
        org_id="O",
        refresh_token=refresh_token,
    )
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"http://127.0.0.1:{dev_port}/api/auth/me")
        assert r.status_code == 401
        assert r.headers["x-bifrost-dev-auth"] == "expired"
        assert r.json() == {
            "error": "bifrost_dev_auth_expired",
            "detail": "Your CLI token has expired. Restart `bifrost solution start`.",
        }
        assert record["refresh_called"] == "stale-token"
        assert record["auth_headers"] == ["Bearer stale-token"]
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()


async def test_vite_proxy_serves_branded_auth_expired_page_after_api_auth_failure():
    record = {}
    up_port, vite_port, dev_port = _free_port(), _free_port(), _free_port()
    up_runner = await _serve(_make_auth_refresh_upstream(record), up_port)

    async def index(_request):
        return web.Response(text="<html><body>Vite app</body></html>", content_type="text/html")

    async def logo(_request):
        return web.Response(text="<svg></svg>", content_type="image/svg+xml")

    vite = web.Application()
    vite.router.add_get("/", index)
    vite.router.add_get("/logo.svg", logo)
    vite_runner = await _serve(vite, vite_port)
    host = _StubHost(set())

    async def refresh_token(_observed_token):
        return None

    cfg = DevProxyConfig(
        upstream_url=f"http://127.0.0.1:{up_port}",
        token="stale-token",
        app_id="A",
        org_id="O",
        refresh_token=refresh_token,
    )
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url=f"http://127.0.0.1:{vite_port}"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            api = await c.get(f"http://127.0.0.1:{dev_port}/api/auth/me")
            page = await c.get(f"http://127.0.0.1:{dev_port}/", headers={"Accept": "text/html"})
            logo_resp = await c.get(
                f"http://127.0.0.1:{dev_port}/logo.svg",
                headers={"Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"},
            )
        assert api.status_code == 401
        assert page.status_code == 401
        assert logo_resp.status_code == 200
        assert logo_resp.headers["content-type"].startswith("image/svg+xml")
        assert logo_resp.text == "<svg></svg>"
        assert record["branding_requested"] is True
        assert "<title>Covi Ops Dev Auth Expired</title>" in page.text
        assert "Something went wrong" in page.text
        assert "What you can do:" in page.text
        assert "/api/branding/logo/square" not in page.text
        assert "/api/branding/logo/rectangle" not in page.text
        assert "#aa3366" in page.text
        assert "Your CLI token has expired" in page.text
        assert "bifrost solution start" in page.text
        assert "Vite app" not in page.text
    finally:
        await dev_runner.cleanup()
        await vite_runner.cleanup()
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


async def test_code_only_request_forwards_upstream_with_scope():
    # Inline `code` execution has no workflow ref to resolve locally — it must
    # forward even with global_repo_access off, keeping the install context.
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost(set())
    cfg = DevProxyConfig(
        upstream_url=f"http://127.0.0.1:{up_port}",
        token="t", app_id="A", org_id="O", solution_id="S",
        global_repo_access=False,
    )
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url="http://127.0.0.1:1"), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"http://127.0.0.1:{dev_port}/api/workflows/execute",
                json={"code": "print('hi')", "input_data": {}},
            )
        assert r.status_code == 200
        assert r.json()["ran_upstream"] is True
        assert record["execute_body"]["solution_id"] == "S"
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


async def test_vite_proxy_returns_502_when_vite_is_down():
    """A dead Vite child must surface as an explained 502, not a bare 500 —
    the API proxy handler already does this; the vite handler didn't (issue #460)."""
    record = {}
    up_port, dev_port = _free_port(), _free_port()
    up_runner = await _serve(_make_upstream(record), up_port)
    host = _StubHost(set())
    cfg = DevProxyConfig(
        upstream_url=f"http://127.0.0.1:{up_port}",
        token="t", app_id="A", org_id="O",
    )
    dead_vite = f"http://127.0.0.1:{_free_port()}"
    dev_runner = await _serve(build_dev_app(cfg, host, vite_url=dead_vite), dev_port)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"http://127.0.0.1:{dev_port}/")
        assert r.status_code == 502
        assert "vite" in r.json()["detail"].lower()
    finally:
        await dev_runner.cleanup()
        await up_runner.cleanup()
