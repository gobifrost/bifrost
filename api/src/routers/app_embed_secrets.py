"""CRUD endpoints for app embed secrets."""

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
from src.models.orm.app_embed_secrets import AppEmbedSecret
from src.models.orm.applications import Application
from src.services.audit import emit_audit
from src.services.authorization import CurrentAuthorizationContext

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/applications/{app_id}/embed-secrets",
    tags=["App Embed Secrets"],
)


async def _get_app_or_404(ctx: Context, app_id: UUID) -> Application:
    result = await ctx.db.execute(select(Application).where(Application.id == app_id))
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


async def _authorized_app(
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    app_id: UUID,
) -> Application:
    authorization.require("apps.readwrite")
    app = await _get_app_or_404(ctx, app_id)
    authorization.require_resource_boundary(app.organization_id)
    return app


@router.post(
    "",
    response_model=EmbedSecretCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an embed secret for an app",
)
async def create_embed_secret(
    body: EmbedSecretCreate,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    app_id: UUID = Path(...),
):
    app = await _authorized_app(ctx, authorization, app_id)

    raw_secret = body.secret or secrets.token_urlsafe(32)
    encrypted = encrypt_secret(raw_secret)

    record = AppEmbedSecret(
        application_id=app_id,
        name=body.name,
        secret_encrypted=encrypted,
        hmac_scheme=body.hmac_scheme,
        created_by=authorization.effective_actor.user_id,
    )
    ctx.db.add(record)
    await emit_audit(
        ctx.db,
        "app.embed_secret.create",
        resource_type="application",
        resource_id=app.id,
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
    summary="List embed secrets for an app",
)
async def list_embed_secrets(
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    app_id: UUID = Path(...),
):
    await _authorized_app(ctx, authorization, app_id)

    result = await ctx.db.execute(
        select(AppEmbedSecret)
        .where(AppEmbedSecret.application_id == app_id)
        .order_by(AppEmbedSecret.created_at.desc())
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
    summary="Update an embed secret",
)
async def update_embed_secret(
    body: EmbedSecretUpdate,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    app_id: UUID = Path(...),
    secret_id: UUID = Path(...),
):
    app = await _authorized_app(ctx, authorization, app_id)
    result = await ctx.db.execute(
        select(AppEmbedSecret).where(
            AppEmbedSecret.id == secret_id,
            AppEmbedSecret.application_id == app_id,
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
        "app.embed_secret.update",
        resource_type="application",
        resource_id=app.id,
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
    summary="Delete an embed secret",
)
async def delete_embed_secret(
    ctx: Context,
    authorization: CurrentAuthorizationContext,
    app_id: UUID = Path(...),
    secret_id: UUID = Path(...),
):
    app = await _authorized_app(ctx, authorization, app_id)
    result = await ctx.db.execute(
        select(AppEmbedSecret).where(
            AppEmbedSecret.id == secret_id,
            AppEmbedSecret.application_id == app_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Embed secret not found")

    await ctx.db.delete(record)
    await emit_audit(
        ctx.db,
        "app.embed_secret.delete",
        resource_type="application",
        resource_id=app.id,
        details={"secret_id": str(record.id), "name": record.name},
    )
    await ctx.db.commit()
