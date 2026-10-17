"""CRUD endpoints for form embed secrets."""

import logging
import secrets
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, status
from sqlalchemy import select

from src.core.auth import Context, CurrentUser
from src.core.security import encrypt_secret
from src.models.contracts.applications import (
    EmbedSecretCreate,
    EmbedSecretCreatedResponse,
    EmbedSecretResponse,
    EmbedSecretUpdate,
)
from src.models.orm.form_embed_secrets import FormEmbedSecret
from src.models.orm.forms import Form
from shared.sdk_forms import check_form_access
from src.services.authorization.enforce import load_caller, require_permission, user_reach

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/forms/{form_id}/embed-secrets",
    tags=["Form Embed Secrets"],
)


async def _get_form_or_404(ctx: Context, form_id: UUID) -> Form:
    result = await ctx.db.execute(select(Form).where(Form.id == form_id))
    form = result.scalar_one_or_none()
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")
    return form


async def _require_embed_secret_permission(
    ctx: Context, user: CurrentUser, form_id: UUID, permission: str
) -> Form:
    """Apply normal Form sharing plus a named permission at the actual row."""
    form = await _get_form_or_404(ctx, form_id)
    caller = await load_caller(ctx.db, user)
    if not user_reach(caller).covers(form.organization_id):
        raise HTTPException(status_code=404, detail="Form not found")
    if not await check_form_access(ctx.db, form, caller):
        raise HTTPException(status_code=403, detail="Access denied to form")
    require_permission(caller, permission, form.organization_id)
    return form


@router.post(
    "",
    response_model=EmbedSecretCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an embed secret for a form",
)
async def create_form_embed_secret(
    body: EmbedSecretCreate,
    ctx: Context,
    current_user: CurrentUser,
    form_id: UUID = Path(...),
):
    await _require_embed_secret_permission(ctx, current_user, form_id, "settings.readwrite")
    await _require_embed_secret_permission(ctx, current_user, form_id, "secrets.read")

    raw_secret = body.secret or secrets.token_urlsafe(32)
    encrypted = encrypt_secret(raw_secret)

    record = FormEmbedSecret(
        form_id=form_id,
        name=body.name,
        secret_encrypted=encrypted,
        hmac_scheme=body.hmac_scheme,
        created_by=current_user.user_id,
    )
    ctx.db.add(record)
    await ctx.db.commit()
    await ctx.db.refresh(record)

    return EmbedSecretCreatedResponse(
        id=str(record.id),
        name=record.name,
        is_active=record.is_active,
        hmac_scheme=record.hmac_scheme,  # type: ignore[arg-type]
        created_at=record.created_at,
        raw_secret=raw_secret,
    )


@router.get(
    "",
    response_model=list[EmbedSecretResponse],
    summary="List embed secrets for a form",
)
async def list_form_embed_secrets(
    ctx: Context,
    _user: CurrentUser,
    form_id: UUID = Path(...),
):
    # This response deliberately excludes raw secret material.  It is form
    # configuration metadata, so keep its gate aligned with the other HMAC
    # settings operations.  Creating a secret remains separately gated by
    # ``secrets.read`` because it returns the raw value once.
    await _require_embed_secret_permission(ctx, _user, form_id, "settings.readwrite")

    result = await ctx.db.execute(
        select(FormEmbedSecret)
        .where(FormEmbedSecret.form_id == form_id)
        .order_by(FormEmbedSecret.created_at.desc())
    )
    records = result.scalars().all()

    return [
        EmbedSecretResponse(
            id=str(r.id),
            name=r.name,
            is_active=r.is_active,
            hmac_scheme=r.hmac_scheme,  # type: ignore[arg-type]
            created_at=r.created_at,
        )
        for r in records
    ]


@router.patch(
    "/{secret_id}",
    response_model=EmbedSecretResponse,
    summary="Update a form embed secret",
)
async def update_form_embed_secret(
    body: EmbedSecretUpdate,
    ctx: Context,
    _user: CurrentUser,
    form_id: UUID = Path(...),
    secret_id: UUID = Path(...),
):
    await _require_embed_secret_permission(ctx, _user, form_id, "settings.readwrite")
    result = await ctx.db.execute(
        select(FormEmbedSecret).where(
            FormEmbedSecret.id == secret_id,
            FormEmbedSecret.form_id == form_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Embed secret not found")

    if body.is_active is not None:
        record.is_active = body.is_active
    if body.name is not None:
        record.name = body.name
    if body.hmac_scheme is not None:
        record.hmac_scheme = body.hmac_scheme

    await ctx.db.commit()
    await ctx.db.refresh(record)

    return EmbedSecretResponse(
        id=str(record.id),
        name=record.name,
        is_active=record.is_active,
        hmac_scheme=record.hmac_scheme,  # type: ignore[arg-type]
        created_at=record.created_at,
    )


@router.delete(
    "/{secret_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a form embed secret",
)
async def delete_form_embed_secret(
    ctx: Context,
    _user: CurrentUser,
    form_id: UUID = Path(...),
    secret_id: UUID = Path(...),
):
    await _require_embed_secret_permission(ctx, _user, form_id, "settings.readwrite")
    result = await ctx.db.execute(
        select(FormEmbedSecret).where(
            FormEmbedSecret.id == secret_id,
            FormEmbedSecret.form_id == form_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Embed secret not found")

    await ctx.db.delete(record)
    await ctx.db.commit()
