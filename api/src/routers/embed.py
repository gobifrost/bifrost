"""Public embed entry point — HMAC-verified iframe loading."""

import logging
import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Path, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.responses import RedirectResponse

from src.core.constants import SYSTEM_USER_ID
from src.core.database import get_db_context
from src.core.security import create_access_token, decrypt_secret
from src.models.orm.applications import Application
from src.models.orm.forms import Form as FormORM
from src.services.embed_auth import verify_embed_hmac

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/embed",
    tags=["Embed"],
)


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
        app = result.scalar_one_or_none()

    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    active_secrets = [s for s in app.embed_secrets if s.is_active]
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
    verified_params = {k: v for k, v in query_params.items() if k != "hmac"}

    # Issue a scoped embed access token — NOT a superuser.
    # The token is org-scoped and carries app_id + embed flag so the
    # auth middleware can restrict it to app-rendering endpoints only.
    token_data = {
        "sub": SYSTEM_USER_ID,
        "jti": str(uuid.uuid4()),
        "app_id": str(app.id),
        "app_slug": app.slug,
        "org_id": str(app.organization_id) if app.organization_id else None,
        "verified_params": verified_params,
        "email": "embed@internal.gobifrost.com",
        "is_superuser": False,
        "embed": True,
        "roles": ["EmbedUser"],
    }
    access_token = create_access_token(token_data, expires_delta=timedelta(hours=8))

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

    if not form:
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
    verified_params = {k: v for k, v in query_params.items() if k != "hmac"}

    # Issue a scoped embed access token
    token_data = {
        "sub": SYSTEM_USER_ID,
        "jti": str(uuid.uuid4()),
        "form_id": str(form.id),
        "org_id": str(form.organization_id) if form.organization_id else None,
        "verified_params": verified_params,
        "email": "embed@internal.gobifrost.com",
        "is_superuser": False,
        "embed": True,
        "roles": ["EmbedUser"],
    }
    access_token = create_access_token(token_data, expires_delta=timedelta(hours=8))

    redirect = RedirectResponse(
        url=f"/execute/{form.id}#embed_token={access_token}",
        status_code=302,
    )

    # Set permissive framing headers for embed route
    redirect.headers["Content-Security-Policy"] = "frame-ancestors *"
    redirect.headers["X-Frame-Options"] = "ALLOWALL"

    return redirect
