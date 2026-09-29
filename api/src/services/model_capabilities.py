"""Chat capability records: fingerprinting, validity, and catalog derivation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from src.models.contracts.artifacts import ModelCapabilities
from src.services.model_catalog import CatalogModel


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


def catalog_capabilities(
    entry: CatalogModel,
    *,
    provider: str,
    model: str,
    endpoint: str | None,
) -> ModelCapabilities:
    """Capabilities the models.dev catalog publishes for this model."""
    return ModelCapabilities(
        image_input="image" in entry.input_modalities,
        pdf_input="pdf" in entry.input_modalities,
        tool_calling=entry.tool_call,
        source="catalog",
        checked_at=datetime.now(timezone.utc),
        fingerprint=model_fingerprint(provider=provider, model=model, endpoint=endpoint),
    )


def should_offer_tool_calling(capabilities: ModelCapabilities) -> bool:
    """Decide whether Chat should optimistically offer tools to the model.

    Unknown capability records are deliberately optimistic: when we do not have
    an authoritative answer yet, we should still offer tools and let the model
    try. Catalog, verified, or manually asserted unsupported records continue
    to suppress tools.
    """

    return capabilities.tool_calling or capabilities.source == "unknown"
