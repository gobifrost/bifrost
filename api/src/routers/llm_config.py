"""
LLM Configuration Admin Router

Admin endpoints for managing LLM provider configuration.
Requires platform admin access.
"""

import logging

from fastapi import APIRouter, HTTPException, status

from src.core.auth import CurrentActiveUser, RequirePlatformAdmin
from src.core.database import DbSession
from src.core.log_safety import log_safe
from src.models.contracts.llm import (
    EmbeddingConfigRequest,
    EmbeddingConfigResponse,
    EmbeddingConfigSaveResponse,
    EmbeddingReindexResponse,
    EmbeddingTestRequest,
    EmbeddingTestResponse,
    LLMConfigRequest,
    LLMConfigResponse,
    LLMModelInfo,
    LLMModelsResponse,
    LLMTestRequest,
    LLMTestResponse,
)
from src.services.embeddings.factory import (
    EMBEDDING_CONFIG_CATEGORY,
    EMBEDDING_CONFIG_KEY,
    LLM_CONFIG_CATEGORY,
    LLM_CONFIG_KEY,
)
from src.services.llm_config_service import LLMConfigService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/llm",
    tags=["LLM Configuration"],
    dependencies=[RequirePlatformAdmin],  # All endpoints require platform admin
)


@router.get("/config")
async def get_llm_config(
    db: DbSession,
    user: CurrentActiveUser,
) -> LLMConfigResponse | None:
    """
    Get current LLM provider configuration.

    Returns the configuration without the API key (only indicates if it's set).
    Requires platform admin access.
    """
    service = LLMConfigService(db)
    config = await service.get_config()

    if not config:
        return None

    return LLMConfigResponse(
        provider=config.provider,
        model=config.model,
        endpoint=config.endpoint,
        max_tokens=config.max_tokens,
        default_system_prompt=config.default_system_prompt,
        summarization_model=config.summarization_model,
        tuning_model=config.tuning_model,
        is_configured=config.is_configured,
        api_key_set=config.api_key_set,
    )


@router.post("/config", status_code=status.HTTP_200_OK)
async def set_llm_config(
    request: LLMConfigRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> LLMConfigResponse:
    """
    Set LLM provider configuration.

    The API key will be encrypted before storage.
    Requires platform admin access.
    """
    service = LLMConfigService(db)

    try:
        await service.save_config(
            provider=request.provider,
            model=request.model,
            api_key=request.api_key,
            endpoint=request.endpoint,
            max_tokens=request.max_tokens,
            default_system_prompt=request.default_system_prompt,
            summarization_model=request.summarization_model,
            tuning_model=request.tuning_model,
            updated_by=user.email,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Gate: run a real 1-token completion before committing. If the chosen
    # model can't actually complete (project-scoped key, missing permission,
    # wrong model id), bail loudly here rather than persisting a broken config
    # that silently fails every downstream summarizer/tuning call.
    await db.flush()
    completion_result = await service.verify_completion()
    if not completion_result.success:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Configuration not saved — {completion_result.message}",
        )

    await db.commit()

    logger.info(f"LLM config updated by {user.email}: provider={log_safe(request.provider)}, model={log_safe(request.model)}")

    # Auto-sync pricing from provider if using a custom endpoint
    if request.endpoint:
        try:
            from src.services.llm.factory import get_llm_config as get_decrypted_config

            llm_config = await get_decrypted_config(db)
            count = await service.sync_provider_pricing(
                provider=request.provider,
                model=request.model,
                api_key=llm_config.api_key,
                endpoint=request.endpoint,
            )
            await db.commit()
            if count:
                logger.info(f"Synced pricing for {count} models from {log_safe(request.endpoint)}")
        except Exception as e:
            await db.rollback()
            logger.warning(f"Failed to sync provider pricing: {e}")

    # Determine if API key is set: either a new key was provided, or an existing one was preserved
    config = await service.get_config()
    api_key_set = bool(config and config.api_key_set)

    return LLMConfigResponse(
        provider=request.provider,
        model=request.model,
        endpoint=request.endpoint,
        max_tokens=request.max_tokens,
        default_system_prompt=request.default_system_prompt,
        summarization_model=request.summarization_model,
        tuning_model=request.tuning_model,
        is_configured=True,
        api_key_set=api_key_set,
    )


@router.delete("/config", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_config(
    db: DbSession,
    user: CurrentActiveUser,
) -> None:
    """
    Delete LLM provider configuration.

    Requires platform admin access.
    """
    service = LLMConfigService(db)
    deleted = await service.delete_config()

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LLM configuration not found",
        )

    await db.commit()
    logger.info(f"LLM config deleted by {user.email}")


@router.post("/test")
async def test_llm_connection(
    request: LLMTestRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> LLMTestResponse:
    """
    Test LLM connection with provided credentials.

    Tests the connection without saving the configuration.
    Useful for validating API keys before committing.
    Also caches the model ID -> display name mapping for AI usage tracking.
    Requires platform admin access.
    """
    service = LLMConfigService(db)

    # Temporarily save the config to test
    # We'll roll back the transaction so it's not persisted
    await service.save_config(
        provider=request.provider,
        model=request.model,
        api_key=request.api_key,
        endpoint=request.endpoint,
        updated_by=user.email,
    )

    result = await service.test_connection()

    # Rollback to not persist the test config
    await db.rollback()

    # Cache model mapping for AI usage tracking (even if test-only)
    if result.success and result.models:
        await _cache_model_mapping_from_result(request.provider, result.models)

    return LLMTestResponse(
        success=result.success,
        message=result.message,
        models=[LLMModelInfo(id=m.id, display_name=m.display_name) for m in result.models] if result.models else None,
    )


@router.post("/test-saved")
async def test_saved_llm_connection(
    db: DbSession,
    user: CurrentActiveUser,
) -> LLMTestResponse:
    """
    Test connection using saved LLM configuration.

    Tests the currently saved configuration.
    Also refreshes the model ID -> display name mapping cache.
    Requires platform admin access.
    """
    service = LLMConfigService(db)
    config = await service.get_config()

    if not config or not config.is_configured:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LLM configuration not found",
        )

    result = await service.test_connection()

    # Cache model mapping for AI usage tracking
    if result.success and result.models:
        await _cache_model_mapping_from_result(config.provider, result.models)

    return LLMTestResponse(
        success=result.success,
        message=result.message,
        models=[LLMModelInfo(id=m.id, display_name=m.display_name) for m in result.models] if result.models else None,
    )


@router.get("/models")
async def list_llm_models(
    db: DbSession,
    user: CurrentActiveUser,
) -> LLMModelsResponse:
    """
    List available models from the configured LLM provider.

    Works with OpenAI and Anthropic (both support model listing).
    Requires platform admin access.
    """
    service = LLMConfigService(db)
    config = await service.get_config()

    if not config or not config.is_configured:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LLM configuration not found",
        )

    models = await service.list_models()

    if models is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not retrieve models from provider",
        )

    return LLMModelsResponse(
        models=[LLMModelInfo(id=m.id, display_name=m.display_name) for m in models],
        provider=config.provider,
    )


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
    from sqlalchemy import select
    from src.models.orm import SystemConfig

    # Check for dedicated embedding config
    result = await db.execute(
        select(SystemConfig).where(
            SystemConfig.category == EMBEDDING_CONFIG_CATEGORY,
            SystemConfig.key == EMBEDDING_CONFIG_KEY,
            SystemConfig.organization_id.is_(None),
        )
    )
    embedding_config = result.scalars().first()

    if embedding_config and embedding_config.value_json:
        config_data = embedding_config.value_json
        return EmbeddingConfigResponse(
            model=config_data.get("model", ""),
            dimensions=config_data.get("dimensions", 1536),
            endpoint=config_data.get("endpoint"),
            is_configured=True,
            api_key_set=bool(config_data.get("encrypted_api_key")),
            uses_llm_key=False,
        )

    # No dedicated embedding config. Don't claim "configured" based on the
    # factory's runtime fallback — the fallback uses an imposed model id the
    # user never picked, which is wrong for any non-stock-OpenAI endpoint and
    # confusing even on stock OpenAI. The UI determines inheritance separately
    # via the LLM provider field; this endpoint just reports "no dedicated".
    return EmbeddingConfigResponse(
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
    import base64
    from cryptography.fernet import Fernet
    from sqlalchemy import select
    from src.config import get_settings
    from src.models.contracts.notifications import (
        NotificationCategory,
        NotificationCreate,
        NotificationStatus,
    )
    from src.core.pubsub import publish_embedding_reindex_request
    from src.models.orm import SystemConfig
    from src.services.embeddings.base import EmbeddingConfig as EmbeddingClientConfig
    from src.services.embeddings.openai_client import OpenAIEmbeddingClient
    from src.services.embeddings.reindex import count_knowledge_rows
    from src.services.notification_service import get_notification_service

    settings = get_settings()

    # Upsert the config
    result = await db.execute(
        select(SystemConfig).where(
            SystemConfig.category == EMBEDDING_CONFIG_CATEGORY,
            SystemConfig.key == EMBEDDING_CONFIG_KEY,
            SystemConfig.organization_id.is_(None),
        )
    )
    existing = result.scalars().first()

    # Determine encrypted API key + decrypted key for the live test.
    # Resolution order matches /embedding-test:
    #   1. request.api_key (user typed a key in override mode)
    #   2. existing dedicated saved key (re-saving an existing config)
    #   3. LLM provider key — only when provider is openai (inherit mode)
    key_bytes = settings.secret_key.encode()[:32].ljust(32, b"0")
    fernet = Fernet(base64.urlsafe_b64encode(key_bytes))
    normalized_endpoint = _normalize_endpoint(request.endpoint)

    if request.api_key:
        encrypted_key = fernet.encrypt(request.api_key.encode()).decode()
        decrypted_key = request.api_key
    elif existing and existing.value_json and existing.value_json.get("encrypted_api_key"):
        encrypted_key = existing.value_json["encrypted_api_key"]
        decrypted_key = fernet.decrypt(encrypted_key.encode()).decode()
    else:
        # Inherit from LLM provider when openai-compatible.
        from sqlalchemy import select as sa_select

        llm_result = await db.execute(
            sa_select(SystemConfig).where(
                SystemConfig.category == LLM_CONFIG_CATEGORY,
                SystemConfig.key == LLM_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
        llm_row = llm_result.scalars().first()
        if (
            llm_row
            and llm_row.value_json
            and llm_row.value_json.get("provider") == "openai"
            and llm_row.value_json.get("encrypted_api_key")
        ):
            encrypted_key = llm_row.value_json["encrypted_api_key"]
            decrypted_key = fernet.decrypt(encrypted_key.encode()).decode()
            # If the user didn't override the endpoint, inherit it too.
            if request.endpoint is None:
                normalized_endpoint = _normalize_endpoint(
                    llm_row.value_json.get("endpoint")
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="API key is required for initial embedding configuration",
            )

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
    # Reindex policy:
    #   - dim matches saved dim → skip reindex (deliberate trade-off; the
    #     "Reindex knowledge store" button is right there if the user wants it).
    #   - dim differs AND rows > 0 → require confirm_reindex=true; otherwise
    #     return needs_reindex_confirmation and don't persist.
    old_dim: int | None = None
    old_model: str | None = None
    if existing and existing.value_json:
        old_dim = existing.value_json.get("dimensions")
        old_model = existing.value_json.get("model")

    dim_changed = old_dim is not None and old_dim != dimensions
    if dim_changed and not request.confirm_reindex:
        row_count = await count_knowledge_rows()
        if row_count > 0:
            return EmbeddingConfigSaveResponse(
                saved=False,
                needs_reindex_confirmation=True,
                reason="dim_change",
                old_dim=old_dim,
                new_dim=dimensions,
                old_model=old_model,
                new_model=request.model,
                row_count=row_count,
            )

    config_data: dict[str, object] = {
        "model": request.model,
        "dimensions": dimensions,
        "encrypted_api_key": encrypted_key,
    }
    if normalized_endpoint:
        config_data["endpoint"] = normalized_endpoint

    api_key_set = bool(encrypted_key)

    if existing:
        existing.value_json = config_data
        existing.updated_by = user.email
    else:
        new_config = SystemConfig(
            category=EMBEDDING_CONFIG_CATEGORY,
            key=EMBEDDING_CONFIG_KEY,
            value_json=config_data,
            created_by=user.email,
            updated_by=user.email,
        )
        db.add(new_config)

    await db.commit()

    logger.info(
        f"Embedding config updated by {user.email}: model={log_safe(request.model)}, "
        f"endpoint={log_safe(normalized_endpoint or 'default')}"
    )

    saved_config = EmbeddingConfigResponse(
        model=request.model,
        dimensions=dimensions,
        endpoint=normalized_endpoint,
        is_configured=True,
        api_key_set=api_key_set,
        uses_llm_key=False,
    )

    # Trigger reindex when the user confirmed a dim change.
    notification_id: str | None = None
    if dim_changed and request.confirm_reindex:
        row_count = await count_knowledge_rows()
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
            await publish_embedding_reindex_request(notification_id)
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
    from sqlalchemy import select
    from src.models.orm import SystemConfig

    result = await db.execute(
        select(SystemConfig).where(
            SystemConfig.category == EMBEDDING_CONFIG_CATEGORY,
            SystemConfig.key == EMBEDDING_CONFIG_KEY,
            SystemConfig.organization_id.is_(None),
        )
    )
    existing = result.scalars().first()

    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Embedding configuration not found",
        )

    await db.delete(existing)
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
    from sqlalchemy import select
    from src.core.pubsub import publish_embedding_reindex_request
    from src.models.contracts.notifications import (
        NotificationCategory,
        NotificationCreate,
        NotificationStatus,
    )
    from src.models.orm import SystemConfig
    from src.services.embeddings.reindex import count_knowledge_rows
    from src.services.notification_service import get_notification_service

    # Confirm an embedding config exists — reindex against nothing is a no-op.
    config_result = await db.execute(
        select(SystemConfig).where(
            SystemConfig.category == EMBEDDING_CONFIG_CATEGORY,
            SystemConfig.key == EMBEDDING_CONFIG_KEY,
            SystemConfig.organization_id.is_(None),
        )
    )
    embedding_config = config_result.scalars().first()
    if not embedding_config or not embedding_config.value_json:
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
                "model": embedding_config.value_json.get("model"),
                "dim": embedding_config.value_json.get("dimensions"),
                "trigger": "on_demand",
            },
        ),
        for_admins=False,
        initial_status=NotificationStatus.PENDING,
    )
    await publish_embedding_reindex_request(notification.id)
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
    import base64
    from cryptography.fernet import Fernet
    from src.config import get_settings

    try:
        api_key = request.api_key
        normalized_endpoint = _normalize_endpoint(request.endpoint)

        if not api_key:
            from sqlalchemy import select as sa_select
            from src.models.orm import SystemConfig

            settings = get_settings()
            key_bytes = settings.secret_key.encode()[:32].ljust(32, b"0")
            fernet = Fernet(base64.urlsafe_b64encode(key_bytes))

            # Saved dedicated embedding config first.
            result = await db.execute(
                sa_select(SystemConfig).where(
                    SystemConfig.category == EMBEDDING_CONFIG_CATEGORY,
                    SystemConfig.key == EMBEDDING_CONFIG_KEY,
                    SystemConfig.organization_id.is_(None),
                )
            )
            existing = result.scalars().first()
            if existing and existing.value_json and existing.value_json.get("encrypted_api_key"):
                api_key = fernet.decrypt(existing.value_json["encrypted_api_key"].encode()).decode()
                if request.endpoint is None:
                    normalized_endpoint = existing.value_json.get("endpoint")
            else:
                # Inherit from LLM provider when it's an OpenAI-compatible config.
                llm_result = await db.execute(
                    sa_select(SystemConfig).where(
                        SystemConfig.category == LLM_CONFIG_CATEGORY,
                        SystemConfig.key == LLM_CONFIG_KEY,
                        SystemConfig.organization_id.is_(None),
                    )
                )
                llm_row = llm_result.scalars().first()
                if (
                    llm_row
                    and llm_row.value_json
                    and llm_row.value_json.get("provider") == "openai"
                    and llm_row.value_json.get("encrypted_api_key")
                ):
                    api_key = fernet.decrypt(
                        llm_row.value_json["encrypted_api_key"].encode()
                    ).decode()
                    if request.endpoint is None:
                        normalized_endpoint = llm_row.value_json.get("endpoint")
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


# =============================================================================
# Helper Functions
# =============================================================================


async def _cache_model_mapping_from_result(
    provider: str,
    models: list,
) -> None:
    """
    Cache model ID -> display name mapping from test connection results.

    This populates the model registry cache so that AI usage recording
    can look up display names without making provider API calls.

    Args:
        provider: LLM provider ("openai" or "anthropic")
        models: List of LLMModelInfo dataclasses from test result
    """
    from src.core.cache import get_shared_redis
    from src.services.model_registry import cache_model_mapping

    try:
        redis_client = await get_shared_redis()
        mapping = {m.id: m.display_name for m in models}
        await cache_model_mapping(redis_client, provider, mapping)
    except Exception as e:
        logger.warning(f"Failed to cache model mapping for {log_safe(provider)}: {log_safe(e)}")


