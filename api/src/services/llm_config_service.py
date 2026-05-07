"""
LLM Configuration Service

Manages LLM provider configuration in system_configs table.
Follows the same pattern as GitHubConfigService for SystemConfig storage.
"""

import base64
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.log_safety import log_safe
from src.models.orm import SystemConfig
from src.models.orm.ai_usage import AIModelPricing

logger = logging.getLogger(__name__)

# SystemConfig keys (same as factory.py)
LLM_CONFIG_CATEGORY = "llm"
LLM_CONFIG_KEY = "provider_config"


@dataclass
class LLMProviderConfig:
    """LLM provider configuration (API key masked for responses)."""

    provider: Literal["openai", "anthropic"]
    model: str
    endpoint: str | None = None  # For custom OpenAI-compatible providers
    max_tokens: int = 16384
    default_system_prompt: str | None = None  # Default system prompt for agentless chat
    summarization_model: str | None = None  # Override for post-run summarization
    tuning_model: str | None = None  # Override for tuning chat + dry-run
    is_configured: bool = False
    api_key_set: bool = False  # Indicates if API key is configured (never return actual key)


@dataclass
class LLMModelInfo:
    """Model information with both ID and display name."""

    id: str
    display_name: str


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

        return LLMProviderConfig(
            provider=provider,
            model=config_data.get("model", ""),
            endpoint=config_data.get("endpoint"),
            max_tokens=config_data.get("max_tokens", 16384),
            default_system_prompt=config_data.get("default_system_prompt"),
            summarization_model=config_data.get("summarization_model"),
            tuning_model=config_data.get("tuning_model"),
            is_configured=True,
            api_key_set=bool(config_data.get("encrypted_api_key")),
        )

    async def save_config(
        self,
        provider: Literal["openai", "anthropic"],
        model: str,
        api_key: str | None = None,
        endpoint: str | None = None,
        max_tokens: int = 16384,
        default_system_prompt: str | None = None,
        summarization_model: str | None = None,
        tuning_model: str | None = None,
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

        config_data = {
            "provider": provider,
            "model": model,
            "encrypted_api_key": encrypted_api_key,
            "endpoint": endpoint,
            "max_tokens": max_tokens,
            "default_system_prompt": default_system_prompt,
            "summarization_model": summarization_model,
            "tuning_model": tuning_model,
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

    async def test_credentials(
        self,
        provider: Literal["openai", "anthropic"],
        api_key: str | None = None,
        endpoint: str | None = None,
    ) -> LLMTestResult:
        """Validate explicit credentials by listing provider models."""
        try:
            resolved_api_key = api_key or await self._get_saved_api_key()

            if provider == "openai":
                return await self._list_openai(resolved_api_key, endpoint)
            elif provider == "anthropic":
                return await self._list_anthropic(resolved_api_key, endpoint)
            else:
                return LLMTestResult(
                    success=False,
                    message=f"Unknown provider: {provider}",
                )
        except ValueError as e:
            return LLMTestResult(success=False, message=str(e))
        except Exception as e:
            logger.error(f"LLM credential test failed: {e}")
            return LLMTestResult(success=False, message=f"Connection test failed: {e}")

    async def _get_saved_api_key(self) -> str:
        """Decrypt the saved LLM API key for unsaved form connection tests."""
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == LLM_CONFIG_CATEGORY,
                SystemConfig.key == LLM_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
        config = result.scalars().first()
        if not config or not config.value_json:
            raise ValueError("API key is required for connection test")

        encrypted_api_key = config.value_json.get("encrypted_api_key")
        if not encrypted_api_key:
            raise ValueError("API key is required for connection test")

        fernet = self._get_fernet()
        return fernet.decrypt(encrypted_api_key.encode()).decode()

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
                    LLMModelInfo(id=m.id, display_name=m.id)
                    for m in sorted(models_response.data, key=lambda x: x.id)
                ]
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
        model: str,
        api_key: str,
        endpoint: str,
    ) -> int:
        """
        Fetch pricing from a provider's /models endpoint and update AIModelPricing
        for the selected model and any existing pricing rows.

        Only creates a new pricing row for the selected model. Updates existing rows
        that match models returned by the provider.

        Returns:
            Number of models with pricing synced.
        """
        import httpx

        models_url = f"{endpoint.rstrip('/')}/models"
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(
                models_url,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            body = resp.json()

        # Build a lookup of model_id -> (input_per_million, output_per_million)
        provider_pricing: dict[str, tuple[Decimal, Decimal]] = {}
        quantize_to = Decimal("0.0001")
        max_price = Decimal("999999.9999")

        for item in body.get("data", []):
            model_id = item.get("id", "")
            pricing = item.get("pricing")
            if not model_id or not pricing:
                continue

            prompt_price = pricing.get("prompt")
            completion_price = pricing.get("completion")
            if not prompt_price or not completion_price:
                continue

            try:
                input_pm = (Decimal(prompt_price) * 1_000_000).quantize(quantize_to)
                output_pm = (Decimal(completion_price) * 1_000_000).quantize(quantize_to)
            except Exception:
                continue

            if input_pm > max_price or output_pm > max_price:
                continue

            provider_pricing[model_id] = (input_pm, output_pm)

        if not provider_pricing:
            return 0

        synced = 0
        today = date.today()

        # Update existing pricing rows that have a match in the provider data
        existing_result = await self.session.execute(
            select(AIModelPricing).where(AIModelPricing.provider == provider)
        )
        priced_models: set[str] = set()
        for row in existing_result.scalars().all():
            priced_models.add(row.model)
            if row.model in provider_pricing:
                input_pm, output_pm = provider_pricing[row.model]
                row.input_price_per_million = input_pm
                row.output_price_per_million = output_pm
                row.updated_at = datetime.now(timezone.utc)
                synced += 1

        # Find models that have been used but don't have pricing yet
        from src.models.orm.ai_usage import AIUsage

        used_result = await self.session.execute(
            select(AIUsage.model).where(AIUsage.provider == provider).distinct()
        )
        used_models = {row[0] for row in used_result.all()}

        # Create pricing for: selected model + used models without pricing
        models_to_add = ({model} | used_models) - priced_models
        for model_id in models_to_add:
            if model_id in provider_pricing:
                input_pm, output_pm = provider_pricing[model_id]
                self.session.add(
                    AIModelPricing(
                        provider=provider,
                        model=model_id,
                        input_price_per_million=input_pm,
                        output_price_per_million=output_pm,
                        effective_date=today,
                    )
                )
                synced += 1

        await self.session.flush()
        return synced
