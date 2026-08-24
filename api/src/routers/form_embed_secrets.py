"""CRUD endpoints for form embed secrets."""

import logging
import secrets
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, status
from sqlalchemy import select

from src.core.auth import Context
from src.core.security import encrypt_secret
from src.models.contracts.applications import (
    EmbedSecretCreate,
    EmbedSecretCreatedResponse,
    EmbedSecretResponse,
    EmbedSecretUpdate,
)
from src.models.orm.form_embed_secrets import FormEmbedSecret
from src.models.orm.forms import Form
from src.services.audit import emit_audit
from src.services.authorization import CurrentAuthorizationContext

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


async def _authorized_form(
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    form_id: UUID,
) -> Form:
    authorization.require("forms.readwrite")
    form = await _get_form_or_404(ctx, form_id)
    authorization.require_resource_boundary(form.organization_id)
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
    authorization: CurrentAuthorizationContext,
    form_id: UUID = Path(...),
):
    form = await _authorized_form(ctx, authorization, form_id)

    raw_secret = body.secret or secrets.token_urlsafe(32)
    encrypted = encrypt_secret(raw_secret)

    record = FormEmbedSecret(
        form_id=form_id,
        name=body.name,
        secret_encrypted=encrypted,
        hmac_scheme=body.hmac_scheme,
        created_by=authorization.effective_actor.user_id,
    )
    ctx.db.add(record)
    await emit_audit(
        ctx.db,
        "form.embed_secret.create",
        resource_type="form",
        resource_id=form.id,
        details={"secret_id": str(record.id), "name": record.name},
    )
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
    authorization: CurrentAuthorizationContext,
    form_id: UUID = Path(...),
):
    await _authorized_form(ctx, authorization, form_id)

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
    authorization: CurrentAuthorizationContext,
    form_id: UUID = Path(...),
    secret_id: UUID = Path(...),
):
    form = await _authorized_form(ctx, authorization, form_id)
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

    await emit_audit(
        ctx.db,
        "form.embed_secret.update",
        resource_type="form",
        resource_id=form.id,
        details={"secret_id": str(record.id), "name": record.name},
    )
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
    authorization: CurrentAuthorizationContext,
    form_id: UUID = Path(...),
    secret_id: UUID = Path(...),
):
    form = await _authorized_form(ctx, authorization, form_id)
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
    await emit_audit(
        ctx.db,
        "form.embed_secret.delete",
        resource_type="form",
        resource_id=form.id,
        details={"secret_id": str(record.id), "name": record.name},
    )
    await ctx.db.commit()
