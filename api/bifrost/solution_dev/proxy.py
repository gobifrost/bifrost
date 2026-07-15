"""The single-origin local dev server for `bifrost solution start`.

Routes:
  POST /api/workflows/execute  → local FunctionHost when the ref (path::fn,
                                 manifest UUID, or name) resolves to THIS
                                 workspace; misses fall back to the platform's
                                 shared _repo/ content only when the descriptor
                                 sets global_repo_access, with all install-scope
                                 signals stripped from the fallback request.
  /api/*                       → reverse-proxy to the dev API (data-plane).
  /ws/*  (and /api/* upgrades) → bridge websockets to the dev API (realtime).
  everything else              → reverse-proxy to the Vite dev server (the app),
                                 including Vite's own HMR websocket.

The upstream proxy injects the CLI token (Authorization), the bound install org
(X-Bifrost-Org), and the bound install id (``?solution=`` / ``solution_id``) so
data-plane calls run under the same install scope as deployed.

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
import html
import re
import uuid as _uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import aiohttp
import click
import httpx
import yarl
from aiohttp import web

from bifrost.solution_dev.function_host import LocalWorkflowError

# Headers we must not forward when reverse-proxying: hop-by-hop headers, plus
# the browser's Accept-Encoding — browsers advertise encodings (br, zstd) that
# httpx may not decode, and the proxy rebuilds response headers WITHOUT
# Content-Encoding, so a passed-through compressed body would reach the browser
# labeled as plain JSON. Stripping it lets httpx negotiate only what it decodes.
_STRIP = {
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "accept-encoding",
}


class _UpstreamAuthorityError(ValueError):
    """The constructed proxy target's authority is not the trusted base's."""


DEV_AUTH_EXPIRED_DETAIL = "Your CLI token has expired. Restart `bifrost solution start`."


def _join_upstream(base_url: str, rel_url: yarl.URL) -> str:
    """Build a target URL whose authority is ALWAYS ``base_url``'s.

    ``rel_url`` is request-controlled. Reconstructing the target as a plain
    f-string (``f"{base_url}{rel_url}"``) lets a crafted request path repoint
    the proxy at a different host (``//evil/…``, an embedded ``@``, a scheme),
    i.e. server-side request forgery. We keep the trusted base's scheme/host/
    port and graft on ONLY ``rel_url``'s already-encoded path + raw query
    string, then **re-validate** that the result's origin is still the base's
    origin with an anchored ``re.fullmatch`` allowlist before returning — so the
    authority can never come from the request even if yarl's join surprised us.

    The query is taken as the RAW string (not reparsed via ``with_query``):
    Vite distinguishes flag-style query params — ``?raw``/``?url``/``?worker``
    for ``import x from './f.md?raw'`` — from ``?raw=``, and reparsing would
    turn the former into the latter and break those imports through the proxy.
    """
    base = yarl.URL(base_url)
    target = base.with_path(rel_url.raw_path, encoded=True)
    if rel_url.raw_query_string:
        result = f"{target}?{rel_url.raw_query_string}"
    else:
        result = str(target)
    # Anchored allowlist: the target MUST start with the exact trusted origin
    # (scheme://host[:port]) followed by a path. re.fullmatch on an
    # origin-prefixed pattern is the SSRF barrier static analysis recognizes,
    # and it genuinely rejects any authority the request managed to inject.
    origin = str(base.origin())
    if not re.fullmatch(re.escape(origin) + r"(?:/.*)?", result, re.DOTALL):
        raise _UpstreamAuthorityError(f"proxy target {result!r} escapes upstream {origin!r}")
    return result


@dataclass
class DevProxyConfig:
    upstream_url: str   # the dev API, e.g. http://localhost:37791
    token: str          # CLI access token
    app_id: str         # chosen app's manifest UUID
    org_id: str | None  # bound install org id (or None for a global install)
    solution_id: str | None = None  # bound Solution install id
    # Whether bifrost.solution.yaml sets global_repo_access. Gates whether a
    # workflow ref that misses locally may fall back to the platform's shared
    # _repo/ content at all. Mirrors the module-loader semantics (§3.5).
    global_repo_access: bool = False
    refresh_token: Callable[[str], Awaitable[str | None]] | None = None
    auth_expired: bool = False
    branding: dict[str, Any] | None = None
    branding_loaded: bool = False


# Typed app keys (avoid aiohttp's NotAppKeyWarning for plain-string keys).
_CFG = web.AppKey("cfg", DevProxyConfig)
_HOST = web.AppKey("host", object)
_VITE = web.AppKey("vite_url", str)
_HTTP = web.AppKey("http", httpx.AsyncClient)
_WARNED_UUID_REFS = web.AppKey("warned_uuid_refs", set)


def build_dev_app(cfg: DevProxyConfig, host, vite_url: str) -> web.Application:
    app = web.Application()
    app[_CFG] = cfg
    app[_HOST] = host
    app[_VITE] = vite_url.rstrip("/")
    app[_HTTP] = httpx.AsyncClient(timeout=120.0)
    app[_WARNED_UUID_REFS] = set()

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
    headers["Accept-Encoding"] = "identity"
    if cfg.org_id:
        headers["X-Bifrost-Org"] = cfg.org_id
    headers["X-Bifrost-App"] = cfg.app_id
    return headers


def _with_solution_query(rel_url: yarl.URL, solution_id: str | None) -> yarl.URL:
    if not solution_id:
        return rel_url
    return rel_url.update_query(solution=solution_id)


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


async def _refresh_cli_token(cfg: DevProxyConfig, observed_access_token: str) -> bool:
    if cfg.refresh_token is None:
        cfg.auth_expired = True
        return False
    try:
        token = await cfg.refresh_token(observed_access_token)
    except Exception:
        token = None
    if not token:
        cfg.auth_expired = True
        return False
    cfg.token = token
    cfg.auth_expired = False
    return True


async def _authed_upstream_request(
    request: web.Request,
    method: str,
    url: str,
    *,
    drop_headers: set[str] | None = None,
    **kwargs,
) -> httpx.Response | None:
    """Upstream request with injected auth, retried once after a token refresh.

    ``drop_headers`` removes headers (lowercase names) after ``_auth_headers``
    builds them — the workflow-execute fallback must not carry ``X-Bifrost-App``
    (an install-scope signal) while every other route keeps it.
    """
    cfg: DevProxyConfig = request.app[_CFG]
    http: httpx.AsyncClient = request.app[_HTTP]

    def _headers() -> dict[str, str]:
        headers = _auth_headers(cfg, request.headers)
        if drop_headers:
            headers = {k: v for k, v in headers.items() if k.lower() not in drop_headers}
        return headers

    observed_access_token = cfg.token
    resp = await http.request(method, url, headers=_headers(), **kwargs)
    if resp.status_code != 401:
        return resp
    if not await _refresh_cli_token(cfg, observed_access_token):
        return None
    retry = await http.request(method, url, headers=_headers(), **kwargs)
    if retry.status_code == 401:
        cfg.auth_expired = True
        return None
    return retry


def _dev_auth_expired_json_response() -> web.Response:
    return web.json_response(
        {
            "error": "bifrost_dev_auth_expired",
            "detail": DEV_AUTH_EXPIRED_DETAIL,
        },
        status=401,
        headers={"X-Bifrost-Dev-Auth": "expired"},
    )


async def _get_branding(request: web.Request) -> dict[str, Any]:
    cfg: DevProxyConfig = request.app[_CFG]
    if cfg.branding_loaded:
        return cfg.branding or {}
    cfg.branding_loaded = True
    try:
        resp = await request.app[_HTTP].get(f"{cfg.upstream_url}/api/branding")
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                cfg.branding = data
    except (httpx.HTTPError, ValueError):
        cfg.branding = None
    return cfg.branding or {}


def _clean_brand_color(value: Any) -> str:
    if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value
    return "#0066CC"


def _clean_brand_text(value: Any) -> str:
    if not isinstance(value, str):
        return "Bifrost"
    value = value.strip()
    return value or "Bifrost"


async def _dev_auth_expired_page_response(request: web.Request) -> web.Response:
    branding = await _get_branding(request)
    product_name = _clean_brand_text(branding.get("application_name"))
    primary_color = _clean_brand_color(branding.get("primary_color"))
    escaped_product_name = html.escape(product_name)
    escaped_primary_color = html.escape(primary_color, quote=True)
    escaped_detail = html.escape(DEV_AUTH_EXPIRED_DETAIL)
    page_html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escaped_product_name} Dev Auth Expired</title>
    <script>
      try {{
        if ((localStorage.getItem("theme") || "dark") === "dark") {{
          document.documentElement.classList.add("dark");
        }}
      }} catch (_) {{
        document.documentElement.classList.add("dark");
      }}
    </script>
    <style>
      :root {{ color-scheme: light dark; }}
      body {{
        margin: 0;
        min-height: 100vh;
        display: flex;
        align-items: center;
        justify-content: center;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #ffffff;
        color: #0f172a;
        padding: 16px;
        box-sizing: border-box;
      }}
      .shell {{
        width: min(672px, 100%);
      }}
      main {{
        border: 1px solid rgba(15, 23, 42, 0.08);
        border-radius: 24px;
        background: #ffffff;
        box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
        overflow: hidden;
      }}
      .header {{
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 20px 20px 0;
      }}
      .icon {{
        flex: 0 0 auto;
        display: flex;
        width: 48px;
        height: 48px;
        align-items: center;
        justify-content: center;
        border-radius: 8px;
        background: rgba(220, 38, 38, 0.1);
        color: #dc2626;
      }}
      h1 {{
        margin: 0 0 4px;
        color: #0f172a;
        font-size: 1rem;
        font-weight: 500;
        line-height: 1.25;
        letter-spacing: 0;
      }}
      p {{
        margin: 0;
        color: #475569;
        line-height: 1.55;
      }}
      .description {{
        font-size: 0.875rem;
      }}
      .content {{
        display: flex;
        flex-direction: column;
        gap: 16px;
        padding: 20px;
      }}
      .alert {{
        border: 1px solid rgba(220, 38, 38, 0.22);
        border-radius: 16px;
        background: #ffffff;
        color: #dc2626;
        padding: 12px 16px;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
        font-size: 0.875rem;
        line-height: 1.5;
      }}
      .actions {{
        border-radius: 8px;
        background: #f4f4f5;
        padding: 16px;
      }}
      h2 {{
        margin: 0 0 8px;
        color: #0f172a;
        font-size: 0.875rem;
        font-weight: 500;
        letter-spacing: 0;
      }}
      ul {{
        margin: 0;
        padding: 0;
        list-style: none;
        color: #71717a;
        font-size: 0.875rem;
        line-height: 1.55;
      }}
      li + li {{
        margin-top: 4px;
      }}
      code {{
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        background: #f1f5f9;
        color: #0f172a;
        padding: 2px 6px;
        font-size: 0.94em;
      }}
      .footer {{
        display: flex;
        gap: 8px;
        padding: 0 20px 20px;
      }}
      button, a {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 32px;
        padding: 0 12px;
        font: inherit;
        font-size: 0.875rem;
        font-weight: 500;
        line-height: 1;
        text-decoration: none;
        cursor: pointer;
      }}
      button {{
        border: 1px solid transparent;
        border-radius: 16px;
        background: {escaped_primary_color};
        color: #ffffff;
      }}
      a {{
        border: 1px solid rgba(15, 23, 42, 0.12);
        border-radius: 16px;
        background: #ffffff;
        color: #0f172a;
      }}
      button:hover {{
        filter: brightness(0.94);
      }}
      a:hover {{
        background: #f4f4f5;
      }}
      .dark body {{
        background: #09090b;
        color: #fafafa;
      }}
      .dark main {{
        border-color: rgba(250, 250, 250, 0.08);
        background: #171717;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35);
      }}
      .dark h1, .dark h2 {{
        color: #fafafa;
      }}
      .dark p {{
        color: #a1a1aa;
      }}
      .dark .alert {{
        border-color: rgba(248, 113, 113, 0.26);
        background: #171717;
        color: #f87171;
      }}
      .dark .actions {{
        background: #262626;
      }}
      .dark ul {{
        color: #a1a1aa;
      }}
      .dark code {{
        border-color: rgba(250, 250, 250, 0.12);
        background: #262626;
        color: #fafafa;
      }}
      .dark .actions code {{
        background: #171717;
      }}
      .dark a {{
        border-color: rgba(250, 250, 250, 0.1);
        background: #171717;
        color: #fafafa;
      }}
      .dark a:hover {{
        background: #262626;
      }}
    </style>
  </head>
  <body>
    <div class="shell">
      <main>
        <div class="header">
          <div class="icon" aria-hidden="true">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="m21.73 18-8-14a2 2 0 0 0-3.46 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"></path>
              <path d="M12 9v4"></path>
              <path d="M12 17h.01"></path>
            </svg>
          </div>
          <div>
            <h1>Something went wrong</h1>
            <p class="description">The local Solution dev session can no longer authenticate requests.</p>
          </div>
        </div>
        <div class="content">
          <div class="alert">{escaped_detail}</div>
          <div class="actions">
            <h2>What you can do:</h2>
            <ul>
              <li>• Stop this server and restart <code>bifrost solution start</code></li>
              <li>• If restarting does not fix it, run <code>bifrost login</code></li>
              <li>• Refresh this page after the Solution dev server is running again</li>
            </ul>
          </div>
        </div>
        <div class="footer">
          <button type="button" onclick="window.location.reload()">Try Again</button>
          <a href="/">Go to Home</a>
        </div>
      </main>
    </div>
  </body>
</html>
"""
    return web.Response(
        text=page_html,
        status=401,
        content_type="text/html",
        headers={"X-Bifrost-Dev-Auth": "expired"},
    )


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
    target = _ws_scheme(_join_upstream(cfg.upstream_url, request.rel_url))
    return await _ws_proxy(request, target)


def _is_uuid(ref: str) -> bool:
    try:
        _uuid.UUID(ref)
        return True
    except ValueError:
        return False


# Body fields the server derives install scope from (see
# derive_execution_solution_scope: ctx/X-Bifrost-App > solution_id > form_id
# > app_id). The global fallback must carry NONE of them: for capture-born
# installs these ids exist server-side and would resolve the CLOUD copy of
# this Solution's own workflows — the bug local-first resolution prevents.
_SCOPE_BODY_FIELDS = ("solution_id", "form_id", "app_id")


def _local_execution_response(*, result: Any = None, error: str | None = None) -> web.Response:
    """Return a terminal execution shape for an in-process local workflow.

    Local runs have no durable execution row to stream or poll. The canonical
    terminal status lets the current SDK settle inline; the additive fields
    remain compatible with pre-streaming SDKs, which read ``result``/``error``
    directly from this same 200 response.
    """
    payload: dict[str, Any] = {
        "execution_id": f"solution-start-{_uuid.uuid4()}",
        "is_transient": True,
    }
    if error is None:
        payload.update({"status": "Success", "result": result})
    else:
        payload.update({"status": "Failed", "error": error})
    return web.json_response(payload)


async def _execute_handler(request: web.Request) -> web.Response:
    cfg: DevProxyConfig = request.app[_CFG]
    host = request.app[_HOST]
    body = await request.json()
    ref = str(body.get("workflow_id", ""))

    if not ref:
        # Inline `code` execution (no workflow ref) — nothing to resolve
        # locally, and the install context still applies (module imports).
        # Forward exactly as the data plane does, scope intact.
        if cfg.solution_id:
            body["solution_id"] = cfg.solution_id
        try:
            resp = await _authed_upstream_request(
                request,
                "POST",
                f"{cfg.upstream_url}/api/workflows/execute",
                json=body,
            )
        except httpx.HTTPError:
            return web.json_response(
                {"detail": f"Dev API unreachable at {cfg.upstream_url}"}, status=502
            )
        if resp is None:
            return _dev_auth_expired_json_response()
        return web.Response(
            body=resp.content, status=resp.status_code,
            headers=_passthrough_headers(resp, "application/json"),
        )

    # Surface resolution problems in the app: useWorkflow reads `body.error`
    # on a 200 (the deployed error contract) and shows it; on a non-200 it
    # only shows `statusText`, hiding the cause. Same contract as run errors.
    try:
        local_ref = host.resolve(ref)
    except LocalWorkflowError as exc:
        return _local_execution_response(error=str(exc))

    if local_ref is not None:
        if _is_uuid(ref) and ref not in request.app[_WARNED_UUID_REFS]:
            request.app[_WARNED_UUID_REFS].add(ref)
            click.echo(
                f"  warning: workflow ref '{ref}' is a manifest UUID — it runs "
                "locally, but deploy remaps entity ids, so this ref will NOT "
                "resolve on a deployed install. Use the workflow name or "
                f"'{local_ref}' instead.",
                err=True,
            )
        try:
            result = await host.run(local_ref, body.get("input_data") or {})
        except Exception as exc:
            # Returning {"error": ...} at 200 gives the dev the actual
            # traceback — the whole point of a local debug loop.
            import traceback

            tb = traceback.format_exc()
            return _local_execution_response(
                error=f"{type(exc).__name__}: {exc}\n\n{tb}"
            )
        return _local_execution_response(result=result)

    if not cfg.global_repo_access:
        known = ", ".join(host.refs()) or "(none discovered)"
        return _local_execution_response(
            error=(
                f"Workflow '{ref}' not found in this Solution workspace. "
                f"Local refs: {known}. This Solution does not set "
                "global_repo_access: true in bifrost.solution.yaml, so "
                "`bifrost solution start` will not ask the platform to "
                "resolve it."
            )
        )

    # Shared _repo/ fallback, stripped of every install-scope signal.
    fallback_body = {k: v for k, v in body.items() if k not in _SCOPE_BODY_FIELDS}
    try:
        resp = await _authed_upstream_request(
            request,
            "POST",
            f"{cfg.upstream_url}/api/workflows/execute",
            json=fallback_body,
            drop_headers={"x-bifrost-app"},
        )
    except httpx.HTTPError:
        return web.json_response(
            {"detail": f"Dev API unreachable at {cfg.upstream_url}"}, status=502
        )
    if resp is None:
        return _dev_auth_expired_json_response()
    return web.Response(
        body=resp.content, status=resp.status_code,
        headers=_passthrough_headers(resp, "application/json"),
    )


async def _api_proxy_handler(request: web.Request) -> web.StreamResponse:
    cfg: DevProxyConfig = request.app[_CFG]
    if _is_ws_upgrade(request):
        target = _ws_scheme(_join_upstream(cfg.upstream_url, request.rel_url))
        return await _ws_proxy(request, target)
    data = await request.read()
    try:
        resp = await _authed_upstream_request(
            request,
            request.method,
            _join_upstream(
                cfg.upstream_url,
                _with_solution_query(request.rel_url, cfg.solution_id),
            ),
            content=data or None,
        )
    except httpx.HTTPError:
        return web.json_response(
            {"detail": f"Dev API unreachable at {cfg.upstream_url}"}, status=502
        )
    if resp is None:
        return _dev_auth_expired_json_response()
    return web.Response(
        body=resp.content, status=resp.status_code,
        headers=_passthrough_headers(resp, "application/json"),
    )


async def _vite_proxy_handler(request: web.Request) -> web.StreamResponse:
    cfg: DevProxyConfig = request.app[_CFG]
    accept = request.headers.get("Accept", "")
    fetch_dest = request.headers.get("Sec-Fetch-Dest", "")
    if cfg.auth_expired and ("text/html" in accept or fetch_dest in {"document", "iframe"}):
        return await _dev_auth_expired_page_response(request)
    vite_url = request.app[_VITE]
    if _is_ws_upgrade(request):
        target = _ws_scheme(_join_upstream(vite_url, request.rel_url))
        return await _ws_proxy(request, target)
    data = await request.read()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _STRIP}
    try:
        resp = await request.app[_HTTP].request(
            request.method,
            _join_upstream(vite_url, request.rel_url),
            content=data or None,
            headers=headers,
        )
    except httpx.ConnectError:
        # A dead Vite child must be an EXPLAINED 502 (like the API handler's),
        # not a bare 500 — "the page won't load" with no cause was issue #460.
        return web.json_response(
            {
                "detail": (
                    f"App dev server (vite) unreachable at {vite_url} — did it "
                    "fail to start? Check the npm output in the `bifrost "
                    "solution start` terminal."
                )
            },
            status=502,
        )
    except httpx.HTTPError as exc:
        # A live-but-misbehaving vite (read timeout during cold dependency
        # pre-bundling, protocol error mid-response) is NOT a startup failure
        # — say what actually happened instead of sending the user hunting one.
        return web.json_response(
            {"detail": f"Error talking to the app dev server (vite): {type(exc).__name__}: {exc}"},
            status=502,
        )
    return web.Response(
        body=resp.content, status=resp.status_code,
        headers=_passthrough_headers(resp, "text/html"),
    )
