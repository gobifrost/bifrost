"""Opaque sandbox runtime for builder-generated Solution apps.

This narrow ASGI application is mounted inside the existing API process at
``/api/builder-runtime``. Generated documents are forced into a unique opaque
origin by both CSP ``sandbox`` and the client iframe's sandbox attribute. That
preserves the browser boundary without a second public port, hostname, DNS
record, or permanent container. Nothing here serves control-plane UI.

Three request shapes, three different credentials:

* ``GET /launch/{code}`` — redeems the one-time launch code minted by the
  control plane and installs the app-host session cookie. The code carries no
  authority itself and dies on first use, so a leaked Referer or history entry
  is worthless.
* ``GET /{solution_id}/apps/{app_id}/{path}`` — a browser *document* load, so
  it authenticates on the session cookie, not a bearer token. The cookie's
  session must be bound to exactly this Solution and app or the response is
  404 (not 403 — a private Solution is invisible).
* ``POST /{solution_id}/apps/{app_id}/_bifrost/session-token`` — mints the
  short-lived ``solution_app`` bearer token the app's SDK calls use, renewing
  the session as it goes.

Enforcement is by route dependency, never by path-regex allowlist. The
existing ``EmbedScopeMiddleware`` uses a regex allowlist and fails open as
routes evolve; that pattern is deliberately not repeated here.
"""

from __future__ import annotations

import mimetypes
from typing import Annotated
from uuid import UUID

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings, get_settings
from src.core.cache.redis_client import get_shared_redis
from src.core.database import get_db
from src.models.orm.applications import Application
from src.routers.solution_builder import BuilderContext
from src.services.builder.app_session import (
    AppLaunchService,
    AppSession,
    mint_app_token,
)
from src.services.builder.private_solutions import load_accessible_private_solution
from src.services.solutions.access import SolutionAction
from src.services.solutions.app_build import SolutionAppBuilder
from src.services.solutions.app_runtime_access import load_runtime_viewer

router = APIRouter(prefix="", tags=["solution-app-host"])

# The control-plane half of the launch flow stays on the ordinary authenticated
# API; the generated document and attenuated SDK live in the mounted sub-app.
control_router = APIRouter(prefix="/api/builder/solutions", tags=["builder"])

SESSION_COOKIE = "bifrost_app_session"
INDEX_FILE = "index.html"
PUBLIC_RUNTIME_PREFIX = "/api/builder-runtime"

# Hashed Vite assets are content-addressed, so they may be cached hard. The
# entry document must not be, or a redeploy leaves the browser pinned to a
# stale asset graph.
_IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
_NO_STORE = "no-store"
_BOOTSTRAP_PATH = "/_bifrost/bootstrap.js"

_BOOTSTRAP_JS = r"""
const entryScript = [...document.querySelectorAll('script[type="module"][src]')]
  .find((node) => !node.src.endsWith('/_bifrost/bootstrap.js'));
if (!entryScript) throw new Error("Missing generated app module entry");

const bootstrapScript = [...document.querySelectorAll('script[data-bifrost-session-token]')][0];
if (!bootstrapScript) throw new Error("Missing app session bootstrap data");
const sessionTokenPath = bootstrapScript.dataset.bifrostSessionToken;
const tokenResponse = await fetch(sessionTokenPath, {
  method: "POST",
  credentials: "include",
  headers: { "Accept": "application/json" },
});
if (!tokenResponse.ok) throw new Error("App session has expired");
const bootstrap = await tokenResponse.json();

const entryUrl = new URL(entryScript.src, window.location.href).href;
const module = await import(entryUrl);
if (!module?.mount) throw new Error("Generated app does not export mount()");

const controlOrigin = document.referrer
  ? new URL(document.referrer).origin
  : new URL(window.location.href).origin;
const reportNavigation = () => window.parent.postMessage({
  type: "bifrost:app-navigation",
  path: window.location.pathname.slice(bootstrap.basename.length) || "/",
  search: window.location.search,
  hash: window.location.hash,
}, controlOrigin);
for (const method of ["pushState", "replaceState"]) {
  const original = history[method].bind(history);
  history[method] = (...args) => {
    const result = original(...args);
    reportNavigation();
    return result;
  };
}
window.addEventListener("popstate", reportNavigation);
window.addEventListener("hashchange", reportNavigation);

const mountEl = document.getElementById("root");
if (!mountEl) throw new Error("Missing #root mount element");
module.mount(mountEl, {
  basename: bootstrap.basename,
  baseUrl: `${new URL(window.location.href).origin}/api/builder-runtime/_bifrost`,
  token: bootstrap.access_token,
  orgScope: bootstrap.organization_id,
  appId: bootstrap.app_id,
  onLogout: async () => {
    await fetch(sessionTokenPath, { method: "DELETE", credentials: "include" });
    window.location.reload();
  },
  theme: window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light",
});
reportNavigation();
""".strip()


async def get_launch_service() -> AppLaunchService:
    return AppLaunchService(await get_shared_redis())


LaunchService = Annotated[AppLaunchService, Depends(get_launch_service)]
Db = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def require_app_session(request: Request, launches: LaunchService) -> AppSession:
    """Authenticate a browser document load on the app-host session cookie."""
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No app-host session",
        )
    session = await launches.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="App-host session has expired",
        )
    return session


CurrentAppSession = Annotated[AppSession, Depends(require_app_session)]


async def _validate_session_binding(
    db: AsyncSession,
    session: AppSession,
) -> str | None:
    """Return the viewer email while the exact launch remains authorized."""
    viewer = await load_runtime_viewer(
        db,
        user_id=session.user_id,
        solution_id=session.solution_id,
        app_id=session.app_id,
        organization_id=session.organization_id,
    )
    return viewer.user.email if viewer is not None else None


def _csp() -> str:
    """Restrictive CSP for generated apps.

    The runtime shares the public Bifrost host, while CSP ``sandbox`` gives the
    document an opaque browser origin. Arbitrary browser egress is disabled:
    generated code can talk only to its narrow runtime API.
    """
    return (
        "default-src 'self'; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'self'; "
        "frame-ancestors 'self'; "
        "sandbox allow-forms allow-scripts"
    )


@router.get("/launch/{code}", summary="Redeem a one-time app launch code")
async def redeem_launch(
    code: str, launches: LaunchService, settings: SettingsDep
) -> RedirectResponse:
    session = await launches.redeem_launch_code(code)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This launch link is invalid or has already been used",
        )

    app_base = f"{PUBLIC_RUNTIME_PREFIX}/{session.solution_id}/apps/{session.app_id}"
    target = f"{app_base}{session.path}"
    response = RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)
    # Host-only and path-scoped so generated code cannot send this capability
    # to a sibling app runtime or an ordinary control-plane endpoint.
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session.session_id,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path=app_base,
    )
    return response


@router.post(
    "/{solution_id}/apps/{app_id}/_bifrost/session-token",
    summary="Mint a short-lived Solution app token",
)
async def mint_session_token(
    solution_id: UUID,
    app_id: UUID,
    session: CurrentAppSession,
    launches: LaunchService,
    db: Db,
) -> dict[str, str]:
    if session.solution_id != solution_id or session.app_id != app_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    email = await _validate_session_binding(db, session)
    if email is None:
        await launches.revoke_session(session.session_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The app launch is no longer authorized",
        )
    if not await launches.renew_session(session.session_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="App-host session has expired",
        )
    return {
        "access_token": mint_app_token(session, user_email=email),
        "token_type": "bearer",
        "app_id": str(session.app_id),
        "organization_id": str(session.organization_id),
        "basename": f"{PUBLIC_RUNTIME_PREFIX}/{session.solution_id}/apps/{session.app_id}",
    }


@router.delete(
    "/{solution_id}/apps/{app_id}/_bifrost/session-token",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the app-host session",
)
async def revoke_session(
    solution_id: UUID,
    app_id: UUID,
    session: CurrentAppSession,
    launches: LaunchService,
    response: Response,
) -> None:
    if session.solution_id != solution_id or session.app_id != app_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await launches.revoke_session(session.session_id)
    response.delete_cookie(
        SESSION_COOKIE,
        path=f"{PUBLIC_RUNTIME_PREFIX}/{solution_id}/apps/{app_id}",
    )


@router.get(_BOOTSTRAP_PATH, summary="Serve the fixed app-host bootstrap")
async def serve_bootstrap() -> Response:
    return Response(
        content=_BOOTSTRAP_JS,
        media_type="text/javascript",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": _NO_STORE,
        },
    )


@router.get(
    "/{solution_id}/apps/{app_id}/{path:path}",
    summary="Serve a built Solution app artifact",
)
async def serve_app_artifact(
    solution_id: UUID,
    app_id: UUID,
    path: str,
    session: CurrentAppSession,
    settings: SettingsDep,
    db: Db,
) -> Response:
    # A session is bound to exactly one launch. 404, not 403: a Solution the
    # session is not bound to must be indistinguishable from one that does not
    # exist.
    if session.solution_id != solution_id or session.app_id != app_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if await _validate_session_binding(db, session) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    builder = SolutionAppBuilder(settings)
    rel = path.lstrip("/") or INDEX_FILE
    # The relative path becomes an S3 key under _apps/{app_id}/dist/. A "..",
    # a leading slash, or a NUL would let a crafted request address a key
    # outside this app's artifact prefix.
    if any(seg in ("..", "") for seg in rel.split("/")) or "\x00" in rel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    try:
        body = await builder.read_dist(app_id, rel)
    except ClientError:
        # BrowserRouter deep links arrive as unknown paths; the SPA entry
        # document resolves them client-side. An asset request that misses is
        # a real 404 and must not be answered with HTML.
        if "." in rel.rsplit("/", 1)[-1]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Not found"
            ) from None
        rel = INDEX_FILE
        try:
            body = await builder.read_dist(app_id, INDEX_FILE)
        except ClientError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Not found"
            ) from None

    is_index = rel == INDEX_FILE
    if is_index:
        try:
            html = body.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Built app index is not UTF-8",
            ) from None
        session_token_path = (
            f"{PUBLIC_RUNTIME_PREFIX}/{solution_id}/apps/{app_id}"
            "/_bifrost/session-token"
        )
        app_base = f"{PUBLIC_RUNTIME_PREFIX}/{solution_id}/apps/{app_id}"
        # Builder Vite output is path-independent (base ``./``) so promotion
        # may toggle isolated/trusted without rebuilding reviewed bytes. A
        # deep-link document needs its relative entry URLs anchored to the app
        # root rather than the requested client-side route.
        html = html.replace('src="./', f'src="{app_base}/')
        html = html.replace('href="./', f'href="{app_base}/')
        bootstrap_tag = (
            f'<script type="module" src="{PUBLIC_RUNTIME_PREFIX}{_BOOTSTRAP_PATH}" '
            f'data-bifrost-session-token="{session_token_path}"></script>'
        )
        if "</body>" in html:
            html = html.replace("</body>", f"{bootstrap_tag}</body>", 1)
        else:
            html = f"{html}{bootstrap_tag}"
        body = html.encode("utf-8")
    media_type = mimetypes.guess_type(rel)[0] or "application/octet-stream"
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": _NO_STORE if is_index else _IMMUTABLE_CACHE,
    }
    if is_index:
        headers["Content-Security-Policy"] = _csp()
    return Response(content=body, media_type=media_type, headers=headers)


@control_router.post(
    "/{solution_id}/apps/{app_id}/launch",
    summary="Create a one-time launch URL for an isolated Solution app",
)
async def create_launch(
    solution_id: UUID,
    app_id: UUID,
    ctx: BuilderContext,
    launches: LaunchService,
    path: str = "/",
) -> dict[str, str]:
    loaded = await load_accessible_private_solution(
        ctx.db,
        solution_id=solution_id,
        action=SolutionAction.VIEW,
        actor_user_id=ctx.user.user_id,
        is_platform_admin=ctx.authorization.has_capability("platform.superuser"),
        is_external=ctx.user.is_external,
        can_support=ctx.authorization.has_delegated_capability("builder.read"),
        effective_role_ids=frozenset(ctx.authorization.role_ids),
    )
    if loaded is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    app = (
        await ctx.db.execute(
            select(Application).where(
                Application.id == app_id,
                Application.solution_id == solution_id,
            )
        )
    ).scalar_one_or_none()
    if app is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if app.runtime_mode != "isolated":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This application uses the trusted runtime",
        )
    runtime_org_id = app.organization_id or ctx.org_id
    if runtime_org_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This app needs an organization runtime scope",
        )

    code = await launches.create_launch_code(
        user_id=ctx.user.user_id,
        solution_id=solution_id,
        app_id=app_id,
        organization_id=runtime_org_id,
        path=path if path.startswith("/") else f"/{path}",
    )
    return {"launch_url": f"{PUBLIC_RUNTIME_PREFIX}/launch/{code}"}
