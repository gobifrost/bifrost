"""Probe the usable OpenCode Go wire surface once per model profile.

Known models never reach this module: their surface comes from the documented
prefix table in :mod:`src.services.opencode_go`. Only models newer than that
table are probed, and the answer is persisted on the profile so the gateway
sees at most three tiny probe calls per unknown model, ever.
"""

from uuid import uuid4

from anthropic import APIStatusError as AnthropicAPIStatusError
from anthropic import AsyncAnthropic
from openai import APIStatusError, AsyncOpenAI

from src.services.agent_runtime.retry_transport import get_ai_retry_http_client
from src.services.opencode_go import (
    OpenCodeGoWireApi,
    opencode_go_anthropic_endpoint,
    opencode_go_extra_headers,
)

_PROBE_PROMPT = "Reply with OK."
_PROBE_MAX_TOKENS = 16

# Markers for "this surface does not serve this model" (mirrors
# openai_transport_detection._responses_are_unsupported).
_UNSUPPORTED_MARKERS = (
    "model not supported",
    "model is unavailable",
    "does not support",
    "is not supported",
    "unsupported endpoint",
    "unknown endpoint",
    "no endpoints found",
)


def _is_unsupported(error: APIStatusError | AnthropicAPIStatusError) -> bool:
    if getattr(error, "status_code", None) in (404, 405, 501):
        return True
    if getattr(error, "status_code", None) != 400:
        return False
    return any(marker in str(error).casefold() for marker in _UNSUPPORTED_MARKERS)


def _is_auth_error(error: APIStatusError | AnthropicAPIStatusError) -> bool:
    return getattr(error, "status_code", None) in (401, 403)


async def detect_opencode_go_wire_api(
    *, api_key: str, endpoint: str | None, model: str
) -> OpenCodeGoWireApi:
    """Probe Chat Completions, then Responses, then Messages for one model.

    Every probe carries the identity headers Go requires: without
    ``x-opencode-session`` the gateway hard-fails with ``MissingSessionID``,
    so a session-less probe could never distinguish "wrong surface" from
    "missing header".
    """

    # One-shot value: this probe is a single round trip, not a conversation.
    headers = opencode_go_extra_headers(f"probe-{uuid4().hex[:16]}")

    openai_client = AsyncOpenAI(
        api_key=api_key,
        base_url=endpoint,
        http_client=get_ai_retry_http_client(),
        max_retries=0,
    )
    try:
        await openai_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": _PROBE_PROMPT}],
            max_completion_tokens=_PROBE_MAX_TOKENS,
            extra_headers=headers,
        )
        return "chat_completions"
    except APIStatusError as error:
        if _is_auth_error(error):
            raise ValueError(
                f"OpenCode Go rejected the API key (HTTP {error.status_code}); "
                "wire surface was not probed."
            ) from error
        if not _is_unsupported(error):
            raise ValueError(
                f"Could not probe model '{model}' through Chat Completions "
                f"(HTTP {error.status_code}); wire surface was not changed."
            ) from error

    try:
        await openai_client.responses.create(
            model=model,
            input=_PROBE_PROMPT,
            max_output_tokens=_PROBE_MAX_TOKENS,
            store=False,
            extra_headers=headers,
        )
        return "responses"
    except APIStatusError as error:
        if _is_auth_error(error):
            raise ValueError(
                f"OpenCode Go rejected the API key (HTTP {error.status_code}); "
                "wire surface was not probed."
            ) from error
        if not _is_unsupported(error):
            raise ValueError(
                f"Could not probe model '{model}' through the Responses API "
                f"(HTTP {error.status_code}); wire surface was not changed."
            ) from error

    anthropic_client = AsyncAnthropic(
        api_key=api_key,
        base_url=opencode_go_anthropic_endpoint(endpoint),
        http_client=get_ai_retry_http_client(),
        max_retries=0,
    )
    try:
        await anthropic_client.messages.create(
            model=model,
            max_tokens=_PROBE_MAX_TOKENS,
            messages=[{"role": "user", "content": _PROBE_PROMPT}],
            extra_headers=headers,
        )
        return "messages"
    except AnthropicAPIStatusError as error:
        raise ValueError(
            f"Model '{model}' is unavailable through Chat Completions, "
            f"Responses, and Messages (Messages HTTP {error.status_code})."
        ) from error
