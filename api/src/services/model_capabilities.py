"""Deterministic model capability discovery and fingerprinting."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

import httpx

from src.models.contracts.artifacts import ModelCapabilities
from src.services.llm import LLMInputFile, LLMMessage, ToolDefinition

OPENROUTER_MODELS_URL = (
    "https://openrouter.ai/api/v1/models?output_modalities=all"
)

_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


async def verify_model_capabilities(
    *,
    provider: Literal["openai", "anthropic", "google"],
    model: str,
    endpoint: str | None,
    api_key: str,
) -> tuple[ModelCapabilities, str]:
    """Run bounded provider calls for capabilities catalogs cannot establish."""
    from shared.artifact_generation import generate_document
    from src.models.contracts.artifacts import DocumentArtifactSpec, DocumentSection
    from src.services.llm.factory import create_llm_client

    client = create_llm_client(
        provider,
        api_key,
        model=model,
        endpoint=endpoint,
        max_tokens=16,
    )
    async with asyncio.timeout(45):
        await client.complete(
            [LLMMessage(role="user", content="Reply with OK.")],
            max_tokens=8,
        )

    tool_calling = False
    try:
        async with asyncio.timeout(45):
            response = await client.complete(
                [LLMMessage(role="user", content="Call capability_probe now.")],
                tools=[
                    ToolDefinition(
                        name="capability_probe",
                        description="Required capability conformance probe.",
                        parameters={
                            "type": "object",
                            "properties": {"ok": {"type": "boolean"}},
                            "required": ["ok"],
                            "additionalProperties": False,
                        },
                    )
                ],
                max_tokens=16,
                require_tool_call=True,
            )
        tool_calling = bool(
            response.tool_calls
            and response.tool_calls[0].name == "capability_probe"
        )
    except Exception:
        tool_calling = False

    async def accepts_file(filename: str, media_type: str, data: bytes) -> bool:
        try:
            async with asyncio.timeout(45):
                await client.complete(
                    [
                        LLMMessage(
                            role="user",
                            content="Briefly identify the attached test file.",
                            input_files=[
                                LLMInputFile(
                                    filename=filename,
                                    media_type=media_type,
                                    data=data,
                                )
                            ],
                        )
                    ],
                    max_tokens=8,
                )
            return True
        except Exception:
            return False

    pdf = generate_document(
        DocumentArtifactSpec(
            filename="capability-probe.pdf",
            format="pdf",
            title="Capability Probe",
            sections=[DocumentSection(paragraphs=["Bifrost capability probe."])],
        )
    )
    image_input, pdf_input = await asyncio.gather(
        accepts_file("capability-probe.png", "image/png", _ONE_PIXEL_PNG),
        accepts_file(pdf.filename, pdf.content_type, pdf.content),
    )
    capabilities = ModelCapabilities(
        image_input=image_input,
        pdf_input=pdf_input,
        tool_calling=tool_calling,
        source="verified",
        checked_at=datetime.now(timezone.utc),
        fingerprint=model_fingerprint(
            provider=provider, model=model, endpoint=endpoint
        ),
    )
    return (
        capabilities,
        "Provider conformance check completed.",
    )


def model_fingerprint(
    *, provider: str, model: str, endpoint: str | None
) -> str:
    """Fingerprint the exact configured target so stale checks cannot be reused."""
    payload = {
        "provider": provider.strip().lower(),
        "model": model.strip(),
        "endpoint": (endpoint or "").rstrip("/").lower(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def normalize_capabilities(
    capabilities: ModelCapabilities | None,
    *,
    provider: str,
    model: str,
    endpoint: str | None,
) -> ModelCapabilities:
    """Return persisted capabilities only when they match the selected target."""
    fingerprint = model_fingerprint(provider=provider, model=model, endpoint=endpoint)
    if capabilities is None or capabilities.fingerprint != fingerprint:
        return ModelCapabilities(source="unknown", fingerprint=fingerprint)
    return capabilities


def should_offer_tool_calling(capabilities: ModelCapabilities) -> bool:
    """Decide whether Chat should optimistically offer tools to the model.

    Unknown capability records are deliberately optimistic: when we do not have
    an authoritative answer yet, we should still offer tools and let the model
    try. Explicitly verified or manually asserted unsupported records continue
    to suppress tools.
    """

    return capabilities.tool_calling or capabilities.source == "unknown"


def manual_capabilities(
    *,
    provider: str,
    model: str,
    endpoint: str | None,
    image_input: bool,
    pdf_input: bool,
    tool_calling: bool,
) -> ModelCapabilities:
    """Create an explicitly administrator-asserted, fingerprinted record."""
    return ModelCapabilities(
        image_input=image_input,
        pdf_input=pdf_input,
        tool_calling=tool_calling,
        source="manual",
        checked_at=datetime.now(timezone.utc),
        fingerprint=model_fingerprint(provider=provider, model=model, endpoint=endpoint),
    )


async def lookup_model_capabilities(
    *,
    provider: Literal["openai", "anthropic", "google"],
    model: str,
    endpoint: str | None,
    client: httpx.AsyncClient | None = None,
) -> tuple[ModelCapabilities, str]:
    """Resolve capabilities from OpenRouter's public model catalog when possible.

    Unknown is deliberate: provider model-list endpoints generally do not expose
    input modalities or tool support, and Pydantic model profiles are hints rather
    than a cross-provider conformance guarantee.
    """
    fingerprint = model_fingerprint(provider=provider, model=model, endpoint=endpoint)
    normalized_endpoint = (endpoint or "").lower()
    looks_like_openrouter = "openrouter.ai" in normalized_endpoint
    if not looks_like_openrouter or "/" not in model:
        return (
            ModelCapabilities(source="unknown", fingerprint=fingerprint),
            "No authoritative public capability record matched this endpoint and model. Set the flags manually.",
        )

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=8.0, follow_redirects=True)
    try:
        response = await http.get(OPENROUTER_MODELS_URL)
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        records = body.get("data")
        if not isinstance(records, list):
            raise TypeError("OpenRouter model catalog did not contain a model list.")
        data = next(
            (
                record
                for record in records
                if isinstance(record, dict) and record.get("id") == model
            ),
            None,
        )
        if data is None:
            return (
                ModelCapabilities(source="unknown", fingerprint=fingerprint),
                "OpenRouter did not return a catalog record for this model. Set the flags manually.",
            )
        architecture = data.get("architecture") or {}
        input_modalities = set(architecture.get("input_modalities") or [])
        supported_parameters = set(data.get("supported_parameters") or [])
        capabilities = ModelCapabilities(
            image_input="image" in input_modalities,
            pdf_input="file" in input_modalities,
            tool_calling="tools" in supported_parameters,
            source="openrouter",
            checked_at=datetime.now(timezone.utc),
            fingerprint=fingerprint,
        )
        return capabilities, "Capabilities loaded from OpenRouter's public model catalog."
    except (httpx.HTTPError, ValueError, TypeError):
        return (
            ModelCapabilities(source="unknown", fingerprint=fingerprint),
            "OpenRouter capability lookup was unavailable. Set the flags manually.",
        )
    finally:
        if owns_client:
            await http.aclose()
