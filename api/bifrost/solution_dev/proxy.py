"""The single-origin local dev server for `bifrost solution start`.

Routes:
  POST /api/workflows/execute  → local FunctionHost when the path::fn ref is one
                                 of THIS workspace's functions; else upstream.
  /api/*                       → reverse-proxy to the dev API (data-plane).
  /ws/*  (and /api/* upgrades) → bridge websockets to the dev API (realtime).
  everything else              → reverse-proxy to the Vite dev server (the app),
                                 including Vite's own HMR websocket.

The upstream proxy injects the CLI token (Authorization) and the resolved org
(X-Bifrost-Org) so data-plane calls run under the chosen --org, matching deployed.

WebSockets are NOT given the injected Authorization header: the browser
authenticates the realtime socket via cookies or a `token` query param (see
client/src/services/websocket.ts). The `token` query param rides along in
`rel_url`; cookies are forwarded explicitly on the upstream handshake.
Requested subprotocols (e.g. Vite's `vite-hmr`) are echoed back to the client
and forwarded upstream — browsers fail the connection otherwise. When either
side closes, the bridge tears down both sockets (no half-open pump leaks).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import aiohttp
import httpx
from aiohttp import web

# Hop-by-hop headers we must not forward when reverse-proxying.
_STRIP = {"host", "content-length", "transfer-encoding", "connection", "keep-alive"}


@dataclass(frozen=True)
class DevProxyConfig:
    upstream_url: str   # the dev API, e.g. http://localhost:37791
    token: str          # CLI access token
    app_id: str         # chosen app's manifest UUID
    org_id: str | None  # resolved --org (or None → caller's default org)


# Typed app keys (avoid aiohttp's NotAppKeyWarning for plain-string keys).
_CFG = web.AppKey("cfg", DevProxyConfig)
_HOST = web.AppKey("host", object)
_VITE = web.AppKey("vite_url", str)
_HTTP = web.AppKey("http", httpx.AsyncClient)


def build_dev_app(cfg: DevProxyConfig, host, vite_url: str) -> web.Application:
    app = web.Application()
    app[_CFG] = cfg
    app[_HOST] = host
    app[_VITE] = vite_url.rstrip("/")
    app[_HTTP] = httpx.AsyncClient(timeout=120.0)

    app.router.add_post("/api/workflows/execute", _execute_handler)
    app.router.add_route("*", "/ws/{tail:.*}", _ws_handler)
    app.router.add_route("*", "/api/{tail:.*}", _api_proxy_handler)
    app.router.add_route("*", "/{tail:.*}", _vite_proxy_handler)

    async def _close(app):
        await app[_HTTP].aclose()

    app.on_cleanup.append(_close)
    return app


def _auth_headers(cfg: DevProxyConfig, incoming) -> dict[str, str]:
    headers = {k: v for k, v in incoming.items() if k.lower() not in _STRIP}
    headers["Authorization"] = f"Bearer {cfg.token}"
    if cfg.org_id:
        headers["X-Bifrost-Org"] = cfg.org_id
    headers["X-Bifrost-App"] = cfg.app_id
    return headers


def _passthrough_headers(resp, default_content_type: str) -> dict[str, str]:
    """Headers to copy from an upstream httpx response onto our web.Response.

    Forwards content-type and (when present) location so upstream 3xx
    redirects survive the proxy.
    """
    headers = {"content-type": resp.headers.get("content-type", default_content_type)}
    location = resp.headers.get("location")
    if location:
        headers["location"] = location
    return headers


def _is_ws_upgrade(request: web.Request) -> bool:
    return request.headers.get("Upgrade", "").lower() == "websocket"


def _ws_scheme(http_url: str) -> str:
    """http→ws, https→wss for the origin of a target URL."""
    if http_url.startswith("https://"):
        return "wss://" + http_url[len("https://"):]
    if http_url.startswith("http://"):
        return "ws://" + http_url[len("http://"):]
    return http_url


async def _ws_proxy(request: web.Request, target_ws_url: str) -> web.WebSocketResponse:
    requested = [
        p.strip()
        for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",")
        if p.strip()
    ]
    ws_server = web.WebSocketResponse(protocols=tuple(requested))
    await ws_server.prepare(request)
    session = aiohttp.ClientSession()
    try:
        async with session.ws_connect(
            target_ws_url,
            protocols=tuple(requested),
            headers={k: v for k, v in request.headers.items() if k.lower() == "cookie"},
        ) as ws_client:
            async def c2s():
                async for msg in ws_server:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await ws_client.send_str(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await ws_client.send_bytes(msg.data)
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        break

            async def s2c():
                async for msg in ws_client:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await ws_server.send_str(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await ws_server.send_bytes(msg.data)
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        break

            # When either side closes, tear the other down too — gather()ing
            # both would leave the surviving pump (and this handler, the
            # ClientSession, and the upstream socket) alive forever on every
            # browser reload.
            _, pending = await asyncio.wait(
                [asyncio.ensure_future(c2s()), asyncio.ensure_future(s2c())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await ws_client.close()
            await ws_server.close()
    finally:
        await session.close()
    return ws_server


async def _ws_handler(request: web.Request) -> web.WebSocketResponse:
    """Bridge realtime (/ws/...) sockets to the dev API."""
    cfg: DevProxyConfig = request.app[_CFG]
    target = f"{_ws_scheme(cfg.upstream_url)}{request.rel_url}"
    return await _ws_proxy(request, target)


async def _execute_handler(request: web.Request) -> web.Response:
    cfg: DevProxyConfig = request.app[_CFG]
    host = request.app[_HOST]
    body = await request.json()
    ref = body.get("workflow_id", "")

    # Local path::fn that we discovered → run it in-process (own-first, locally).
    if "::" in str(ref) and host.has(ref):
        try:
            result = await host.run(ref, body.get("input_data") or {})
        except Exception as exc:
            # Surface the real error in the app: useWorkflow reads `body.error`
            # on a 200 (the deployed error contract) and shows it; on a non-200 it
            # only shows `statusText` ("Internal Server Error"), hiding the cause.
            # Returning {"error": ...} at 200 gives the dev the actual traceback —
            # the whole point of a local debug loop.
            import traceback

            tb = traceback.format_exc()
            return web.json_response({"error": f"{type(exc).__name__}: {exc}\n\n{tb}"})
        return web.json_response({"status": "completed", "result": result})

    # Otherwise proxy to the dev API (UUIDs, _repo/ refs, sibling installs).
    try:
        resp = await request.app[_HTTP].post(
            f"{cfg.upstream_url}/api/workflows/execute",
            json=body,
            headers=_auth_headers(cfg, request.headers),
        )
    except httpx.HTTPError:
        return web.json_response(
            {"detail": f"Dev API unreachable at {cfg.upstream_url}"}, status=502
        )
    return web.Response(
        body=resp.content, status=resp.status_code,
        headers=_passthrough_headers(resp, "application/json"),
    )


async def _api_proxy_handler(request: web.Request) -> web.StreamResponse:
    cfg: DevProxyConfig = request.app[_CFG]
    if _is_ws_upgrade(request):
        target = f"{_ws_scheme(cfg.upstream_url)}{request.rel_url}"
        return await _ws_proxy(request, target)
    data = await request.read()
    try:
        resp = await request.app[_HTTP].request(
            request.method,
            f"{cfg.upstream_url}{request.rel_url}",
            content=data or None,
            headers=_auth_headers(cfg, request.headers),
        )
    except httpx.HTTPError:
        return web.json_response(
            {"detail": f"Dev API unreachable at {cfg.upstream_url}"}, status=502
        )
    return web.Response(
        body=resp.content, status=resp.status_code,
        headers=_passthrough_headers(resp, "application/json"),
    )


async def _vite_proxy_handler(request: web.Request) -> web.StreamResponse:
    vite_url = request.app[_VITE]
    if _is_ws_upgrade(request):
        target = f"{_ws_scheme(vite_url)}{request.rel_url}"
        return await _ws_proxy(request, target)
    data = await request.read()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _STRIP}
    resp = await request.app[_HTTP].request(
        request.method,
        f"{vite_url}{request.rel_url}",
        content=data or None,
        headers=headers,
    )
    return web.Response(
        body=resp.content, status=resp.status_code,
        headers=_passthrough_headers(resp, "text/html"),
    )
