"""The app host: a separate browser origin that serves builder-generated apps.

This router is mounted on the configured app origin (``BIFROST_APP_ORIGIN`` —
a sibling subdomain or a second port on the platform host). A distinct origin
is the actual security boundary: an iframe on the control origin would let
generated code read platform local storage, reach the parent DOM, and use the
user's full token. Nothing here serves control-plane UI.

Three request shapes, three different credentials:

* ``GET /launch/{code}`` — redeems the one-time launch code minted by the
  control plane and installs the app-host session cookie. The code carries no
  authority itself and dies on first use, so a leaked Referer or history entry
  is worthless.
* ``GET /{solution_id}/apps/{app_id}/{path}`` — a browser *document* load, so
  it authenticates on the session cookie, not a bearer token. The cookie's
  session must be bound to exactly this Solution and app or the response is
  404 (not 403 — a private Solution is invisible).
* ``POST /app-session/token`` — mints the short-lived ``solution_app`` bearer
  token the app's SDK calls use, renewing the session as it goes.

Enforcement is by route dependency, never by path-regex allowlist. The
existing ``EmbedScopeMiddleware`` uses a regex allowlist and fails open as
routes evolve; that pattern is deliberately not repeated here.
"""

from __future__ import annotations

import mimetypes
from typing import Annotated
from uuid import UUID

import redis.asyncio as redis
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings, get_settings
from src.core.database import get_db
from src.models.orm.applications import Application
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.routers.solution_builder import BuilderContext
from src.services.builder.app_session import (
    AppLaunchService,
    AppSession,
    mint_app_token,
)
from src.services.builder.private_solutions import load_accessible_private_solution
from src.services.solutions.access import (
    VISIBILITY_PRIVATE,
    SolutionAction,
)
from src.services.solutions.app_build import SolutionAppBuilder

router = APIRouter(prefix="", tags=["solution-app-host"])

# The control-plane half of the launch flow. It lives here rather than appended
# to solution_builder.py because that file is being written concurrently; both
# routers are wired in main.py by the orchestrator.
control_router = APIRouter(prefix="/api/builder/solutions", tags=["builder"])

SESSION_COOKIE = "bifrost_app_session"
INDEX_FILE = "index.html"

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

const tokenResponse = await fetch("/app-session/token", {
  method: "POST",
  credentials: "same-origin",
  headers: { "Accept": "application/json" },
});
if (!tokenResponse.ok) throw new Error("App session has expired");
const bootstrap = await tokenResponse.json();

const entryUrl = new URL(entryScript.src, window.location.href).href;
const module = await import(entryUrl);
if (!module?.mount) throw new Error("Generated app does not export mount()");

const reportNavigation = () => window.parent.postMessage({
  type: "bifrost:app-navigation",
  path: window.location.pathname.slice(bootstrap.basename.length) || "/",
  search: window.location.search,
  hash: window.location.hash,
}, bootstrap.control_origin);
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
  baseUrl: `${window.location.origin}/_bifrost`,
  token: bootstrap.access_token,
  orgScope: bootstrap.organization_id,
  appId: bootstrap.app_id,
  onLogout: async () => {
    await fetch("/app-session", { method: "DELETE", credentials: "same-origin" });
    window.location.reload();
  },
  theme: window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light",
});
reportNavigation();
""".strip()


def get_launch_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AppLaunchService:
    return AppLaunchService(redis.from_url(settings.redis_url, decode_responses=True))


LaunchService = Annotated[AppLaunchService, Depends(get_launch_service)]
Db = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def require_app_session(
    request: Request, launches: LaunchService
) -> AppSession:
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
    """Return the owner's current email only while this launch remains valid."""
    return (
        await db.execute(
            select(User.email)
            .select_from(Solution)
            .join(Application, Application.solution_id == Solution.id)
            .join(User, User.id == session.user_id)
            .where(
                Solution.id == session.solution_id,
                Solution.visibility == VISIBILITY_PRIVATE,
                Solution.status == "active",
                Solution.owner_user_id == session.user_id,
                Solution.organization_id == session.organization_id,
                Application.id == session.app_id,
                Application.organization_id == session.organization_id,
            )
        )
    ).scalar_one_or_none()


def _csp(settings: Settings) -> str:
    """Restrictive CSP for generated apps.

    ``frame-ancestors`` names the control origin so the builder preview iframe
    works and nothing else can frame the app. Arbitrary browser egress is not
    enabled: ``connect-src 'self'`` keeps generated code talking to the app
    host only.
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
        f"frame-ancestors {settings.public_url}"
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

    target = f"/{session.solution_id}/apps/{session.app_id}{session.path}"
    response = RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)
    # Host-only (no Domain attribute) so the cookie never reaches the control
    # origin or a sibling subdomain.
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session.session_id,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/app-session/token", summary="Mint a short-lived Solution app token")
async def mint_session_token(
    session: CurrentAppSession,
    launches: LaunchService,
    db: Db,
    settings: SettingsDep,
) -> dict[str, str]:
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
        "basename": f"/{session.solution_id}/apps/{session.app_id}",
        "control_origin": settings.public_url.rstrip("/"),
    }


@router.delete(
    "/app-session",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the app-host session",
)
async def revoke_session(
    session: CurrentAppSession, launches: LaunchService, response: Response
) -> None:
    await launches.revoke_session(session.session_id)
    response.delete_cookie(SESSION_COOKIE, path="/")


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
        bootstrap_tag = f'<script type="module" src="{_BOOTSTRAP_PATH}"></script>'
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
        headers["Content-Security-Policy"] = _csp(settings)
    return Response(content=body, media_type=media_type, headers=headers)


@control_router.post(
    "/{solution_id}/apps/{app_id}/launch",
    summary="Create a one-time launch URL for a Solution app on the app origin",
)
async def create_launch(
    solution_id: UUID,
    app_id: UUID,
    ctx: BuilderContext,
    launches: LaunchService,
    settings: SettingsDep,
    path: str = "/",
) -> dict[str, str]:
    if not settings.app_origin:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No app origin is configured (BIFROST_APP_ORIGIN). Generated apps "
                "require a separate browser origin and will not be served from the "
                "control plane."
            ),
        )
    loaded = await load_accessible_private_solution(
        ctx.db,
        solution_id=solution_id,
        action=SolutionAction.VIEW,
        actor_user_id=ctx.user.user_id,
        is_platform_admin=ctx.user.is_platform_admin,
        is_external=ctx.user.is_external,
    )
    if loaded is None or ctx.org_id is None:
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

    code = await launches.create_launch_code(
        user_id=ctx.user.user_id,
        solution_id=solution_id,
        app_id=app_id,
        organization_id=ctx.org_id,
        path=path if path.startswith("/") else f"/{path}",
    )
    return {"launch_url": f"{settings.app_origin.rstrip('/')}/launch/{code}"}
