"""Detect the usable OpenAI-compatible inference transport once per profile."""

from typing import Literal

from openai import APIStatusError, AsyncOpenAI

from src.services.agent_runtime.retry_transport import get_ai_retry_http_client

OpenAITransport = Literal["responses", "chat_completions"]


def _responses_are_unsupported(error: APIStatusError) -> bool:
    if error.status_code in (404, 405, 501):
        return True
    if error.status_code != 400:
        return False
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "model not supported",
            "does not support the responses api",
            "responses api is not supported",
            "unsupported endpoint",
            "unknown endpoint",
        )
    )


async def detect_openai_transport(
    *, api_key: str, endpoint: str | None, model: str
) -> OpenAITransport:
    """Probe Responses first and use Chat only for a definite unsupported error."""

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=endpoint,
        http_client=get_ai_retry_http_client(),
        max_retries=0,
    )
    try:
        await client.responses.create(
            model=model,
            input="Reply with OK.",
            max_output_tokens=64,
            store=False,
        )
        return "responses"
    except APIStatusError as error:
        if not _responses_are_unsupported(error):
            raise ValueError(
                f"Could not verify model '{model}' through the Responses API "
                f"(HTTP {error.status_code}); transport was not changed."
            ) from error

    try:
        await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with OK."}],
            max_completion_tokens=64,
        )
    except APIStatusError as error:
        raise ValueError(
            f"Model '{model}' is unavailable through both Responses and Chat "
            f"Completions (Chat HTTP {error.status_code})."
        ) from error
    return "chat_completions"
