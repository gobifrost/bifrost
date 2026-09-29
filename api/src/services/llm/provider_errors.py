"""Plain-language descriptions of model-provider request failures."""

from __future__ import annotations

from typing import Any

from src.services.agent_runtime.model_failover import is_connection_error

_STATUS_SUMMARIES = {
    401: "The provider rejected the API key.",
    402: "The provider account is out of credit.",
    403: "This API key is not allowed to use the model.",
    404: "The provider does not recognize this model.",
    429: "The provider is rate limiting this key.",
}
_DETAIL_LIMIT = 200


def describe_provider_error(error: BaseException) -> str:
    """One or two sentences for an administrator, never a raw payload.

    The HTTP status picks the summary; the provider's own message, when its
    error body carries one, follows it.
    """
    status = getattr(error, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        summary = _STATUS_SUMMARIES.get(status) or (
            f"The provider is unavailable (HTTP {status})."
            if status >= 500
            else f"The provider refused the request (HTTP {status})."
        )
        detail = _provider_message(getattr(error, "body", None))
        return f"{summary} The provider said: {detail}" if detail else summary
    if is_connection_error(error):
        return "Bifrost could not reach the provider."
    return "The request failed before the provider answered."


def _provider_message(body: Any) -> str | None:
    """The human-readable message inside a provider's error body, if any.

    Anthropic and OpenAI nest it under ``error.message``; OpenRouter and
    others put ``message`` or ``detail`` at the top level.
    """
    if not isinstance(body, dict):
        return None
    candidates = [body.get("error"), body.get("message"), body.get("detail")]
    nested = body.get("error")
    if isinstance(nested, dict):
        candidates.insert(0, nested.get("message"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            text = " ".join(candidate.split())
            if len(text) > _DETAIL_LIMIT:
                text = text[: _DETAIL_LIMIT - 1].rstrip() + "…"
            return text if text.endswith((".", "!", "?", "…")) else f"{text}."
    return None
