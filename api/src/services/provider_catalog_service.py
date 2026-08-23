"""Credential validation and model discovery for saved provider connections."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from src.services.model_capabilities import OPENROUTER_MODELS_URL

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


def _is_openrouter_endpoint(endpoint: str | None) -> bool:
    hostname = urlparse(endpoint or "").hostname
    return hostname == "openrouter.ai" or bool(hostname and hostname.endswith(".openrouter.ai"))


async def list_openrouter_models(
    api_key: str,
    client: httpx.AsyncClient | None = None,
) -> list[ProviderModelInfo]:
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=10.0, follow_redirects=True)
    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        response, images, videos = await asyncio.gather(
            http.get(OPENROUTER_MODELS_URL, headers=headers),
            http.get("https://openrouter.ai/api/v1/images/models", headers=headers),
            http.get("https://openrouter.ai/api/v1/videos/models", headers=headers),
        )
        for item in (response, images, videos):
            item.raise_for_status()
        payload: dict[str, Any] = response.json()
        records = payload.get("data")
        if not isinstance(records, list):
            raise TypeError("OpenRouter model catalog did not contain a model list.")

        def media_catalog(media_response: httpx.Response) -> dict[str, str]:
            media_records = media_response.json().get("data")
            if not isinstance(media_records, list):
                raise TypeError("OpenRouter media catalog did not contain a model list.")
            return {
                record["id"]: str(record.get("name") or record["id"])
                for record in media_records
                if isinstance(record, dict) and isinstance(record.get("id"), str)
            }

        image_models = media_catalog(images)
        video_models = media_catalog(videos)
        models: dict[str, ProviderModelInfo] = {}
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                continue
            architecture = record.get("architecture")
            raw = architecture.get("output_modalities") if isinstance(architecture, dict) else None
            modalities = [str(value) for value in raw] if isinstance(raw, list) else []
            if record["id"] in image_models and "image" not in modalities:
                modalities.append("image")
            if record["id"] in video_models and "video" not in modalities:
                modalities.append("video")
            models[record["id"]] = ProviderModelInfo(
                id=record["id"],
                display_name=str(record.get("name") or record["id"]),
                output_modalities=modalities,
            )
        for model_id, display_name in image_models.items():
            models.setdefault(model_id, ProviderModelInfo(model_id, display_name, ["image"]))
        for model_id, display_name in video_models.items():
            existing = models.get(model_id)
            if existing is None:
                models[model_id] = ProviderModelInfo(model_id, display_name, ["video"])
            elif existing.output_modalities is not None and "video" not in existing.output_modalities:
                existing.output_modalities.append("video")
        return sorted(models.values(), key=lambda item: item.display_name.casefold())
    finally:
        if owns_client:
            await http.aclose()


class ProviderCatalogService:
    async def list_openai(self, api_key: str, endpoint: str | None = None) -> ProviderTestResult:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key, base_url=endpoint or None)
            endpoint_label = endpoint or "https://api.openai.com/v1"
            try:
                response = await client.models.list()
                models = [
                    ProviderModelInfo(m.id, m.id, _model_output_modalities(m))
                    for m in sorted(response.data, key=lambda item: item.id)
                ]
                if _is_openrouter_endpoint(endpoint):
                    try:
                        models = await list_openrouter_models(api_key)
                    except (httpx.HTTPError, TypeError, ValueError) as error:
                        logger.info("OpenRouter all-modality catalog unavailable: %s", error)
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

            client = AsyncAnthropic(api_key=api_key, base_url=endpoint or None)
            endpoint_label = endpoint or "https://api.anthropic.com"
            try:
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
                http_options=types.HttpOptions(base_url=endpoint) if endpoint else None,
            )
            try:
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
