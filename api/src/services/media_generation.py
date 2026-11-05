"""Provider-backed image and video generation for trusted artifacts."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
from uuid import uuid4
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from shared.ai_http_retry import get_ai_provider_read
from shared.artifact_generation import (
    GeneratedArtifact,
    safe_artifact_filename,
    validate_artifact_content,
)
from src.services.llm.factory import get_llm_config
from src.services.model_pricing import is_openrouter_endpoint

MediaKind = Literal["image", "video"]
ProgressCallback = Callable[[str, float | None], Awaitable[None]]


class MediaGenerationError(ValueError):
    """Raised when media generation is unavailable or fails safely."""


@dataclass(frozen=True)
class MediaProviderConfig:
    provider: str
    endpoint: str
    api_key: str
    model: str
    is_openrouter: bool


def _base_endpoint(provider: str, endpoint: str | None) -> str:
    if endpoint:
        return endpoint.rstrip("/")
    if provider == "openai":
        return "https://api.openai.com/v1"
    raise MediaGenerationError(
        "This provider requires an explicit media-generation API endpoint."
    )


async def get_media_provider_config(
    db: AsyncSession,
    kind: MediaKind,
) -> MediaProviderConfig:
    """Resolve the configured provider key and dedicated media model."""
    assignment_key = "image_generation" if kind == "image" else "video_generation"
    try:
        llm_config = await get_llm_config(db, assignment_key=assignment_key)
    except ValueError as exc:
        label = "Image" if kind == "image" else "Video"
        raise MediaGenerationError(
            f"{label} generation is not configured in System Settings."
        ) from exc
    endpoint = _base_endpoint(llm_config.provider, llm_config.endpoint)
    openrouter = is_openrouter_endpoint(endpoint)
    if kind == "video" and not openrouter:
        raise MediaGenerationError(
            "Video generation currently requires an OpenRouter API endpoint."
        )
    if kind == "image" and llm_config.provider != "openai" and not openrouter:
        raise MediaGenerationError(
            "Image generation currently requires OpenAI or an OpenRouter API endpoint."
        )
    return MediaProviderConfig(
        provider=llm_config.provider,
        endpoint=endpoint,
        api_key=llm_config.api_key,
        model=llm_config.model,
        is_openrouter=openrouter,
    )


def _headers(config: MediaProviderConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }


class MediaProviderError(MediaGenerationError):
    """Stable provider failure identity, also retained in persisted error text."""

    def __init__(
        self, *, provider: str, model: str, surface: str,
        status_code: int | None, root_error_type: str,
        operation_ref: str, outcome_unknown: bool,
    ) -> None:
        self.provider = provider
        self.model = model
        self.surface = surface
        self.status_code = status_code
        self.root_error_type = root_error_type
        self.operation_ref = operation_ref
        self.outcome_unknown = outcome_unknown
        status = f"HTTP {status_code}" if status_code is not None else root_error_type
        label = surface.replace("_", " ")
        message = f"{provider} {label} failed ({status}; {root_error_type}; operation {operation_ref})."
        if status_code == 429:
            message += " The provider is rate limiting requests."
        if outcome_unknown:
            message += " Submission may have been accepted. It was not retried; check provider activity before submitting again."
        elif surface.endswith("submission"):
            message += " Submission was not retried because duplicate prevention is not guaranteed."
        else:
            message += " The existing operation was not resubmitted."
        super().__init__(message)


async def _request_media(
    http: httpx.AsyncClient, config: MediaProviderConfig, url: str, *,
    surface: str, operation_ref: str, submission: bool = False,
    payload: dict[str, Any] | None = None,
) -> httpx.Response:
    provider = "openrouter" if config.is_openrouter else config.provider
    try:
        if submission:
            # No documented idempotency contract for these media endpoints.
            # Disable 307/308 redirects too: they would replay a paid POST.
            response = await http.post(url, headers=_headers(config), json=payload, follow_redirects=False)
        else:
            response = await get_ai_provider_read(
                http, url, headers=_headers(config), provider=provider,
                model=config.model, surface=surface, operation_ref=operation_ref,
            )
        response.raise_for_status()
        return response
    except httpx.HTTPError as exc:
        response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
        status = response.status_code if response is not None else None
        error = MediaProviderError(
            provider=provider, model=config.model, surface=surface, status_code=status,
            root_error_type=type(exc).__name__, operation_ref=operation_ref,
            outcome_unknown=submission and (status is None or status >= 500 or status in {408, 429}),
        )
        logging.getLogger(__name__).warning(
            "ai_media_request_terminal",
            extra={"provider": provider, "model": config.model, "surface": surface,
                   "attempt": 1 if submission else None, "sleep_seconds": 0 if submission else None,
                   "status_code": status, "error_type": error.root_error_type,
                   "operation_ref": operation_ref, "outcome_unknown": error.outcome_unknown},
        )
        raise error from exc


def _image_content_type(item: dict[str, Any]) -> tuple[str, str]:
    content_type = str(item.get("media_type") or "image/png").lower()
    extensions = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
    }
    extension = extensions.get(content_type)
    if extension is None:
        raise MediaGenerationError(
            f"The provider returned unsupported image type {content_type}."
        )
    return content_type, extension


def _usage(payload: dict[str, Any]) -> tuple[int, int, Decimal | None]:
    raw_usage = payload.get("usage")
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(
        usage.get("output_tokens") or usage.get("completion_tokens") or 0
    )
    raw_cost = usage.get("cost")
    try:
        provider_cost = Decimal(str(raw_cost)) if raw_cost is not None else None
    except (InvalidOperation, ValueError):
        provider_cost = None
    return input_tokens, output_tokens, provider_cost


async def generate_image_with_config(
    config: MediaProviderConfig,
    *,
    filename: str,
    prompt: str,
    client: httpx.AsyncClient | None = None,
) -> GeneratedArtifact:
    """Generate one raster image with a previously resolved provider config.

    Runs the provider HTTP call without touching the database so callers
    can resolve ``get_media_provider_config`` in a short transaction,
    release its connection, then call this with no DB session held.
    """
    path = "/images" if config.is_openrouter else "/images/generations"
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=180.0, follow_redirects=True)
    try:
        response = await _request_media(
            http, config, f"{config.endpoint}{path}", surface="image_submission",
            operation_ref=uuid4().hex, submission=True,
            payload={"model": config.model, "prompt": prompt, "n": 1},
        )
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        item = data[0] if isinstance(data, list) and data else None
        if not isinstance(item, dict) or not isinstance(item.get("b64_json"), str):
            raise MediaGenerationError(
                "The media provider did not return base64 image content."
            )
        try:
            content = base64.b64decode(item["b64_json"], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise MediaGenerationError(
                "The media provider returned invalid image content."
            ) from exc
        content_type, extension = _image_content_type(item)
        input_tokens, output_tokens, provider_cost = _usage(payload)
        artifact = GeneratedArtifact(
            filename=safe_artifact_filename(filename, extension),
            content_type=content_type,
            content=content,
            provider="openrouter" if config.is_openrouter else config.provider,
            model=config.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider_cost=provider_cost,
        )
        validate_artifact_content(
            filename=artifact.filename,
            content_type=artifact.content_type,
            content=artifact.content,
        )
        return artifact
    finally:
        if owns_client:
            await http.aclose()


async def generate_image(
    db: AsyncSession,
    *,
    filename: str,
    prompt: str,
    client: httpx.AsyncClient | None = None,
) -> GeneratedArtifact:
    """Generate one raster image with the configured dedicated image model."""
    config = await get_media_provider_config(db, "image")
    return await generate_image_with_config(
        config, filename=filename, prompt=prompt, client=client
    )


def _same_provider_url(base: str, value: str) -> str:
    resolved = urljoin(f"{base.rstrip('/')}/", value)
    base_url = urlparse(base)
    resolved_url = urlparse(resolved)
    if resolved_url.scheme != "https" or resolved_url.hostname != base_url.hostname:
        raise MediaGenerationError("The provider returned an unsafe video status URL.")
    return resolved


async def generate_video(
    db: AsyncSession,
    *,
    filename: str,
    prompt: str,
    report: ProgressCallback,
    client: httpx.AsyncClient | None = None,
) -> GeneratedArtifact:
    """Submit, observe, and download one OpenRouter video generation."""
    config = await get_media_provider_config(db, "video")
    return await generate_video_with_config(
        config,
        filename=filename,
        prompt=prompt,
        report=report,
        client=client,
    )


async def generate_video_with_config(
    config: MediaProviderConfig,
    *,
    filename: str,
    prompt: str,
    report: ProgressCallback,
    client: httpx.AsyncClient | None = None,
) -> GeneratedArtifact:
    """Run video generation without holding a database connection while polling."""
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=180.0, follow_redirects=True)
    try:
        await report("Submitting video generation", 5)
        response = await _request_media(
            http, config, f"{config.endpoint}/videos", surface="video_submission",
            operation_ref=uuid4().hex, submission=True,
            payload={"model": config.model, "prompt": prompt},
        )
        payload = response.json()
        provider_job_id = payload.get("id") if isinstance(payload, dict) else None
        polling_url = payload.get("polling_url") if isinstance(payload, dict) else None
        if not isinstance(provider_job_id, str) or not provider_job_id:
            raise MediaGenerationError("The provider returned no video job ID. Submission may have been accepted; check provider activity before submitting again.")
        status_url = _same_provider_url(
            config.endpoint,
            polling_url
            if isinstance(polling_url, str) and polling_url
            else f"videos/{provider_job_id}",
        )

        operation_ref = hashlib.sha256(provider_job_id.encode()).hexdigest()[:24]
        attempts = 0
        while True:
            attempts += 1
            await report("Generating video", min(90, 10 + attempts))
            status_response = await _request_media(
                http, config, status_url, surface="video_status", operation_ref=operation_ref,
            )
            status_payload = status_response.json()
            provider_status = str(status_payload.get("status") or "").lower()
            if provider_status == "completed":
                break
            if provider_status in {"failed", "cancelled", "canceled", "expired"}:
                error = status_payload.get("error")
                if isinstance(error, dict):
                    error = error.get("message")
                raise MediaGenerationError(
                    str(error)[:500]
                    if error
                    else f"Video generation ended with status {provider_status}."
                )
            if provider_status not in {
                "pending",
                "queued",
                "in_progress",
                "processing",
                "running",
            }:
                raise MediaGenerationError(
                    f"The media provider returned unknown video status {provider_status or 'empty'}."
                )
            await asyncio.sleep(2)

        await report("Downloading generated video", 95)
        content_url = _same_provider_url(
            config.endpoint,
            f"videos/{provider_job_id}/content?index=0",
        )
        content_response = await _request_media(
            http, config, content_url, surface="video_content", operation_ref=operation_ref,
        )
        content_type = content_response.headers.get("content-type", "video/mp4")
        content_type = content_type.split(";", 1)[0].strip().lower()
        extension = {"video/mp4": "mp4", "video/webm": "webm"}.get(content_type)
        if extension is None:
            raise MediaGenerationError(
                f"The provider returned unsupported video type {content_type}."
            )
        input_tokens, output_tokens, provider_cost = _usage(status_payload)
        artifact = GeneratedArtifact(
            filename=safe_artifact_filename(filename, extension),
            content_type=content_type,
            content=content_response.content,
            provider="openrouter" if config.is_openrouter else config.provider,
            model=config.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider_cost=provider_cost,
        )
        validate_artifact_content(
            filename=artifact.filename,
            content_type=artifact.content_type,
            content=artifact.content,
        )
        await report("Video ready", 100)
        return artifact
    finally:
        if owns_client:
            await http.aclose()


async def record_media_usage(
    db: AsyncSession,
    artifact: GeneratedArtifact,
    *,
    conversation_id: Any | None = None,
    message_id: Any | None = None,
    agent_run_id: Any | None = None,
    execution_id: Any | None = None,
    organization_id: Any | None = None,
    user_id: Any | None = None,
) -> None:
    """Record provider-reported media usage in the canonical AI ledger."""
    if (
        artifact.provider is None
        or artifact.model is None
        or not any((conversation_id, agent_run_id, execution_id))
    ):
        return
    from src.core.cache import get_shared_redis
    from src.services.ai_usage_service import record_ai_usage

    await record_ai_usage(
        session=db,
        redis_client=await get_shared_redis(),
        provider=artifact.provider,
        model=artifact.model,
        input_tokens=artifact.input_tokens,
        output_tokens=artifact.output_tokens,
        provider_cost=artifact.provider_cost,
        conversation_id=conversation_id,
        message_id=message_id,
        agent_run_id=agent_run_id,
        execution_id=execution_id,
        organization_id=organization_id,
        user_id=user_id,
    )
