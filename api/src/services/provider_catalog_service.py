"""Credential validation and model discovery for saved provider connections."""

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from src.services.agent_runtime.retry_transport import (
    MAX_ATTEMPTS,
    RETRYABLE_STATUS_CODES,
    ai_retry_context,
    get_ai_retry_http_client,
)
from src.services.model_pricing import is_openrouter_endpoint

logger = logging.getLogger(__name__)


@dataclass
class ProviderModelInfo:
    id: str
    display_name: str
    output_modalities: list[str] | None = None


@dataclass
class ProviderTestResult:
    success: bool
    message: str
    models: list[ProviderModelInfo] | None = None


def _model_output_modalities(model: object) -> list[str] | None:
    architecture = getattr(model, "architecture", None)
    if architecture is None:
        model_extra = getattr(model, "model_extra", None)
        if isinstance(model_extra, dict):
            architecture = model_extra.get("architecture")
    raw = architecture.get("output_modalities") if isinstance(architecture, dict) else getattr(
        architecture, "output_modalities", None
    )
    if not isinstance(raw, (list, tuple)):
        return None
    return [str(modality) for modality in raw]


class ProviderCatalogService:
    async def list_openai(
        self,
        api_key: str,
        endpoint: str | None = None,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> ProviderTestResult:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=api_key,
                base_url=endpoint or None,
                http_client=get_ai_retry_http_client(),
                max_retries=0,
            )
            endpoint_label = endpoint or "https://api.openai.com/v1"
            try:
                with ai_retry_context(
                    provider="openrouter" if is_openrouter_endpoint(endpoint) else "openai",
                    model="catalog",
                    surface="provider_catalog",
                ):
                    response = await client.models.list(extra_headers=extra_headers)
                models = [
                    ProviderModelInfo(m.id, m.id, _model_output_modalities(m))
                    for m in sorted(response.data, key=lambda item: item.id)
                ]
                hostname = (urlparse(endpoint).hostname or "") if endpoint else ""
                if hostname.endswith((".openai.azure.com", ".services.ai.azure.com")):
                    return ProviderTestResult(
                        True,
                        (
                            f"Connected to {endpoint_label}. Microsoft Foundry's model "
                            "catalog does not identify deployment names; enter the "
                            "deployment name manually."
                        ),
                    )
                return ProviderTestResult(True, f"Connected to {endpoint_label}. Listed {len(models)} model(s).", models)
            except Exception as error:
                message = str(error)
                if any(token in message.lower() for token in ("401", "403", "unauthorized", "forbidden", "authentication", "invalid")):
                    return ProviderTestResult(False, f"Authentication failed at {endpoint_label}: {error}")
                return ProviderTestResult(True, f"Connected to {endpoint_label}. Model listing is unavailable; enter a model id manually.")
        except Exception as error:
            return ProviderTestResult(False, f"OpenAI connection failed: {error}")

    async def list_anthropic(self, api_key: str, endpoint: str | None = None) -> ProviderTestResult:
        try:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(
                api_key=api_key,
                base_url=endpoint or None,
                http_client=get_ai_retry_http_client(),
                max_retries=0,
            )
            endpoint_label = endpoint or "https://api.anthropic.com"
            try:
                with ai_retry_context(
                    provider="anthropic",
                    model="catalog",
                    surface="provider_catalog",
                ):
                    response = await client.models.list()
                seen: set[str] = set()
                models: list[ProviderModelInfo] = []
                for item in sorted(response.data, key=lambda model: model.id, reverse=True):
                    display_name = getattr(item, "display_name", item.id)
                    if display_name in seen:
                        continue
                    seen.add(display_name)
                    models.append(ProviderModelInfo(item.id, display_name))
                models.sort(key=lambda item: item.display_name)
                return ProviderTestResult(True, f"Connected to {endpoint_label}. Listed {len(models)} model(s).", models)
            except Exception as error:
                if any(token in str(error).lower() for token in ("401", "403", "unauthorized", "forbidden", "authentication", "invalid")):
                    return ProviderTestResult(False, f"Authentication failed at {endpoint_label}: {error}")
                return ProviderTestResult(True, f"Connected to {endpoint_label}. Model listing is unavailable; enter a model id manually.")
        except Exception as error:
            return ProviderTestResult(False, f"Anthropic connection failed: {error}")

    async def list_google(self, api_key: str, endpoint: str | None = None) -> ProviderTestResult:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    base_url=endpoint,
                    retry_options=types.HttpRetryOptions(
                        attempts=MAX_ATTEMPTS,
                        initial_delay=1.0,
                        max_delay=4.0,
                        exp_base=2.0,
                        jitter=1.0,
                        http_status_codes=sorted(RETRYABLE_STATUS_CODES),
                    ),
                ),
            )
            try:
                with ai_retry_context(
                    provider="google",
                    model="catalog",
                    surface="provider_catalog",
                ):
                    pager = await client.aio.models.list(config={"page_size": 100})
                models = [
                    ProviderModelInfo(
                        (item.name or "").removeprefix("models/"),
                        item.display_name or item.name or "Unknown model",
                    )
                    for item in pager.page
                    if item.name
                ]
            finally:
                await client.aio.aclose()
            return ProviderTestResult(True, f"Connected to Google. Listed {len(models)} model(s).", models)
        except Exception as error:
            logger.error("Google connection test failed: %s", error)
            return ProviderTestResult(False, f"Google connection failed: {error}")
