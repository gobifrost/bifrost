"""Lightweight provider identity helpers shared with isolated runtimes."""

from urllib.parse import urlparse

OPENROUTER_PROVIDER = "openrouter"


def is_openrouter_endpoint(endpoint: str | None) -> bool:
    """Return whether an OpenAI-compatible endpoint is OpenRouter."""

    if not endpoint:
        return False
    hostname = urlparse(endpoint).hostname
    return hostname == "openrouter.ai" or bool(
        hostname and hostname.endswith(".openrouter.ai")
    )


def canonical_provider(provider: str, endpoint: str | None = None) -> str:
    """Resolve the billing provider without changing Bifrost's public config."""

    normalized = provider.strip().lower()
    if normalized == "custom":
        normalized = "openai"
    if normalized == OPENROUTER_PROVIDER or is_openrouter_endpoint(endpoint):
        return OPENROUTER_PROVIDER
    return normalized


__all__ = [
    "OPENROUTER_PROVIDER",
    "canonical_provider",
    "is_openrouter_endpoint",
]
