"""
LLM Configuration Admin Router

Admin endpoints for managing LLM provider configuration.
Requires platform admin access.
"""

import logging

from fastapi import APIRouter, HTTPException, status

from src.core.auth import CurrentActiveUser, RequirePlatformAdmin
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.models.contracts.llm import (
    EmbeddingConfigRequest,
    EmbeddingConfigResponse,
    EmbeddingConfigSaveResponse,
    EmbeddingReindexResponse,
    EmbeddingTestRequest,
    EmbeddingTestResponse,
)
from src.models.contracts.artifacts import (
    ModelCapabilityLookupRequest,
    ModelCapabilityLookupResponse,
    ModelCapabilityVerifyRequest,
)
from src.services.ai_model_service import AIModelService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/llm",
    tags=["LLM Configuration"],
    dependencies=[RequirePlatformAdmin],  # All endpoints require platform admin
)


@router.post("/model-capabilities")
async def discover_model_capabilities(
    request: ModelCapabilityLookupRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> ModelCapabilityLookupResponse:
    """Look up model features without trusting provider model-list labels."""
    del db, user
    from src.services.model_capabilities import lookup_model_capabilities

    capabilities, message = await lookup_model_capabilities(
        provider=request.provider,
        model=request.model,
        endpoint=request.endpoint,
    )
    return ModelCapabilityLookupResponse(capabilities=capabilities, message=message)


@router.post("/model-capabilities/verify")
async def verify_model_capability_support(
    request: ModelCapabilityVerifyRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> ModelCapabilityLookupResponse:
    """Run a bounded, one-time provider conformance check for an unknown model."""
    del user
    from src.services.llm.factory import get_llm_config
    from src.services.model_capabilities import verify_model_capabilities

    api_key = request.api_key
    if not api_key:
        try:
            saved = await get_llm_config(db)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Enter an API key or save the provider configuration before verification.",
            ) from exc
        if saved.provider != request.provider:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The saved API key belongs to a different provider.",
            )
        api_key = saved.api_key

    try:
        capabilities, message = await verify_model_capabilities(
            provider=request.provider,
            model=request.model,
            endpoint=request.endpoint,
            api_key=api_key,
        )
    except Exception as exc:
        logger.info("Model capability verification failed: %s", log_safe(exc))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The model did not complete the capability verification. Confirm the endpoint, key, and model, then retry.",
        ) from exc
    return ModelCapabilityLookupResponse(capabilities=capabilities, message=message)


# =============================================================================
# Embedding Configuration Endpoints
# =============================================================================


DEFAULT_OPENAI_ENDPOINT = "https://api.openai.com/v1"


def _normalize_endpoint(value: str | None) -> str | None:
    """Empty string and OpenAI's default URL both collapse to None."""
    if not value:
        return None
    trimmed = value.rstrip("/")
    if trimmed == DEFAULT_OPENAI_ENDPOINT.rstrip("/"):
        return None
    return trimmed


@router.get("/embedding-config")
async def get_embedding_config_endpoint(
    db: DbSession,
    user: CurrentActiveUser,
) -> EmbeddingConfigResponse:
    """
    Get current embedding configuration.

    Returns the configuration and indicates whether it uses a dedicated key
    or falls back to the LLM provider's key. The `endpoint` field is the
    resolved endpoint (dedicated → inherited LLM → null = OpenAI default).
    Requires platform admin access.
    """
    service = AIModelService(db)
    config = await service.get_embedding_config_row()
    if config:
        return EmbeddingConfigResponse(
            connection_id=config.connection_id,
            model=config.model,
            dimensions=config.dimensions,
            endpoint=service.embedding_client_endpoint(config.connection.provider, config.connection.endpoint),
            is_configured=True,
            api_key_set=bool(config.connection.encrypted_api_key),
            uses_llm_key=False,
        )

    # No dedicated embedding config. Don't claim "configured" based on the
    # factory's runtime fallback — the fallback uses an imposed model id the
    # user never picked, which is wrong for any non-stock-OpenAI endpoint and
    # confusing even on stock OpenAI. The UI determines inheritance separately
    # via the LLM provider field; this endpoint just reports "no dedicated".
    return EmbeddingConfigResponse(
        connection_id=None,
        model="",
        dimensions=1536,
        endpoint=None,
        is_configured=False,
        api_key_set=False,
        uses_llm_key=False,
    )


@router.post("/embedding-config", status_code=status.HTTP_200_OK)
async def set_embedding_config(
    request: EmbeddingConfigRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> EmbeddingConfigSaveResponse:
    """
    Set dedicated embedding configuration.

    Validates the configuration by running a live embedding call before
    persisting; captures the returned vector dimensions.

    If the new model's output dimension differs from the currently-saved
    dimension AND knowledge_store has existing rows, the response carries
    `needs_reindex_confirmation=True` and persistence is skipped — re-POST
    with `confirm_reindex: true` to commit the new config and trigger a
    reindex via the scheduler.
    Requires platform admin access.
    """
    from src.models.contracts.notifications import (
        NotificationCategory,
        NotificationCreate,
        NotificationStatus,
    )
    from src.jobs.platform.embedding_reindex import (
        EMBEDDING_REINDEX_DEFINITION,
        EmbeddingReindexPayload,
    )
    from src.services.platform_jobs import enqueue_platform_job, publish_platform_job_update
    from src.services.embeddings.base import EmbeddingConfig as EmbeddingClientConfig
    from src.services.embeddings.openai_client import OpenAIEmbeddingClient
    from src.services.embeddings.reindex import (
        count_knowledge_rows_at_other_dims,
    )
    from src.services.notification_service import get_notification_service

    service = AIModelService(db)
    existing = await service.get_embedding_config_row()

    try:
        connection = await service.get_connection(request.connection_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if service.client_provider(connection.provider) != "openai":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Embedding configuration requires an OpenAI-compatible provider connection",
        )
    decrypted_key = service.decrypt_api_key(connection.encrypted_api_key)
    normalized_endpoint = service.embedding_client_endpoint(connection.provider, connection.endpoint)

    # SSRF guard: reject endpoints that resolve to private/loopback addresses
    # unless the host is explicitly opted-in via EMBEDDING_ALLOWED_HOSTS.
    # See url_safety.validate_embedding_endpoint for rationale.
    if normalized_endpoint:
        from src.services.embeddings.url_safety import validate_embedding_endpoint

        try:
            validate_embedding_endpoint(normalized_endpoint)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Embedding endpoint rejected: {e}",
            ) from e

    # Live-test the config before saving so we never persist something that doesn't work.
    try:
        client = OpenAIEmbeddingClient(
            EmbeddingClientConfig(
                api_key=decrypted_key,
                model=request.model,
                endpoint=normalized_endpoint,
            )
        )
        embedding = await client.embed_single("test connection")
        dimensions = len(embedding)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Embedding test failed; configuration not saved: {e}",
        ) from e

    # knowledge_store.embedding is unconstrained `vector` (migration
    # 20260506_knowledge_dim), so any dim *stores* fine. The real failure mode
    # is at *query* time: a query embedded with the new model can't be compared
    # to old rows when dims differ, and even at matching dims the two models
    # live in different vector spaces (similarity scores are noise).
    #
    # Reindex policy: ground the prompt in *DB state*, not config diff.
    # If the table has any row at a dim other than the new one — whether
    # left over from a previous failed reindex, a never-completed migration,
    # or a config that was saved without recording its dim — fire the
    # confirmation. The previous config-vs-config diff missed the resave
    # case (issue #198): same config saved twice with stale rows still in
    # the table never prompted.
    old_dim: int | None = None
    old_model: str | None = None
    if existing:
        old_dim = existing.dimensions
        old_model = existing.model

    if not request.confirm_reindex:
        stale_count = await count_knowledge_rows_at_other_dims(dimensions)
        if stale_count > 0:
            return EmbeddingConfigSaveResponse(
                saved=False,
                needs_reindex_confirmation=True,
                reason="stale_rows",
                old_dim=old_dim,
                new_dim=dimensions,
                old_model=old_model,
                new_model=request.model,
                row_count=stale_count,
            )

    config = await service.set_embedding_config(
        connection_id=connection.id,
        model=request.model,
        dimensions=dimensions,
    )
    api_key_set = bool(config.connection.encrypted_api_key)

    await db.commit()

    logger.info(
        f"Embedding config updated by {user.email}: model={log_safe(request.model)}, "
        f"endpoint={log_safe(normalized_endpoint or 'default')}"
    )

    saved_config = EmbeddingConfigResponse(
        connection_id=config.connection_id,
        model=request.model,
        dimensions=dimensions,
        endpoint=service.embedding_client_endpoint(config.connection.provider, config.connection.endpoint),
        is_configured=True,
        api_key_set=api_key_set,
        uses_llm_key=False,
    )

    # Trigger reindex when the user clicked through the confirmation dialog.
    # The gate above already verified there are stale rows at non-`dimensions`
    # dims; re-check here so we don't fire a notification for a clean DB.
    notification_id: str | None = None
    if request.confirm_reindex:
        row_count = await count_knowledge_rows_at_other_dims(dimensions)
        if row_count > 0:
            notif_service = get_notification_service()
            notification = await notif_service.create_notification(
                user_id=str(user.user_id),
                request=NotificationCreate(
                    category=NotificationCategory.EMBEDDING_REINDEX,
                    title="Re-embedding knowledge store",
                    description=f"Queued — {row_count} rows to re-embed.",
                    percent=0.0,
                    metadata={
                        "row_count": row_count,
                        "old_model": old_model,
                        "new_model": request.model,
                        "old_dim": old_dim,
                        "new_dim": dimensions,
                    },
                ),
                for_admins=False,
                initial_status=NotificationStatus.PENDING,
            )
            notification_id = notification.id
            platform_job, _ = await enqueue_platform_job(
                db,
                EMBEDDING_REINDEX_DEFINITION,
                EmbeddingReindexPayload(notification_id=notification_id),
                dedupe_key=notification_id,
                resource_lock_key="embedding.reindex",
                priority=250,
                organization_id=None,
                requested_by_user_id=user.user_id,
                requested_by_email=user.email,
                requested_by_name=user.name or user.email or "Unknown",
                resource_type="knowledge_store",
                resource_id="embedding-index",
                title="Re-embedding knowledge store",
                action_url="/settings/llm",
            )
            await db.commit()
            await publish_platform_job_update(platform_job)
            logger.info(
                f"Embedding reindex triggered after save: notification_id={notification_id}, rows={row_count}"
            )

    return EmbeddingConfigSaveResponse(
        saved=True,
        config=saved_config,
        notification_id=notification_id,
    )


@router.delete("/embedding-config", status_code=status.HTTP_204_NO_CONTENT)
async def delete_embedding_config(
    db: DbSession,
    user: CurrentActiveUser,
) -> None:
    """
    Delete dedicated embedding configuration.

    After deletion, embeddings will fall back to using the LLM provider's
    OpenAI key (if available).
    Requires platform admin access.
    """
    deleted = await AIModelService(db).delete_embedding_config()
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Embedding configuration not found",
        )

    await db.commit()

    logger.info(f"Embedding config deleted by {user.email}")


@router.post("/embedding-reindex", response_model=EmbeddingReindexResponse)
async def trigger_embedding_reindex(
    db: DbSession,
    user: CurrentActiveUser,
) -> EmbeddingReindexResponse:
    """
    Re-embed every knowledge_store row against the currently-saved embedding config.

    Returns immediately with a notification_id; progress is delivered over the
    `notification:{user_id}` WebSocket channel. Cancel via
    DELETE /api/notifications/{notification_id}.

    No-op when knowledge_store is empty (returns row_count=0 and no notification
    is created).
    Requires platform admin access.
    """
    from src.jobs.platform.embedding_reindex import (
        EMBEDDING_REINDEX_DEFINITION,
        EmbeddingReindexPayload,
    )
    from src.models.contracts.notifications import (
        NotificationCategory,
        NotificationCreate,
        NotificationStatus,
    )
    from src.services.embeddings.reindex import count_knowledge_rows
    from src.services.notification_service import get_notification_service
    from src.services.platform_jobs import enqueue_platform_job, publish_platform_job_update

    # Confirm an embedding config exists — reindex against nothing is a no-op.
    embedding_config = await AIModelService(db).get_embedding_config_row()
    if not embedding_config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No embedding configuration to reindex against. Save a config first.",
        )

    row_count = await count_knowledge_rows()
    if row_count == 0:
        # Nothing to do — return a synthetic empty notification id so the
        # caller can short-circuit without confusing UI.
        return EmbeddingReindexResponse(notification_id="", row_count=0)

    notif_service = get_notification_service()
    notification = await notif_service.create_notification(
        user_id=str(user.user_id),
        request=NotificationCreate(
            category=NotificationCategory.EMBEDDING_REINDEX,
            title="Re-embedding knowledge store",
            description=f"Queued — {row_count} rows to re-embed.",
            percent=0.0,
            metadata={
                "row_count": row_count,
                "model": embedding_config.model,
                "dim": embedding_config.dimensions,
                "trigger": "on_demand",
            },
        ),
        for_admins=False,
        initial_status=NotificationStatus.PENDING,
    )
    platform_job, _ = await enqueue_platform_job(
        db,
        EMBEDDING_REINDEX_DEFINITION,
        EmbeddingReindexPayload(notification_id=notification.id),
        dedupe_key=notification.id,
        resource_lock_key="embedding.reindex",
        priority=250,
        organization_id=None,
        requested_by_user_id=user.user_id,
        requested_by_email=user.email,
        requested_by_name=user.name or user.email or "Unknown",
        resource_type="knowledge_store",
        resource_id="embedding-index",
        title="Re-embedding knowledge store",
        action_url="/settings/llm",
    )
    await db.commit()
    await publish_platform_job_update(platform_job)
    logger.info(
        f"On-demand embedding reindex triggered by {user.email}: "
        f"notification_id={notification.id}, rows={row_count}"
    )

    return EmbeddingReindexResponse(
        notification_id=notification.id,
        row_count=row_count,
    )


@router.post("/embedding-test")
async def test_embedding_connection(
    request: EmbeddingTestRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> EmbeddingTestResponse:
    """
    Validate credentials and list embedding-capable models.

    Symmetric with the LLM /test endpoint: this is the "does the key work,
    what models are available" call. It does NOT issue an embedding — that's
    Save's job. Save runs the real embeddings.create() against the chosen
    model and rejects with 400 on failure.

    Credential resolution order:
    1. request.api_key + request.endpoint when provided
    2. saved dedicated embedding config (decrypt stored key, use stored endpoint)
    3. LLM provider config — only when provider is openai (Anthropic doesn't
       have embeddings). Inherits both key and endpoint.
    """
    try:
        api_key = request.api_key
        normalized_endpoint = _normalize_endpoint(request.endpoint)

        if not api_key:
            service = AIModelService(db)
            existing = await service.get_embedding_config_row()
            if existing:
                api_key = service.decrypt_api_key(existing.connection.encrypted_api_key)
                if request.endpoint is None:
                    normalized_endpoint = existing.connection.endpoint
            else:
                return EmbeddingTestResponse(
                    success=False,
                    message="No API key provided and no saved key found",
                    dimensions=None,
                )

        # SSRF guard before any outbound call. _list_embedding_models also
        # validates internally for defense-in-depth, but failing here gives
        # the user a clear error message instead of a silent empty model list.
        if normalized_endpoint:
            from src.services.embeddings.url_safety import validate_embedding_endpoint

            try:
                validate_embedding_endpoint(normalized_endpoint)
            except ValueError as ve:
                return EmbeddingTestResponse(
                    success=False,
                    message=f"Endpoint rejected: {ve}",
                    dimensions=None,
                )

        models = await _list_embedding_models(api_key, normalized_endpoint)

        return EmbeddingTestResponse(
            success=True,
            message="Endpoint reachable.",
            dimensions=None,
            models=models,
        )
    except Exception as e:
        logger.warning(f"Embedding test failed: {e}")
        return EmbeddingTestResponse(
            success=False,
            message=f"Connection failed: {str(e)}",
            dimensions=None,
        )


async def _list_embedding_models(api_key: str, endpoint: str | None) -> list[str] | None:
    """
    List embedding-capable models from an OpenAI-compatible endpoint.

    We do the filtering ourselves rather than trusting a server query param:

    - OpenRouter exposes `architecture.output_modalities` on every entry (e.g.
      `["text"]` for chat, `["embeddings"]` for embeddings). If we see that
      field on ANY model, we treat the response as capability-aware and filter
      to entries that advertise embeddings.
    - OpenAI / Azure / Ollama don't expose modality fields. The absence of
      `output_modalities` does NOT mean "no embedding models" — it means we
      don't know. In that case we return the full id list and let the user
      pick. The test call is the final gate; wrong picks fail there.
    - On any HTTP/parse error, return None so the UI falls back to free-text.

    NOTE: OpenRouter's `/v1/models` excludes embedding-only models from its
    default response. We pass `?output_modalities=embeddings` to surface them;
    OpenAI/others ignore the unknown param per HTTP convention. The Python
    filter is the source of truth — we don't trust the server actually filtered.
    """
    import httpx

    from src.services.embeddings.url_safety import validate_embedding_endpoint

    base = (endpoint or DEFAULT_OPENAI_ENDPOINT).rstrip("/")

    # Defense-in-depth on top of admin auth: validate that the endpoint
    # resolves to a public address (or is in EMBEDDING_ALLOWED_HOSTS).
    # Use the validator's return value (not the input `base`) so CodeQL's
    # data-flow analysis sees a cleansed URL flowing into http.get,
    # closing py/partial-ssrf.
    try:
        safe_base = validate_embedding_endpoint(base).rstrip("/")
    except ValueError as e:
        logger.info(f"Refusing to list models from {log_safe(base)}: {e}")
        return None

    url = f"{safe_base}/models"

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            response = await http.get(
                url,
                params={"output_modalities": "embeddings"},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as e:
        logger.info(f"Could not list models from {log_safe(base)}: {e}")
        return None

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None

    # Capability-aware iff at least one entry exposes architecture.output_modalities.
    # Absent on every entry = the endpoint doesn't tell us; we can't filter.
    capability_aware = any(
        isinstance(item, dict)
        and isinstance(item.get("architecture"), dict)
        and "output_modalities" in item["architecture"]
        for item in data
    )

    ids: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not isinstance(model_id, str):
            continue
        if capability_aware:
            arch = item.get("architecture") or {}
            modalities = arch.get("output_modalities") or []
            if not isinstance(modalities, list):
                continue
            if not any(
                isinstance(m, str) and m.lower() == "embeddings"
                for m in modalities
            ):
                continue
        ids.append(model_id)

    return ids or None
