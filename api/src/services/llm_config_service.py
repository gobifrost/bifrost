"""
LLM Configuration Service

Manages LLM provider configuration in system_configs table.
Follows the same pattern as GitHubConfigService for SystemConfig storage.
"""

import asyncio
import base64
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.log_safety import log_safe
from src.models.orm import SystemConfig
from src.models.contracts.artifacts import ModelCapabilities
from src.services.model_capabilities import OPENROUTER_MODELS_URL, model_fingerprint, normalize_capabilities

logger = logging.getLogger(__name__)

# SystemConfig keys (same as factory.py)
LLM_CONFIG_CATEGORY = "llm"
LLM_CONFIG_KEY = "provider_config"


@dataclass
class LLMProviderConfig:
    """LLM provider configuration (API key masked for responses)."""

    provider: Literal["openai", "anthropic", "google"]
    model: str
    endpoint: str | None = None  # For custom OpenAI-compatible providers
    max_tokens: int = 16384
    default_system_prompt: str | None = None  # Default system prompt for agentless chat
    summarization_model: str | None = None  # Override for post-run summarization
    tuning_model: str | None = None  # Override for tuning chat + dry-run
    image_generation_model: str | None = None
    video_generation_model: str | None = None
    chat_fast_label: str = "Fast"
    chat_fast_model: str | None = None
    chat_balanced_label: str = "Balanced"
    chat_balanced_model: str | None = None
    chat_pro_label: str = "Pro"
    chat_pro_model: str | None = None
    chat_fast_capabilities: ModelCapabilities | None = None
    chat_balanced_capabilities: ModelCapabilities | None = None
    chat_pro_capabilities: ModelCapabilities | None = None
    is_configured: bool = False
    api_key_set: bool = False  # Indicates if API key is configured (never return actual key)

    def resolve_chat_model(
        self, tier: Literal["fast", "balanced", "pro"]
    ) -> str:
        """Resolve an enabled Chat tier without exposing provider model IDs."""
        if tier == "balanced":
            return self.chat_balanced_model or self.model
        model = {
            "fast": self.chat_fast_model,
            "pro": self.chat_pro_model,
        }[tier]
        if not model:
            raise ValueError(f"The {tier} Chat model tier is not enabled.")
        return model

    def resolve_chat_capabilities(
        self, tier: Literal["fast", "balanced", "pro"]
    ) -> ModelCapabilities:
        model = self.resolve_chat_model(tier)
        capabilities = {
            "fast": self.chat_fast_capabilities,
            "balanced": self.chat_balanced_capabilities,
            "pro": self.chat_pro_capabilities,
        }[tier]
        return normalize_capabilities(
            capabilities,
            provider=self.provider,
            model=model,
            endpoint=self.endpoint,
        )


@dataclass
class LLMModelInfo:
    """Model information with both ID and display name."""

    id: str
    display_name: str
    output_modalities: list[str] | None = None


def _model_output_modalities(model: object) -> list[str] | None:
    """Read OpenRouter's architecture metadata from an OpenAI SDK model."""
    architecture = getattr(model, "architecture", None)
    if architecture is None:
        model_extra = getattr(model, "model_extra", None)
        if isinstance(model_extra, dict):
            architecture = model_extra.get("architecture")

    if isinstance(architecture, dict):
        raw_modalities = architecture.get("output_modalities")
    else:
        raw_modalities = getattr(architecture, "output_modalities", None)

    if not isinstance(raw_modalities, (list, tuple)):
        return None
    return [str(modality) for modality in raw_modalities]


def _is_openrouter_endpoint(endpoint: str | None) -> bool:
    hostname = urlparse(endpoint or "").hostname
    return hostname == "openrouter.ai" or bool(hostname and hostname.endswith(".openrouter.ai"))


async def _list_openrouter_models(
    api_key: str,
    client: httpx.AsyncClient | None = None,
) -> list[LLMModelInfo]:
    """List every OpenRouter model, including non-text output models."""
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=10.0, follow_redirects=True)
    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        response, image_response, video_response = await asyncio.gather(
            http.get(OPENROUTER_MODELS_URL, headers=headers),
            http.get("https://openrouter.ai/api/v1/images/models", headers=headers),
            http.get("https://openrouter.ai/api/v1/videos/models", headers=headers),
        )
        response.raise_for_status()
        image_response.raise_for_status()
        video_response.raise_for_status()
        payload: dict[str, Any] = response.json()
        records = payload.get("data")
        if not isinstance(records, list):
            raise TypeError("OpenRouter model catalog did not contain a model list.")

        def media_catalog(response: httpx.Response) -> dict[str, str]:
            media_records = response.json().get("data")
            if not isinstance(media_records, list):
                raise TypeError("OpenRouter media catalog did not contain a model list.")
            return {
                record["id"]: str(record.get("name") or record["id"])
                for record in media_records
                if isinstance(record, dict) and isinstance(record.get("id"), str)
            }

        image_models = media_catalog(image_response)
        video_models = media_catalog(video_response)
        models_by_id: dict[str, LLMModelInfo] = {}
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                continue
            architecture = record.get("architecture")
            output_modalities = architecture.get("output_modalities") if isinstance(architecture, dict) else None
            modalities = (
                [
                    str(value)
                    for value in output_modalities
                    if str(value) not in {"image", "video"}
                ]
                if isinstance(output_modalities, list)
                else []
            )
            if record["id"] in image_models:
                modalities.append("image")
            if record["id"] in video_models:
                modalities.append("video")
            models_by_id[record["id"]] = LLMModelInfo(
                id=record["id"],
                display_name=record.get("name") or record["id"],
                output_modalities=modalities,
            )
        for model_id, display_name in image_models.items():
            if model_id not in models_by_id:
                models_by_id[model_id] = LLMModelInfo(
                    id=model_id,
                    display_name=display_name,
                    output_modalities=["image"],
                )
        for model_id, display_name in video_models.items():
            existing = models_by_id.get(model_id)
            if existing is None:
                models_by_id[model_id] = LLMModelInfo(
                    id=model_id,
                    display_name=display_name,
                    output_modalities=["video"],
                )
            elif existing.output_modalities is not None and "video" not in existing.output_modalities:
                existing.output_modalities.append("video")
        return sorted(models_by_id.values(), key=lambda item: item.display_name.casefold())
    finally:
        if owns_client:
            await http.aclose()


@dataclass
class LLMTestResult:
    """Result of testing LLM connection."""

    success: bool
    message: str
    models: list[LLMModelInfo] | None = None  # Available models if provider supports listing


class LLMConfigService:
    """
    Service for managing LLM provider configuration.

    Stores configuration in system_configs table with:
    - category: "llm"
    - key: "provider_config"
    - value_json: JSON object with provider settings
    - organization_id: NULL (global config)

    API keys are encrypted using Fernet (same as GitHub token encryption).
    """

    def __init__(self, session: AsyncSession):
        """Initialize the service with a database session."""
        self.session = session
        self.settings = get_settings()

    def _get_fernet(self) -> Fernet:
        """Get Fernet instance for encryption/decryption."""
        key_bytes = self.settings.secret_key.encode()[:32].ljust(32, b"0")
        return Fernet(base64.urlsafe_b64encode(key_bytes))

    async def get_config(self) -> LLMProviderConfig | None:
        """
        Get current LLM configuration (API key masked).

        Returns:
            LLMProviderConfig with current settings, or None if not configured
        """
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == LLM_CONFIG_CATEGORY,
                SystemConfig.key == LLM_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
        config = result.scalars().first()

        if not config or not config.value_json:
            return None

        config_data = config.value_json

        # Map legacy "custom" provider to "openai"
        provider = config_data.get("provider", "openai")
        if provider == "custom":
            provider = "openai"

        def stored_capabilities(key: str) -> ModelCapabilities | None:
            value = config_data.get(key)
            return ModelCapabilities.model_validate(value) if value else None

        return LLMProviderConfig(
            provider=provider,
            model=config_data.get("model", ""),
            endpoint=config_data.get("endpoint"),
            max_tokens=config_data.get("max_tokens", 16384),
            default_system_prompt=config_data.get("default_system_prompt"),
            summarization_model=config_data.get("summarization_model"),
            tuning_model=config_data.get("tuning_model"),
            image_generation_model=config_data.get("image_generation_model"),
            video_generation_model=config_data.get("video_generation_model"),
            chat_fast_label=config_data.get("chat_fast_label", "Fast"),
            chat_fast_model=config_data.get("chat_fast_model"),
            chat_balanced_label=config_data.get("chat_balanced_label", "Balanced"),
            chat_balanced_model=config_data.get("chat_balanced_model"),
            chat_pro_label=config_data.get("chat_pro_label", "Pro"),
            chat_pro_model=config_data.get("chat_pro_model"),
            chat_fast_capabilities=stored_capabilities("chat_fast_capabilities"),
            chat_balanced_capabilities=stored_capabilities("chat_balanced_capabilities"),
            chat_pro_capabilities=stored_capabilities("chat_pro_capabilities"),
            is_configured=True,
            api_key_set=bool(config_data.get("encrypted_api_key")),
        )

    async def save_config(
        self,
        provider: Literal["openai", "anthropic", "google"],
        model: str,
        api_key: str | None = None,
        endpoint: str | None = None,
        max_tokens: int = 16384,
        default_system_prompt: str | None = None,
        summarization_model: str | None = None,
        tuning_model: str | None = None,
        image_generation_model: str | None = None,
        video_generation_model: str | None = None,
        chat_fast_label: str = "Fast",
        chat_fast_model: str | None = None,
        chat_balanced_label: str = "Balanced",
        chat_balanced_model: str | None = None,
        chat_pro_label: str = "Pro",
        chat_pro_model: str | None = None,
        chat_fast_capabilities: ModelCapabilities | None = None,
        chat_balanced_capabilities: ModelCapabilities | None = None,
        chat_pro_capabilities: ModelCapabilities | None = None,
        updated_by: str = "system",
    ) -> None:
        """
        Save LLM provider configuration.

        Args:
            provider: LLM provider type
            model: Model identifier
            api_key: API key (will be encrypted). If None, preserves existing key.
            endpoint: Custom endpoint URL (for custom providers)
            max_tokens: Maximum tokens for completion
            default_system_prompt: Default system prompt for agentless chat
            summarization_model: Optional override for summarization calls.
                ``None`` means use the primary model.
            tuning_model: Optional override for tuning chat + dry-run calls.
                ``None`` means use the primary model.
            image_generation_model: Optional dedicated image generation model.
            video_generation_model: Optional dedicated video generation model.
            updated_by: Email/ID of user making the change
        """
        fernet = self._get_fernet()

        # Check if config already exists
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == LLM_CONFIG_CATEGORY,
                SystemConfig.key == LLM_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
        existing = result.scalars().first()

        # Determine encrypted API key
        if api_key:
            encrypted_api_key = fernet.encrypt(api_key.encode()).decode()
        elif existing and existing.value_json and existing.value_json.get("encrypted_api_key"):
            encrypted_api_key = existing.value_json["encrypted_api_key"]
        else:
            raise ValueError("API key is required for initial configuration")

        def prepare_capabilities(
            capabilities: ModelCapabilities | None, selected_model: str | None
        ) -> ModelCapabilities | None:
            if not selected_model or capabilities is None:
                return None
            fingerprint = model_fingerprint(
                provider=provider,
                model=selected_model,
                endpoint=endpoint,
            )
            if capabilities.source == "manual":
                return capabilities.model_copy(
                    update={
                        "fingerprint": fingerprint,
                        "checked_at": capabilities.checked_at or datetime.now(timezone.utc),
                    }
                )
            return normalize_capabilities(
                capabilities,
                provider=provider,
                model=selected_model,
                endpoint=endpoint,
            )

        prepared_fast = prepare_capabilities(chat_fast_capabilities, chat_fast_model)
        prepared_balanced = prepare_capabilities(
            chat_balanced_capabilities, chat_balanced_model or model
        )
        prepared_pro = prepare_capabilities(chat_pro_capabilities, chat_pro_model)

        config_data = {
            "provider": provider,
            "model": model,
            "encrypted_api_key": encrypted_api_key,
            "endpoint": endpoint,
            "max_tokens": max_tokens,
            "default_system_prompt": default_system_prompt,
            "summarization_model": summarization_model,
            "tuning_model": tuning_model,
            "image_generation_model": image_generation_model,
            "video_generation_model": video_generation_model,
            "chat_fast_label": chat_fast_label,
            "chat_fast_model": chat_fast_model,
            "chat_balanced_label": chat_balanced_label,
            "chat_balanced_model": chat_balanced_model,
            "chat_pro_label": chat_pro_label,
            "chat_pro_model": chat_pro_model,
            "chat_fast_capabilities": (
                prepared_fast.model_dump(mode="json")
                if prepared_fast
                else None
            ),
            "chat_balanced_capabilities": (
                prepared_balanced.model_dump(mode="json")
                if prepared_balanced
                else None
            ),
            "chat_pro_capabilities": (
                prepared_pro.model_dump(mode="json")
                if prepared_pro
                else None
            ),
        }

        if existing:
            # Update existing config
            existing.value_json = config_data
            existing.updated_at = datetime.now(timezone.utc)
            existing.updated_by = updated_by
            logger.info(f"Updated LLM config: provider={log_safe(provider)}, model={log_safe(model)}")
        else:
            # Create new config
            new_config = SystemConfig(
                id=uuid4(),
                category=LLM_CONFIG_CATEGORY,
                key=LLM_CONFIG_KEY,
                value_json=config_data,
                value_bytes=None,
                organization_id=None,
                created_by=updated_by,
                updated_by=updated_by,
            )
            self.session.add(new_config)
            logger.info(f"Created LLM config: provider={log_safe(provider)}, model={log_safe(model)}")

        await self.session.flush()

    async def delete_config(self) -> bool:
        """
        Delete LLM configuration.

        Returns:
            True if config was deleted, False if it didn't exist
        """
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == LLM_CONFIG_CATEGORY,
                SystemConfig.key == LLM_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
        config = result.scalars().first()

        if config:
            await self.session.delete(config)
            await self.session.flush()
            logger.info("Deleted LLM config")
            return True

        return False

    async def test_connection(self) -> LLMTestResult:
        """
        Validate credentials and list available models.

        Symmetric with the embedding /test endpoint: this confirms the key
        reaches the provider and returns a model list, but does NOT issue a
        completion. The completion gate runs at Save time (see
        `verify_completion`), which is the action that persists.
        """
        from src.services.llm.factory import get_llm_config

        try:
            config = await get_llm_config(self.session)

            if config.provider == "openai":
                return await self._list_openai(config.api_key, config.endpoint)
            elif config.provider == "anthropic":
                return await self._list_anthropic(config.api_key, config.endpoint)
            elif config.provider == "google":
                return await self._list_google(config.api_key, config.endpoint)
            else:
                return LLMTestResult(
                    success=False,
                    message=f"Unknown provider: {config.provider}",
                )

        except ValueError as e:
            return LLMTestResult(success=False, message=str(e))
        except Exception as e:
            logger.error(f"LLM connection test failed: {e}")
            return LLMTestResult(success=False, message=f"Connection test failed: {e}")

    async def verify_completion(self) -> LLMTestResult:
        """
        Issue a 1-token completion against the saved config to confirm the
        chosen model actually works for inference. Called by /config Save.

        A key may list models fine but be rejected on chat completions
        (project-scoped keys, missing model permissions). Without this gate,
        Save would persist a broken config.
        """
        from src.services.llm.factory import get_llm_config

        try:
            config = await get_llm_config(self.session)

            if config.provider == "openai":
                return await self._complete_openai(
                    config.api_key, config.model, config.endpoint
                )
            elif config.provider == "anthropic":
                return await self._complete_anthropic(
                    config.api_key, config.model, config.endpoint
                )
            elif config.provider == "google":
                return await self._complete_google(
                    config.api_key, config.model, config.endpoint
                )
            else:
                return LLMTestResult(
                    success=False,
                    message=f"Unknown provider: {config.provider}",
                )

        except ValueError as e:
            return LLMTestResult(success=False, message=str(e))
        except Exception as e:
            logger.error(f"LLM completion verify failed: {e}")
            return LLMTestResult(success=False, message=f"Completion test failed: {e}")

    async def _list_openai(self, api_key: str, endpoint: str | None = None) -> LLMTestResult:
        """List models from an OpenAI-compatible endpoint."""
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key, base_url=endpoint or None)
            endpoint_label = endpoint or "https://api.openai.com/v1"

            try:
                models_response = await client.models.list()
                model_infos = [
                    LLMModelInfo(
                        id=m.id,
                        display_name=m.id,
                        output_modalities=_model_output_modalities(m),
                    )
                    for m in sorted(models_response.data, key=lambda x: x.id)
                ]
                if _is_openrouter_endpoint(endpoint):
                    try:
                        model_infos = await _list_openrouter_models(api_key)
                    except (httpx.HTTPError, TypeError, ValueError) as error:
                        logger.info("OpenRouter's all-modality catalog was unavailable: %s", error)
                return LLMTestResult(
                    success=True,
                    message=f"Connected to {endpoint_label}. Listed {len(model_infos)} model(s).",
                    models=model_infos,
                )
            except Exception as e:
                error_str = str(e).lower()
                if any(
                    tok in error_str
                    for tok in ("401", "403", "unauthorized", "forbidden", "authentication", "invalid")
                ):
                    return LLMTestResult(
                        success=False,
                        message=f"Authentication failed at {endpoint_label}: {e}",
                    )
                # Listing not supported — that's OK, key still seems live.
                logger.info(f"Model listing not supported at {endpoint_label}: {e}")
                return LLMTestResult(
                    success=True,
                    message=f"Connected to {endpoint_label}. Model listing not available — enter the model id manually.",
                    models=None,
                )
        except Exception as e:
            return LLMTestResult(success=False, message=f"OpenAI connection failed: {e}")

    async def _list_anthropic(self, api_key: str, endpoint: str | None = None) -> LLMTestResult:
        """List models from Anthropic."""
        try:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=api_key, base_url=endpoint or None)
            endpoint_label = endpoint or "https://api.anthropic.com"

            try:
                models_response = await client.models.list()
                seen_display_names: set[str] = set()
                model_infos: list[LLMModelInfo] = []
                for m in sorted(models_response.data, key=lambda x: x.id, reverse=True):
                    display_name = getattr(m, "display_name", m.id)
                    if display_name in seen_display_names:
                        continue
                    seen_display_names.add(display_name)
                    model_infos.append(LLMModelInfo(id=m.id, display_name=display_name))
                model_infos.sort(key=lambda x: x.display_name)

                return LLMTestResult(
                    success=True,
                    message=f"Connected to {endpoint_label}. Listed {len(model_infos)} model(s).",
                    models=model_infos,
                )
            except Exception as e:
                error_str = str(e).lower()
                if any(
                    tok in error_str
                    for tok in ("401", "403", "unauthorized", "forbidden", "authentication", "invalid")
                ):
                    return LLMTestResult(
                        success=False,
                        message=f"Authentication failed at {endpoint_label}: {e}",
                    )
                logger.info(f"Model listing not supported at {endpoint_label}: {e}")
                return LLMTestResult(
                    success=True,
                    message=f"Connected to {endpoint_label}. Model listing not available — enter the model id manually.",
                    models=None,
                )
        except Exception as e:
            return LLMTestResult(success=False, message=f"Anthropic connection failed: {e}")

    async def _list_google(self, api_key: str, endpoint: str | None = None) -> LLMTestResult:
        """List models from the Gemini Developer API."""
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(base_url=endpoint) if endpoint else None,
            )
            try:
                pager = await client.aio.models.list(config={"page_size": 100})
                model_infos = [
                    LLMModelInfo(
                        id=(item.name or "").removeprefix("models/"),
                        display_name=item.display_name or item.name or "Unknown model",
                    )
                    for item in pager.page
                    if item.name
                ]
            finally:
                await client.aio.aclose()

            return LLMTestResult(
                success=True,
                message=f"Connected to Google. Listed {len(model_infos)} model(s).",
                models=model_infos,
            )
        except Exception as e:
            logger.error("Google connection test failed: %s", e)
            return LLMTestResult(success=False, message=f"Google connection failed: {e}")

    async def _complete_openai(
        self, api_key: str, model: str, endpoint: str | None = None
    ) -> LLMTestResult:
        """Issue a 1-token chat completion against an OpenAI-compatible endpoint."""
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key, base_url=endpoint or None)
            endpoint_label = endpoint or "https://api.openai.com/v1"

            await client.chat.completions.create(
                model=model,
                max_completion_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
            return LLMTestResult(
                success=True,
                message=f"Completion succeeded on {endpoint_label} with model '{model}'.",
            )
        except Exception as e:
            return LLMTestResult(
                success=False,
                message=(
                    f"Model '{model}' rejected a test completion: {e}. "
                    "For OpenAI project keys, enable this model under Project Settings → Model Permissions."
                ),
            )

    async def _complete_anthropic(
        self, api_key: str, model: str, endpoint: str | None = None
    ) -> LLMTestResult:
        """Issue a 1-token messages.create against Anthropic."""
        try:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=api_key, base_url=endpoint or None)
            endpoint_label = endpoint or "https://api.anthropic.com"

            await client.messages.create(
                model=model,
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
            return LLMTestResult(
                success=True,
                message=f"Completion succeeded on {endpoint_label} with model '{model}'.",
            )
        except Exception as e:
            return LLMTestResult(
                success=False,
                message=f"Model '{model}' rejected a test completion: {e}.",
            )

    async def _complete_google(
        self,
        api_key: str,
        model: str,
        endpoint: str | None = None,
    ) -> LLMTestResult:
        """Issue a minimal Gemini completion through the shared runtime adapter."""
        try:
            from src.services.llm.base import LLMMessage
            from src.services.llm.factory import create_llm_client

            client = create_llm_client(
                "google",
                api_key,
                model=model,
                endpoint=endpoint,
                max_tokens=1,
            )
            await client.complete([LLMMessage(role="user", content="Reply OK")], max_tokens=1)
            return LLMTestResult(
                success=True,
                message=f"Completion succeeded with Google model '{model}'.",
            )
        except Exception as e:
            logger.error("Google completion verify failed: %s", e)
            return LLMTestResult(
                success=False,
                message=f"Model '{model}' rejected a test completion: {e}.",
            )

    async def list_models(self) -> list[LLMModelInfo] | None:
        """
        List available models from the configured provider.

        Returns:
            List of model info objects, or None if not available
        """
        result = await self.test_connection()
        return result.models if result.success else None

    async def sync_provider_pricing(
        self,
        provider: str,
        models: set[str],
        api_key: str | None,
        endpoint: str,
    ) -> int:
        """Sync every selected model plus previously used models from ``/models``."""

        from src.services.model_pricing import (
            canonical_provider,
            fetch_pricing_catalog,
            is_openrouter_endpoint,
            sync_published_pricing,
        )

        pricing_provider = canonical_provider(provider, endpoint)
        models_url = f"{endpoint.rstrip('/')}/models"
        catalog = await fetch_pricing_catalog(
            models_url,
            api_key=None if is_openrouter_endpoint(endpoint) else api_key,
        )
        return await sync_published_pricing(
            self.session,
            provider=pricing_provider,
            selected_models=models,
            catalog=catalog,
        )
