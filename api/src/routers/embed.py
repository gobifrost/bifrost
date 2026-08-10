"""Public embed entry point — HMAC-verified iframe loading."""

import logging
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Path, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.responses import RedirectResponse

from src.core.database import get_db_context
from src.core.rate_limit import RateLimiter, get_client_ip
from src.core.security import create_embed_access_token, decrypt_secret
from src.models.orm.applications import Application
from src.models.orm.forms import Form as FormORM
from src.models.orm.form_publications import FormPublication
from src.models.orm.solutions import Solution
from src.services.embed_auth import verify_embed_hmac
from shared.form_runtime import form_capability_fingerprint

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/embed",
    tags=["Embed"],
)

_public_bootstrap_limiter = RateLimiter(max_requests=30, window_seconds=60)

_FORM_PRESENTATION_VALUES = {
    "theme": {"light", "dark", "system"},
    "header": {"true", "false"},
    "background": {"solid", "transparent"},
}


def _form_presentation_query(query_params: dict[str, str]) -> str:
    """Return the validated, presentation-only query for the SPA document."""

    values = [
        (key, query_params[key])
        for key, allowed_values in _FORM_PRESENTATION_VALUES.items()
        if query_params.get(key) in allowed_values
    ]
    return urlencode(values)


def _form_redirect_url(
    path: str, query_params: dict[str, str], access_token: str
) -> str:
    presentation_query = _form_presentation_query(query_params)
    query_suffix = f"?{presentation_query}" if presentation_query else ""
    return f"{path}{query_suffix}#embed_token={access_token}"


def _frame_policy_response(frame_ancestors: str) -> Response:
    """Return the policy consumed by the SPA server for the final document."""

    return Response(
        status_code=204,
        headers={
            "Content-Security-Policy": f"frame-ancestors {frame_ancestors}",
            "X-Bifrost-Frame-Ancestors": frame_ancestors,
            "Cache-Control": "no-store",
        },
    )


async def _load_current_publication(public_key: str) -> FormPublication:
    async with get_db_context() as db:
        result = await db.execute(
            select(FormPublication)
            .where(
                FormPublication.public_key == public_key,
                FormPublication.is_active.is_(True),
            )
            .options(
                selectinload(FormPublication.form).selectinload(FormORM.fields)
            )
        )
        publication = result.scalar_one_or_none()

    if publication is None or not publication.form.is_active:
        raise HTTPException(status_code=404, detail="Form unavailable")

    if publication.form.solution_id is not None:
        async with get_db_context() as db:
            solution_status = await db.scalar(
                select(Solution.status).where(
                    Solution.id == publication.form.solution_id
                )
            )
        if solution_status != "active":
            raise HTTPException(status_code=404, detail="Form unavailable")

    fingerprint = form_capability_fingerprint(publication.form)
    if fingerprint != publication.approved_fingerprint:
        raise HTTPException(status_code=404, detail="Form unavailable")
    return publication


@router.get("/apps/{slug}")
async def embed_app(
    request: Request,
    slug: str = Path(...),
):
    """Public entry point for HMAC-authenticated iframe embedding.

    Verifies the HMAC signature against the app's embed secrets,
    issues an 8-hour embed JWT cookie, and returns a confirmation response.
    """
    query_params = dict(request.query_params)

    if "hmac" not in query_params:
        raise HTTPException(status_code=403, detail="Missing HMAC signature")

    # Look up the app and its active embed secrets (no auth required — public endpoint)
    async with get_db_context() as db:
        result = await db.execute(
            select(Application)
            .where(Application.slug == slug)
            .options(selectinload(Application.embed_secrets))
        )
        candidates = list(result.scalars().all())

    if not candidates:
        raise HTTPException(status_code=404, detail="Application not found")

    # A slug may match multiple installs of the same solution (slug uniqueness
    # is per-install). The embed secret is bound to ONE Application row, so the
    # HMAC itself disambiguates: the row whose active secret verifies wins.
    app = None
    for candidate in candidates:
        for secret_record in (s for s in candidate.embed_secrets if s.is_active):
            raw_secret = decrypt_secret(secret_record.secret_encrypted)
            if verify_embed_hmac(query_params, raw_secret, secret_record.hmac_scheme):
                app = candidate
                break
        if app is not None:
            break

    if app is None:
        # "No embed secrets configured" only when NO candidate has one: with
        # multiple installs we can't know which install the caller meant, and
        # reporting per-install secret state on a public endpoint would leak
        # configuration info. Whenever any secret exists, the only safe answer
        # is that the signature didn't verify.
        if not any(s.is_active for c in candidates for s in c.embed_secrets):
            raise HTTPException(status_code=403, detail="No embed secrets configured")
        raise HTTPException(status_code=403, detail="Invalid HMAC signature")

    # Extract verified params (everything except hmac)
    verified_params = {k: v for k, v in query_params.items() if k != "hmac"}

    # Issue a scoped embed access token — NOT a superuser.
    # The token is org-scoped and carries app_id + embed flag so the
    # auth middleware can restrict it to app-rendering endpoints only.
    access_token = create_embed_access_token(
        embed_kind="app",
        grant="hmac",
        resource_id=str(app.id),
        org_id=str(app.organization_id) if app.organization_id else None,
        verified_context=verified_params,
        expires_delta=timedelta(hours=8),
    )

    # Pass token in URL fragment — fragments are never sent to the server,
    # keeping the token client-side only. This avoids cross-origin cookie
    # issues when third-party sites embed Bifrost apps in iframes.
    redirect = RedirectResponse(
        url=f"/apps/{app.slug}#embed_token={access_token}",
        status_code=302,
    )

    # Set permissive framing headers for embed route
    redirect.headers["Content-Security-Policy"] = "frame-ancestors *"
    redirect.headers["X-Frame-Options"] = "ALLOWALL"

    return redirect


@router.get("/forms/public/{public_key}")
async def embed_public_form(request: Request, public_key: str = Path(...)):
    """Mint a short-lived form session from an active public publication."""

    await _public_bootstrap_limiter.check(
        "public_form_bootstrap",
        f"{public_key}:{get_client_ip(request)}",
    )
    publication = await _load_current_publication(public_key)
    fingerprint = publication.approved_fingerprint

    token = create_embed_access_token(
        embed_kind="form",
        grant="public",
        resource_id=str(publication.form.id),
        org_id=(
            str(publication.form.organization_id)
            if publication.form.organization_id
            else None
        ),
        display_name=f"Public Form · {publication.form.name}",
        verified_context={},
        capability_fingerprint=fingerprint,
        expires_delta=timedelta(minutes=30),
    )
    logger.info(
        "Public form session issued",
        extra={"form_id": str(publication.form.id), "grant": "public"},
    )
    return RedirectResponse(
        url=_form_redirect_url(
            f"/embedded/forms/public/{public_key}",
            dict(request.query_params),
            token,
        ),
        status_code=302,
    )


@router.get("/forms/public/{public_key}/frame-policy", include_in_schema=False)
async def public_form_frame_policy(public_key: str = Path(...)) -> Response:
    """Resolve the CSP applied to the final public-form SPA document."""

    publication = await _load_current_publication(public_key)
    ancestors = " ".join(publication.allowed_origins) or "*"
    return _frame_policy_response(ancestors)


@router.get("/forms/hmac/{form_id}/frame-policy", include_in_schema=False)
async def hmac_form_frame_policy(form_id: str = Path(...)) -> Response:
    """HMAC form links intentionally allow framing from any parent origin."""

    from uuid import UUID as PyUUID

    try:
        PyUUID(form_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Form not found") from exc
    return _frame_policy_response("*")


@router.get("/forms/{form_id}")
async def embed_form(
    request: Request,
    form_id: str = Path(...),
):
    """Public entry point for HMAC-authenticated form iframe embedding."""
    from uuid import UUID as PyUUID

    query_params = dict(request.query_params)

    if "hmac" not in query_params:
        raise HTTPException(status_code=403, detail="Missing HMAC signature")

    # Parse form_id as UUID
    try:
        form_uuid = PyUUID(form_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Form not found")

    # Look up the form and its active embed secrets
    async with get_db_context() as db:
        result = await db.execute(
            select(FormORM)
            .where(FormORM.id == form_uuid)
            .options(selectinload(FormORM.embed_secrets))
        )
        form = result.scalar_one_or_none()

        solution_status = None
        if form is not None and form.solution_id is not None:
            solution_status = await db.scalar(
                select(Solution.status).where(Solution.id == form.solution_id)
            )

    if not form or not form.is_active or (
        form.solution_id is not None and solution_status != "active"
    ):
        raise HTTPException(status_code=404, detail="Form not found")

    active_secrets = [s for s in form.embed_secrets if s.is_active]
    if not active_secrets:
        raise HTTPException(status_code=403, detail="No embed secrets configured")

    # Try each active secret using its configured scheme
    verified = False
    for secret_record in active_secrets:
        raw_secret = decrypt_secret(secret_record.secret_encrypted)
        if verify_embed_hmac(query_params, raw_secret, secret_record.hmac_scheme):
            verified = True
            break

    if not verified:
        raise HTTPException(status_code=403, detail="Invalid HMAC signature")

    # Extract verified params (everything except hmac)
    verified_params = {
        k: v
        for k, v in query_params.items()
        if k != "hmac" and k not in _FORM_PRESENTATION_VALUES
    }

    # Issue a scoped embed access token
    access_token = create_embed_access_token(
        embed_kind="form",
        grant="hmac",
        resource_id=str(form.id),
        org_id=str(form.organization_id) if form.organization_id else None,
        verified_context=verified_params,
        expires_delta=timedelta(hours=8),
    )
    logger.info(
        "HMAC form session issued",
        extra={"form_id": str(form.id), "grant": "hmac"},
    )

    redirect = RedirectResponse(
        url=_form_redirect_url(
            f"/embedded/forms/hmac/{form.id}", query_params, access_token
        ),
        status_code=302,
    )

    # Set permissive framing headers for embed route
    redirect.headers["Content-Security-Policy"] = "frame-ancestors *"
    redirect.headers["X-Frame-Options"] = "ALLOWALL"

    return redirect
